"""Paho MQTT 传输适配器：连接、发布确认与关闭边界。"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from paho.mqtt import client as mqtt_client


@dataclass(frozen=True)
class PublishOutcome:
    """一次 MQTT 发布提交的最终结果。"""

    success: bool
    reason: str
    rc: int
    detail: str = ""


class MqttTransport:
    """集中管理 Paho Client，并为业务层提供可取消的发布确认。"""

    def __init__(
        self,
        config: Dict[str, Any],
        on_payload: Callable[[bytes, float], None],
        on_connected: Optional[Callable[[], None]] = None,
        on_disconnected: Optional[Callable[[Any], None]] = None,
        logger=None,
        client_factory=None,
    ):
        self.config = config
        self.on_payload = on_payload
        self.on_connected = on_connected
        self.on_disconnected = on_disconnected
        self.logger = logger
        self._client_factory = client_factory or mqtt_client.Client

        mqtt_cfg = config["mqtt"]
        self.qos = int(mqtt_cfg.get("qos", 0))
        self.publish_timeout = float(mqtt_cfg.get("publish_timeout", 5.0))
        self.client = None
        self._connected = threading.Event()
        self._accepting = False
        self._lock = threading.Lock()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

    def connect(self) -> None:
        mqtt_cfg = self.config["mqtt"]
        client_id = "%s_%d" % (
            mqtt_cfg["client_id_prefix"], random.randint(0, 10000)
        )
        client = self._client_factory(
            mqtt_client.CallbackAPIVersion.VERSION2, client_id
        )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message

        if mqtt_cfg["username"]:
            client.username_pw_set(mqtt_cfg["username"], mqtt_cfg["password"])

        with self._lock:
            self.client = client
            self._accepting = True
        try:
            client.connect(
                mqtt_cfg["broker"], mqtt_cfg["port"], mqtt_cfg["keepalive"]
            )
            client.loop_start()
        except Exception:
            with self._lock:
                self._accepting = False
            raise

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            self._connected.clear()
            if self.logger is not None:
                self.logger.error("连接 MQTT 代理失败，返回码: %s", reason_code)
            return
        self._connected.set()
        topic = self.config["topics"]["subscribe"]
        client.subscribe(topic, qos=self.qos)
        if self.on_connected is not None:
            self.on_connected()

    def _on_disconnect(
        self, client, userdata, disconnect_flags, reason_code, properties
    ):
        self._connected.clear()
        if self.on_disconnected is not None:
            self.on_disconnected(reason_code)

    def _on_message(self, client, userdata, msg):
        received_at = time.monotonic()
        with self._lock:
            accepting = self._accepting
        if accepting:
            self.on_payload(bytes(msg.payload), received_at)

    def submit(self, payload: bytes):
        """原子检查关闭状态并向 Paho 提交发布，等待在锁外进行。"""
        with self._lock:
            if not self._accepting or self.client is None:
                raise RuntimeError("MQTT 传输已停止接受发布")
            return self.client.publish(
                self.config["topics"]["publish"], bytes(payload), qos=self.qos
            )

    def wait_for_publish(
        self, info, cancel: Optional[threading.Event] = None
    ) -> PublishOutcome:
        """等待 Paho 完成通知；每 100ms 检查一次取消和总超时。"""
        rc = int(info.rc)
        if rc != mqtt_client.MQTT_ERR_SUCCESS:
            return PublishOutcome(False, "publish_rc", rc)

        deadline = time.monotonic() + self.publish_timeout
        while True:
            try:
                if info.is_published():
                    return PublishOutcome(True, "published", rc)
            except (RuntimeError, ValueError) as exc:
                return PublishOutcome(False, "publish_error", rc, str(exc))

            if cancel is not None and cancel.is_set():
                return PublishOutcome(False, "cancelled", rc)

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return PublishOutcome(False, "timeout", rc)
            try:
                info.wait_for_publish(timeout=min(0.1, remaining))
            except (RuntimeError, ValueError) as exc:
                return PublishOutcome(False, "publish_error", rc, str(exc))

    def quiesce(self) -> None:
        """关闭入站与出站提交入口；已提交消息由业务线程完成或取消等待。"""
        with self._lock:
            self._accepting = False

    def disconnect(self) -> None:
        """业务线程退出后断开连接并停止 Paho 网络循环。"""
        self.quiesce()
        with self._lock:
            client = self.client
        if client is None:
            return
        try:
            client.disconnect()
        finally:
            client.loop_stop()
            self._connected.clear()
