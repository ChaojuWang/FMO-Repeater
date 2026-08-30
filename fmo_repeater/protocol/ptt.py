"""FMO 协议第 8 章：单信道 PTT 路由仲裁。"""

from __future__ import annotations

import threading
from dataclasses import dataclass
from enum import Enum
from typing import Callable, Optional, Tuple, TypeVar


ROUTE_WINDOW_S = 1.5
PREEMPT_EARLIER_MAX_MS = 2000
T = TypeVar("T")


class RouteAction(str, Enum):
    """网络包相对当前路由的裁决结果。"""

    ACQUIRED = "acquired"
    CONTINUED = "continued"
    REPLACED = "replaced"
    PREEMPTED = "preempted"
    REJECTED = "rejected"
    DURATION_LIMIT = "duration_limit"


@dataclass(frozen=True)
class RouteDecision:
    action: RouteAction
    accepted: bool
    generation: Optional[int] = None
    previous_origin: Optional[str] = None
    previous_uid: Optional[int] = None
    reason: str = ""


@dataclass(frozen=True)
class RouteSnapshot:
    origin: str
    uid: int
    stream_begin_utc: int
    started_at: float
    last_activity: float
    generation: int


@dataclass
class _Route:
    origin: str
    uid: int
    stream_begin_utc: int
    started_at: float
    last_activity: float
    generation: int


def _signed_u32(value: int) -> int:
    value &= 0xFFFFFFFF
    return value - 0x100000000 if value & 0x80000000 else value


def stream_begin_delta_ms(new: int, current: int) -> int:
    """返回 new-current 的 uint32 回绕安全有符号毫秒差。"""
    return _signed_u32((new - current) & 0xFFFFFFFF)


class ChannelLease:
    """Echo 对本地路由的可撤销租约。"""

    def __init__(self, coordinator: "ChannelCoordinator", generation: int):
        self._coordinator = coordinator
        self.generation = generation

    def refresh(self, now: float) -> bool:
        return self._coordinator.refresh_echo(self.generation, now)

    def cancel(self) -> None:
        self._coordinator.cancel_echo(self.generation)

    def publish(self, now: float, publisher: Callable[[], T]) -> Tuple[bool, Optional[T]]:
        """原子验证租约并把一个 Echo 包提交给 MQTT。"""
        return self._coordinator.publish_echo(self.generation, now, publisher)


