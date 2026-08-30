"""Echo 回音服务：PTT 完成事件消费者与本地信道语音流生产者。"""

from __future__ import annotations

import threading
import time
from typing import Any, Dict, Optional

from paho.mqtt import client as mqtt_client

from ..protocol import ChannelCoordinator, MessageHeader, PacketParser
from .event_log import EventLog
from .logging_setup import setup_logging
from .transmission import TransmissionCompleted


def _utc_ms() -> int:
    return int(time.time() * 1000) & 0xFFFFFFFF


class EchoService:
    """消费完整 PTT，并在获得统一信道路由后按原时间轴回放。"""

    def __init__(
        self,
        config: Dict[str, Any],
        coordinator: ChannelCoordinator,
        event_log: Optional[EventLog] = None,
        logger=None,
    ):
        self.config = config
        self.coordinator = coordinator
        self.logger = logger or setup_logging(config)
        self.event_log = event_log or EventLog(config)
        echo_cfg = config["echo"]
        self.replay_vendor = echo_cfg["vendor"]
        self.replay_uid = echo_cfg["uid"]
        self.callsign_prefix = echo_cfg["callsign_prefix"]
        self.max_duration = float(echo_cfg.get("max_duration", 30.0))
        self.mqtt_client = None
        self._stop = threading.Event()
        self._lease_lock = threading.Lock()
        self._active_lease = None

    def is_own_replay(self, header: MessageHeader) -> bool:
        """识别 MQTT 回环的本服务语音包。"""
        if header.vendor != self.replay_vendor:
            return False
        if self.replay_uid and header.uid == self.replay_uid:
            return True
        return header.callsign.startswith(self.callsign_prefix)

    _is_own_replay = is_own_replay

    def handle(self, transmission: TransmissionCompleted) -> None:
        """事件总线消费者入口；信道忙则整段丢弃。"""
        if self._stop.is_set() or not transmission.packets:
            return
        packets = tuple(
            packet
            for packet in transmission.packets
            if packet.offset_s <= self.max_duration
        )
        dropped = len(transmission.packets) - len(packets)
        if not packets:
            return

        replay_stream_begin = _utc_ms()
        lease = self.coordinator.try_start_echo(
            self.replay_uid or transmission.uid,
            replay_stream_begin,
            time.monotonic(),
        )
        if lease is None:
            self.event_log.log(
                "echo_route_rejected",
                uid=transmission.uid,
                callsign=transmission.callsign,
                reason="channel_busy",
            )
            self.logger.info(
                "信道忙，丢弃回音 - UID=%s, 呼号='%s'",
                transmission.uid,
                transmission.callsign,
            )
            return

        with self._lease_lock:
            if self._stop.is_set():
                lease.cancel()
                return
            self._active_lease = lease

        self.event_log.log(
            "echo_route_acquired",
            uid=transmission.uid,
            callsign=transmission.callsign,
            replay_uid=self.replay_uid or transmission.uid,
            stream_begin_utc=replay_stream_begin,
        )

        self.event_log.log(
            "replay_started",
            uid=transmission.uid,
            callsign=transmission.callsign,
            packets=len(packets),
        )
        success = failed = 0
        reason = "completed"
        started_at = time.monotonic()

        for index, timed_packet in enumerate(packets):
            delay = started_at + timed_packet.offset_s - time.monotonic()
            if delay > 0 and self._stop.wait(delay):
                reason = "shutdown"
                break
            if self._stop.is_set():
                reason = "shutdown"
                break
            try:
                parsed = PacketParser.parse(timed_packet.payload)
                payload = self._rewrite_packet(parsed, replay_stream_begin)
                accepted, result = lease.publish(
                    time.monotonic(),
                    lambda: self.mqtt_client.publish(
                        self.config["topics"]["publish"], payload
                    ),
                )
                if not accepted:
                    reason = "preempted"
                    self.event_log.log(
                        "echo_preempted",
                        uid=transmission.uid,
                        callsign=transmission.callsign,
                        published=success,
                    )
                    break
                assert result is not None
                if result.rc == mqtt_client.MQTT_ERR_SUCCESS:
                    success += 1
                else:
                    failed += 1
                    self.logger.warning(
                        "发布回音包 [%d/%d] 失败，返回码: %s",
                        index + 1,
                        len(packets),
                        result.rc,
                    )
            except Exception:
                failed += 1
                self.logger.exception("发布回音包 [%d/%d] 失败", index + 1, len(packets))

        duration = time.monotonic() - started_at
        self.event_log.log(
            "replay_finished",
            uid=transmission.uid,
            callsign=transmission.callsign,
            packets=len(packets),
            ok=success,
            failed=failed,
            duration_s=round(duration, 3),
            truncated=dropped > 0,
            dropped=dropped,
            reason=reason,
        )
        self.logger.info(
            "回音结束 - 成功: %d, 失败: %d, 原因: %s", success, failed, reason
        )
        with self._lease_lock:
            if self._active_lease is lease:
                self._active_lease = None

    def _rewrite_packet(self, packet, stream_begin_utc: Optional[int] = None) -> bytes:
        """重写回音包头；整次回放共享 stream_begin_utc。"""
        header = packet.header
        now_ms = _utc_ms()
        new_header = header.copy_with(
            vendor=self.replay_vendor,
            callsign=f"{self.callsign_prefix}{header.callsign}",
            stream_begin_utc=now_ms if stream_begin_utc is None else stream_begin_utc,
            timestamp=now_ms,
        )
        if self.replay_uid:
            new_header.uid = self.replay_uid
        return new_header.to_bytes() + b"".join(
            frame.to_bytes() for frame in packet.frames
        )

    def stop(self) -> None:
        """立即取消正在等待或发布的回音。"""
        self._stop.set()
        with self._lease_lock:
            lease = self._active_lease
            self._active_lease = None
        if lease is not None:
            lease.cancel()
