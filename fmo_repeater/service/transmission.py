"""单信道 PTT 完成事件、生产者与进程内广播。"""

from __future__ import annotations

import logging
import queue
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, List, Optional, Protocol, Tuple

from ..protocol import (
    ChannelCoordinator,
    MessageHeader,
    PacketParser,
    ProtocolError,
    RouteAction,
)
from .event_log import EventLog


@dataclass(frozen=True)
class TimedPacket:
    payload: bytes
    offset_s: float


@dataclass(frozen=True)
class TransmissionCompleted:
    vendor: int
    uid: int
    callsign: str
    stream_begin_utc: int
    first_received_at: float
    last_received_at: float
    packets: Tuple[TimedPacket, ...]
    reason: str

    @property
    def duration_s(self) -> float:
        return self.last_received_at - self.first_received_at


class TransmissionConsumer(Protocol):
    def handle(self, transmission: TransmissionCompleted) -> None: ...


_STOP = object()


@dataclass
class _Subscription:
    name: str
    handler: Callable[[TransmissionCompleted], None]
    inbox: queue.Queue = field(default_factory=queue.Queue)
    thread: Optional[threading.Thread] = None


class TransmissionEventBus:
    """每个订阅者独立 FIFO 线程的进程内广播。"""

    def __init__(self, logger: Optional[logging.Logger] = None):
        self.logger = logger or logging.getLogger(__name__)
        self._subscriptions: List[_Subscription] = []
        self._started = False
        self._accepting = True
        self._lock = threading.Lock()

    def subscribe(
        self, name: str, handler: Callable[[TransmissionCompleted], None]
    ) -> None:
        with self._lock:
            if self._started:
                raise RuntimeError("事件总线启动后不可新增订阅者")
            self._subscriptions.append(_Subscription(name=name, handler=handler))

    def start(self) -> None:
        with self._lock:
            if self._started:
                return
            self._started = True
            self._accepting = True
            for subscription in self._subscriptions:
                subscription.thread = threading.Thread(
                    target=self._run_subscription,
                    args=(subscription,),
                    name=f"fmo-consumer-{subscription.name}",
                    daemon=False,
                )
                subscription.thread.start()

    def _run_subscription(self, subscription: _Subscription) -> None:
        while True:
            item = subscription.inbox.get()
            if item is _STOP:
                return
            try:
                subscription.handler(item)
            except Exception:
                self.logger.exception("消费者 %s 处理 PTT 事件失败", subscription.name)

    def publish(self, transmission: TransmissionCompleted) -> None:
        with self._lock:
            if not self._accepting:
                return
            subscriptions = tuple(self._subscriptions)
        for subscription in subscriptions:
            subscription.inbox.put_nowait(transmission)

    def stop(
        self, cancel_pending: bool = True, timeout: Optional[float] = None
    ) -> bool:
        with self._lock:
            self._accepting = False
            if not self._started:
                return True
            subscriptions = tuple(self._subscriptions)
            self._started = False
        for subscription in subscriptions:
            if cancel_pending:
                _drain(subscription.inbox)
            subscription.inbox.put_nowait(_STOP)
        deadline = None if timeout is None else time.monotonic() + timeout
        for subscription in subscriptions:
            if subscription.thread is not None:
                remaining = (
                    None if deadline is None else max(0.0, deadline - time.monotonic())
                )
                subscription.thread.join(timeout=remaining)
        return all(
            subscription.thread is None or not subscription.thread.is_alive()
            for subscription in subscriptions
        )


@dataclass
class _ActiveTransmission:
    header: MessageHeader
    generation: int
    first_received_at: float
    last_received_at: float
    packets: List[Tuple[bytes, float]]