class ChannelCoordinator:
    """一个 MQTT 语音主题对应的半双工信道状态机。"""

    def __init__(
        self,
        max_uplink_duration: float = 60.0,
        route_window: float = ROUTE_WINDOW_S,
    ):
        self.max_uplink_duration = float(max_uplink_duration)
        self.route_window = float(route_window)
        self._lock = threading.Lock()
        self._route: Optional[_Route] = None
        self._generation = 0
        self._blocked_uid: Optional[int] = None
        self._blocked_last_seen: Optional[float] = None

    def _next_generation(self) -> int:
        self._generation += 1
        return self._generation

    def _new_route(
        self, origin: str, uid: int, stream_begin_utc: int, now: float
    ) -> _Route:
        return _Route(
            origin=origin,
            uid=uid,
            stream_begin_utc=stream_begin_utc & 0xFFFFFFFF,
            started_at=now,
            last_activity=now,
            generation=self._next_generation(),
        )

    def _blocked(self, uid: int, now: float) -> bool:
        if self._blocked_uid != uid:
            return False
        assert self._blocked_last_seen is not None
        if now - self._blocked_last_seen >= self.route_window:
            self._blocked_uid = None
            self._blocked_last_seen = None
            return False
        self._blocked_last_seen = now
        return True

    def accept_network_packet(
        self, uid: int, stream_begin_utc: int, now: float
    ) -> RouteDecision:
        """按协议 §8.4/§8.5 裁决一条网络语音包。"""
        with self._lock:
            if self._blocked(uid, now):
                return RouteDecision(
                    RouteAction.REJECTED, False, reason="uplink_uid_blocked"
                )

            current = self._route
            if current is None:
                route = self._new_route("network", uid, stream_begin_utc, now)
                self._route = route
                return RouteDecision(
                    RouteAction.ACQUIRED, True, generation=route.generation
                )

            if current.origin == "network" and current.uid == uid:
                if (
                    current.origin == "network"
                    and self.max_uplink_duration > 0
                    and now - current.started_at >= self.max_uplink_duration
                ):
                    self._blocked_uid = uid
                    self._blocked_last_seen = now
                    self._route = None
                    return RouteDecision(
                        RouteAction.DURATION_LIMIT,
                        False,
                        previous_origin=current.origin,
                        previous_uid=current.uid,
                        reason="max_uplink_duration",
                    )
                current.last_activity = now
                return RouteDecision(
                    RouteAction.CONTINUED,
                    True,
                    generation=current.generation,
                )

            elapsed = now - current.last_activity
            if elapsed >= self.route_window:
                route = self._new_route("network", uid, stream_begin_utc, now)
                self._route = route
                return RouteDecision(
                    RouteAction.REPLACED,
                    True,
                    generation=route.generation,
                    previous_origin=current.origin,
                    previous_uid=current.uid,
                    reason="route_window_expired",
                )

            delta = stream_begin_delta_ms(
                stream_begin_utc, current.stream_begin_utc
            )
            earlier_wins = -PREEMPT_EARLIER_MAX_MS <= delta < 0
            uid_tie_wins = delta == 0 and uid < current.uid
            if earlier_wins or uid_tie_wins:
                route = self._new_route("network", uid, stream_begin_utc, now)
                self._route = route
                return RouteDecision(
                    RouteAction.PREEMPTED,
                    True,
                    generation=route.generation,
                    previous_origin=current.origin,
                    previous_uid=current.uid,
                    reason="earlier_stream" if earlier_wins else "smaller_uid",
                )

            return RouteDecision(
                RouteAction.REJECTED,
                False,
                generation=current.generation,
                previous_origin=current.origin,
                previous_uid=current.uid,
                reason="lower_priority",
            )

    def try_start_echo(
        self, uid: int, stream_begin_utc: int, now: float
    ) -> Optional[ChannelLease]:
        """信道空闲时为 Echo 建立本地路由；忙时返回 None。"""
        with self._lock:
            current = self._route
            if current is not None and now - current.last_activity < self.route_window:
                return None
            route = self._new_route("echo", uid, stream_begin_utc, now)
            self._route = route
            return ChannelLease(self, route.generation)

    def refresh_echo(self, generation: int, now: float) -> bool:
        with self._lock:
            route = self._route
            if (
                route is None
                or route.origin != "echo"
                or route.generation != generation
            ):
                return False
            route.last_activity = now
            return True

    def publish_echo(
        self, generation: int, now: float, publisher: Callable[[], T]
    ) -> Tuple[bool, Optional[T]]:
        """在同一临界区内验证/刷新 Echo 路由并提交一个包。"""
        with self._lock:
            route = self._route
            if (
                route is None
                or route.origin != "echo"
                or route.generation != generation
            ):
                return False, None
            route.last_activity = now
            return True, publisher()

    def cancel_echo(self, generation: int) -> None:
        with self._lock:
            route = self._route
            if (
                route is not None
                and route.origin == "echo"
                and route.generation == generation
            ):
                self._route = None

    def finish_network(self, generation: int) -> None:
        """2s 空闲封口后清除仍指向该网络流的过期路由。"""
        with self._lock:
            route = self._route
            if (
                route is not None
                and route.origin == "network"
                and route.generation == generation
            ):
                self._route = None

    def snapshot(self) -> Optional[RouteSnapshot]:
        with self._lock:
            route = self._route
            if route is None:
                return None
            return RouteSnapshot(
                origin=route.origin,
                uid=route.uid,
                stream_begin_utc=route.stream_begin_utc,
                started_at=route.started_at,
                last_activity=route.last_activity,
                generation=route.generation,
            )
