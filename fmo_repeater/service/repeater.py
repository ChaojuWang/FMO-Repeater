"""FMO Repeater 组合根：MQTT、PTT 生产者和业务消费者生命周期。"""

from __future__ import annotations

import random
import signal
import time
from typing import Any, Dict, Optional

from paho.mqtt import client as mqtt_client
import paho.mqtt.enums

from ..protocol import ChannelCoordinator
from .echo import EchoService
from .event_log import EventLog
from .logging_setup import setup_logging
from .transmission import TransmissionEventBus, TransmissionProducer


class RepeaterService:
    """监听 MQTT 信道、产生 PTT 事件并编排 Echo 服务。"""

    def __init__(self, config: Dict[str, Any], event_log: Optional[EventLog] = None):
        self.config = config
        self.logger = setup_logging(config)
        self.event_log = event_log or EventLog(config)
        transmission_cfg = config["transmission"]
        self.coordinator = ChannelCoordinator(
            max_uplink_duration=transmission_cfg["max_uplink_duration"]
        )
        self.event_bus = TransmissionEventBus(self.logger)
        self.echo = EchoService(
            config, self.coordinator, event_log=self.event_log, logger=self.logger
        )
        self.producer = TransmissionProducer(
            coordinator=self.coordinator,
            event_bus=self.event_bus,
            idle_timeout=transmission_cfg["idle_timeout"],
            event_log=self.event_log,
            logger=self.logger,
            own_replay_filter=self.echo.is_own_replay,
        )
        self.event_bus.subscribe("echo", self.echo.handle)
        self.mqtt_client = None
        self.connected = False
        self.running = False
        self._stopped = False
        self._shutdown_requested = False
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

    @property
    def invalid_packets(self) -> int:
        return self.producer.invalid_packets

    @property
    def loop_packets(self) -> int:
        return self.producer.loop_packets

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code != 0:
            self.connected = False
            self.logger.error("连接 MQTT 代理失败，返回码: %s", reason_code)
            return
        self.connected = True
        topic = self.config["topics"]["subscribe"]
        client.subscribe(topic)
        self.event_log.log(
            "service_started",
            version=1,
            vendor=self.config["echo"]["vendor"],
            subscribe_topic=topic,
            transmission_timeout=self.config["transmission"]["idle_timeout"],
        )
        self.logger.info("已订阅主题: %s", topic)

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        self.connected = False
        if reason_code == 0:
            self.logger.info("已主动断开 MQTT 连接")
        else:
            self.logger.warning("MQTT 连接断开，原因码: %s", reason_code)

    def _on_message(self, client, userdata, msg):
        try:
            self.producer.submit(msg.payload, time.monotonic())
        except Exception:
            self.logger.exception("MQTT 回调入队失败")

    def connect(self) -> None:
        client_id = "%s_%d" % (
            self.config["mqtt"]["client_id_prefix"], random.randint(0, 10000)
        )
        client = mqtt_client.Client(
            paho.mqtt.enums.CallbackAPIVersion.VERSION2, client_id
        )
        client.on_connect = self._on_connect
        client.on_disconnect = self._on_disconnect
        client.on_message = self._on_message
        mqtt_cfg = self.config["mqtt"]
        if mqtt_cfg["username"]:
            client.username_pw_set(mqtt_cfg["username"], mqtt_cfg["password"])
        self.mqtt_client = client
        self.echo.mqtt_client = client
        client.connect(mqtt_cfg["broker"], mqtt_cfg["port"], mqtt_cfg["keepalive"])
        client.loop_start()

    def run(self) -> None:
        if self._shutdown_requested:
            self.stop()
            return
        self.running = True
        self._stopped = False
        self.event_bus.start()
        self.producer.start()
        self.logger.info("FMO Repeater 服务已启动")
        try:
            while self.running:
                time.sleep(0.1)
        except KeyboardInterrupt:
            pass
        finally:
            self.stop()

    def _signal_handler(self, signum, frame):
        self.logger.info("接收到信号 %s，立即停止服务", signum)
        self._shutdown_requested = True
        self.running = False

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._shutdown_requested = True
        self.running = False
        self.echo.stop()
        self.event_bus.stop(cancel_pending=True)
        self.producer.stop()
        if self.mqtt_client is not None:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        self.event_log.log("service_stopped", reason="signal")
        self.logger.info("FMO Repeater 服务已停止")