class TransmissionProducer:
    """串行解析 MQTT 包、执行仲裁并产生一次 PTT 完成事件。"""

    def __init__(
        self,
        coordinator: ChannelCoordinator,
        event_bus: TransmissionEventBus,
        idle_timeout: float,
        event_log: EventLog,
        logger: logging.Logger,
        own_replay_filter: Callable[[MessageHeader], bool],
    ):
        self.coordinator = coordinator
        self.event_bus = event_bus
        self.idle_timeout = float(idle_timeout)
        self.event_log = event_log
        self.logger = logger
        self.own_replay_filter = own_replay_filter

        self.invalid_packets = 0
        self.loop_packets = 0
        self.rejected_packets = 0
        self._active: Optional[_ActiveTransmission] = None
        self._inbox: queue.Queue = queue.Queue()
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, name="fmo-transmission-producer", daemon=False
        )
        self._thread.start()

    def submit(self, payload: bytes, received_at: Optional[float] = None) -> None:
        self._inbox.put_nowait(
            (bytes(payload), time.monotonic() if received_at is None else received_at)
        )

    def process_packet(self, payload: bytes, received_at: float) -> bool:
        """同步处理一包；测试可直接调用。返回是否被路由接受。"""
        # 先结算旧 PTT 的空闲边界；线程调度延迟不能把间隔已满 2s 的同 UID
        # 新包错误续接到上一段。
        self.poll(received_at)
        try:
            packet = PacketParser.parse(payload)
        except ProtocolError as exc:
            self.invalid_packets += 1
            self.event_log.log(
                "packet_invalid", reason=exc.reason, bytes=len(payload)
            )
            self.logger.warning(
                "丢弃非法消息包（%s）: %s，长度 %dB",
                exc.reason,
                exc.detail,
                len(payload),
            )
            return False

        header = packet.header
        if self.own_replay_filter(header):
            self.loop_packets += 1
            self.event_log.log(
                "loop_detected",
                uid=header.uid,
                callsign=header.callsign,
                vendor=header.vendor,
            )
            return False

        decision = self.coordinator.accept_network_packet(
            header.uid, header.stream_begin_utc, received_at
        )

        if decision.action == RouteAction.DURATION_LIMIT:
            self.event_log.log(
                "uplink_limited", uid=header.uid, callsign=header.callsign
            )
            self._finalize("duration_limit")
            return False

        if not decision.accepted:
            self.rejected_packets += 1
            self.event_log.log(
                "route_rejected",
                uid=header.uid,
                callsign=header.callsign,
                current_uid=decision.previous_uid,
                reason=decision.reason,
            )
            return False

        if decision.action in (RouteAction.REPLACED, RouteAction.PREEMPTED):
            if decision.previous_origin == "network":
                reason = (
                    "route_replaced"
                    if decision.action == RouteAction.REPLACED
                    else "preempted"
                )
                self._finalize(reason)
            if decision.action == RouteAction.PREEMPTED:
                self.event_log.log(
                    "route_preempted",
                    previous_uid=decision.previous_uid,
                    uid=header.uid,
                    reason=decision.reason,
                )

        if decision.action in (RouteAction.ACQUIRED, RouteAction.REPLACED):
            self.event_log.log(
                "route_acquired",
                uid=header.uid,
                callsign=header.callsign,
                reason=decision.action.value,
            )

        if self._active is None or self._active.generation != decision.generation:
            self._start(packet.header, decision.generation, received_at, payload)
        else:
            self._active.packets.append((bytes(payload), received_at))
            self._active.last_received_at = received_at

        self.event_log.log(
            "packet_received",
            uid=header.uid,
            callsign=header.callsign,
            vendor=header.vendor,
            frames=header.frame_num,
            bytes=len(payload),
            checksum_ok=True,
        )
        return True

    def _start(
        self,
        header: MessageHeader,
        generation: int,
        received_at: float,
        payload: bytes,
    ) -> None:
        self._active = _ActiveTransmission(
            header=header.copy_with(),
            generation=generation,
            first_received_at=received_at,
            last_received_at=received_at,
            packets=[(bytes(payload), received_at)],
        )
        self.event_log.log(
            "stream_start",
            uid=header.uid,
            callsign=header.callsign,
            vendor=header.vendor,
            stream_begin_utc=header.stream_begin_utc,
            frames=header.frame_num,
        )

    def poll(self, now: Optional[float] = None) -> bool:
        """检查 2s 空闲边界；完成一段时返回 True。"""
        now = time.monotonic() if now is None else now
        active = self._active
        if active is None or now - active.last_received_at < self.idle_timeout:
            return False
        generation = active.generation
        self.coordinator.finish_network(generation)
        self._finalize("idle_timeout")
        return True

    def _finalize(self, reason: str) -> Optional[TransmissionCompleted]:
        active = self._active
        if active is None:
            return None
        self._active = None
        packets = tuple(
            TimedPacket(payload=data, offset_s=received_at - active.first_received_at)
            for data, received_at in active.packets
        )
        transmission = TransmissionCompleted(
            vendor=active.header.vendor,
            uid=active.header.uid,
            callsign=active.header.callsign,
            stream_begin_utc=active.header.stream_begin_utc,
            first_received_at=active.first_received_at,
            last_received_at=active.last_received_at,
            packets=packets,
            reason=reason,
        )
        self.event_log.log(
            "stream_end",
            uid=transmission.uid,
            callsign=transmission.callsign,
            packets=len(transmission.packets),
            duration_s=round(transmission.duration_s, 3),
            reason=reason,
        )
        if not self._stop.is_set():
            self.event_bus.publish(transmission)
        return transmission

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                item = self._inbox.get(timeout=0.1)
            except queue.Empty:
                self.poll()
                continue
            if item is _STOP:
                return
            payload, received_at = item
            try:
                self.process_packet(payload, received_at)
            except Exception:
                self.logger.exception("处理 MQTT 语音包时发生未预期错误")

    def stop(self, timeout: Optional[float] = None) -> bool:
        """立即取消：丢弃输入队列和未完成 PTT，不冲刷事件。"""
        self._stop.set()
        _drain(self._inbox)
        self._active = None
        self._inbox.put_nowait(_STOP)
        if self._thread is not None:
            self._thread.join(timeout=timeout)
            return not self._thread.is_alive()
        return True


def _drain(inbox: queue.Queue) -> None:
    while True:
        try:
            inbox.get_nowait()
        except queue.Empty:
            return
