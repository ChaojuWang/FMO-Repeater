"""RepeaterService 组合根与立即停机测试。"""

import threading
import time

from fmo_repeater.service import (
    EchoService,
    PublishOutcome,
    Recorder,
    RepeaterService,
    TimedPacket,
    TransmissionCompleted,
)


class MockTransport:
    def __init__(self):
        self.connected = False
        self.quiesced = False
        self.loop_stopped = False
        self.disconnected = False

    def quiesce(self):
        self.quiesced = True

    def disconnect(self):
        self.disconnected = True
        self.loop_stopped = True


def test_composition_keeps_echo_service_business_name(service_config):
    service = RepeaterService(service_config)
    assert isinstance(service.echo, EchoService)
    assert service.echo.coordinator is service.coordinator
    assert service.recorder is None
    assert [item.name for item in service.event_bus._subscriptions] == ['echo']


def test_recording_enabled_registers_independent_consumer(
    service_config, make_packet
):
    service_config['recording']['enabled'] = True
    service = RepeaterService(service_config)
    assert isinstance(service.recorder, Recorder)
    assert [item.name for item in service.event_bus._subscriptions] == [
        'echo', 'recorder'
    ]

    service.echo.stop()
    service.event_bus.start()
    service.event_bus.publish(
        TransmissionCompleted(
            vendor=0x1111,
            uid=42,
            callsign='FMOTEST',
            stream_begin_utc=1000,
            first_received_at=1.0,
            last_received_at=1.0,
            first_received_wall_time=1700000000.0,
            packets=(TimedPacket(make_packet(), 0.0),),
            reason='idle_timeout',
        )
    )
    deadline = time.monotonic() + 1.0
    directory = service.recorder.directory
    while not list(directory.glob('*.wav')) and time.monotonic() < deadline:
        time.sleep(0.01)
    service.event_bus.stop(cancel_pending=False)
    assert len(list(directory.glob('*.wav'))) == 1


def test_stop_is_immediate_and_idempotent(service_config):
    service = RepeaterService(service_config)
    client = MockTransport()
    service.transport = client
    service.stop()
    service.stop()
    assert client.loop_stopped is True
    assert client.disconnected is True
    assert client.quiesced is True
    assert service.running is False


def test_shutdown_request_before_run_does_not_restart_service(service_config):
    service = RepeaterService(service_config)
    service.request_shutdown("signal:15")
    service.run()
    assert service.running is False


def test_shutdown_disables_echo_and_bus_before_joining_producer(service_config):
    service = RepeaterService(service_config)
    order = []

    class Transport:
        def quiesce(self):
            order.append("quiesce")

        def disconnect(self):
            order.append("disconnect")

    class Echo:
        def stop(self):
            order.append("echo")

    class Bus:
        def stop(self, cancel_pending=True, timeout=None):
            order.append("bus")
            return True

    class Recorder:
        def stop(self):
            order.append("recorder")

    class Producer:
        def stop(self, timeout=None):
            order.append("producer")
            return True

    service.transport = Transport()
    service.echo = Echo()
    service.recorder = Recorder()
    service.event_bus = Bus()
    service.producer = Producer()
    service.stop()
    assert order == [
        "quiesce", "echo", "recorder", "bus", "producer", "disconnect"
    ]


def test_service_can_be_constructed_off_main_thread(service_config):
    created = []

    def construct():
        created.append(RepeaterService(service_config))

    thread = threading.Thread(target=construct)
    thread.start()
    thread.join(timeout=1.0)
    assert thread.is_alive() is False
    assert len(created) == 1


def test_disconnect_waits_for_active_echo_consumer(service_config, make_packet):
    order = []

    class LifecycleTransport:
        connected = True

        def __init__(self):
            self.waiting = threading.Event()

        def submit(self, payload):
            order.append("submit")
            return object()

        def wait_for_publish(self, ticket, cancel=None):
            order.append("wait")
            self.waiting.set()
            assert cancel.wait(1.0)
            order.append("consumer_finished")
            return PublishOutcome(False, "cancelled", 0)

        def quiesce(self):
            order.append("quiesce")

        def disconnect(self):
            assert "consumer_finished" in order
            order.append("disconnect")

    service = RepeaterService(service_config)
    transport = LifecycleTransport()
    service.transport = transport
    service.echo.transport = transport
    service.event_bus.start()
    payload = make_packet()
    service.event_bus.publish(
        TransmissionCompleted(
            vendor=0x1111,
            uid=42,
            callsign="FMOTEST",
            stream_begin_utc=1000,
            first_received_at=1.0,
            last_received_at=1.0,
            first_received_wall_time=1700000000.0,
            packets=(TimedPacket(payload, 0.0),),
            reason="idle_timeout",
        )
    )
    assert transport.waiting.wait(1.0)
    service.stop()
    assert order.index("consumer_finished") < order.index("disconnect")
