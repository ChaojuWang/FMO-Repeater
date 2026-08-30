"""RepeaterService 组合根与立即停机测试。"""

from fmo_repeater.service import EchoService, RepeaterService


class MockClient:
    def __init__(self):
        self.loop_stopped = False
        self.disconnected = False

    def loop_stop(self):
        self.loop_stopped = True

    def disconnect(self):
        self.disconnected = True


def test_composition_keeps_echo_service_business_name(service_config):
    service = RepeaterService(service_config)
    assert isinstance(service.echo, EchoService)
    assert service.echo.coordinator is service.coordinator


def test_stop_is_immediate_and_idempotent(service_config):
    service = RepeaterService(service_config)
    client = MockClient()
    service.mqtt_client = client
    service.stop()
    service.stop()
    assert client.loop_stopped is True
    assert client.disconnected is True
    assert service.running is False


def test_signal_before_run_does_not_restart_service(service_config):
    service = RepeaterService(service_config)
    service._signal_handler(15, None)
    service.run()
    assert service.running is False


def test_shutdown_disables_echo_and_bus_before_joining_producer(service_config):
    service = RepeaterService(service_config)
    order = []

    class Echo:
        def stop(self):
            order.append("echo")

    class Bus:
        def stop(self, cancel_pending=True):
            order.append("bus")

    class Producer:
        def stop(self):
            order.append("producer")

    service.echo = Echo()
    service.event_bus = Bus()
    service.producer = Producer()
    service.stop()
    assert order == ["echo", "bus", "producer"]
