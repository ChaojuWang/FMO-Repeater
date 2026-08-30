"""FMO Repeater 组合根：MQTT、PTT 生产者和业务消费者生命周期。"""

from __future__ import annotations

import time
from typing import Any, Dict, Optional

from ..protocol import ChannelCoordinator
from .echo import EchoService
from .event_log import EventLog
from .logging_setup import setup_logging
from .mqtt_transport import MqttTransport
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
        self.transport = MqttTransport(
            config,
            on_payload=self.producer.submit,
            on_connected=self._on_connected,
            on_disconnected=self._on_disconnected,
            logger=self.logger,
        )
        self.echo.transport = self.transport
        self.running = False
        self._stopped = False
        self._shutdown_requested = False

    @property
    def connected(self) -> bool:
        return self.transport.connected

    @property
    def shutdown_requested(self) -> bool:
        return self._shutdown_requested

    @property
    def invalid_packets(self) -> int:
        return self.producer.invalid_packets

    @property
    def loop_packets(self) -> int:
        return self.producer.loop_packets

    def _on_connected(self) -> None:
        topic = self.config["topics"]["subscribe"]
        self.event_log.log(
            "service_started",
            version=1,
            vendor=self.config["echo"]["vendor"],
            subscribe_topic=topic,
            transmission_timeout=self.config["transmission"]["idle_timeout"],
        )
        self.logger.info("已订阅主题: %s", topic)

    def _on_disconnected(self, reason_code) -> None:
        if reason_code == 0:
            self.logger.info("已主动断开 MQTT 连接")
        else:
            self.logger.warning("MQTT 连接断开，原因码: %s", reason_code)

    def connect(self) -> None:
        self.transport.connect()

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

    def request_shutdown(self, reason="requested") -> None:
        self.logger.info("收到停止请求（%s），立即停止服务", reason)
        self._shutdown_requested = True
        self.running = False

    def stop(self) -> None:
        if self._stopped:
            return
        self._stopped = True
        self._shutdown_requested = True
        self.running = False
        self.transport.quiesce()
        self.echo.stop()
        bus_stopped = self.event_bus.stop(cancel_pending=True, timeout=None)
        producer_stopped = self.producer.stop(timeout=None)
        if not bus_stopped or not producer_stopped:
            raise RuntimeError("业务线程未能在 MQTT 断开前停止")
        self.transport.disconnect()
        self.event_log.log("service_stopped", reason="shutdown")
        self.logger.info("FMO Repeater 服务已停止")
