"""EchoService 作为事件消费者与本地语音流生产者的测试。"""

import threading

from fmo_repeater.protocol import ChannelCoordinator, PacketParser
from fmo_repeater.service import EchoService, PublishOutcome
from fmo_repeater.service.transmission import TimedPacket, TransmissionCompleted


class MockTransport:
    def __init__(self, on_publish=None):
        self.published = []
        self.on_publish = on_publish

    def submit(self, payload):
        self.published.append(payload)
        if self.on_publish:
            self.on_publish()
        return object()

    def wait_for_publish(self, ticket, cancel=None):
        return PublishOutcome(True, "published", 0)


def make_event(payloads, offsets=None):
    offsets = offsets or [0.0] * len(payloads)
    return TransmissionCompleted(
        vendor=0x1111,
        uid=42,
        callsign="FMOTEST",
        stream_begin_utc=1000,
        first_received_at=10.0,
        last_received_at=10.0 + offsets[-1],
        packets=tuple(TimedPacket(p, o) for p, o in zip(payloads, offsets)),
        reason="idle_timeout",
    )


def make_echo(service_config, coordinator=None):
    coordinator = coordinator or ChannelCoordinator()
    echo = EchoService(service_config, coordinator)
    echo.transport = MockTransport()
    return echo, coordinator


def test_own_replay_filter(service_config, make_packet):
    echo, _ = make_echo(service_config)
    normal = PacketParser.parse(make_packet(uid=42)).header
    own_uid = PacketParser.parse(make_packet(uid=65535, vendor=0x2000)).header
    own_prefix = PacketParser.parse(
        make_packet(uid=1, vendor=0x2000, callsign="RE>X")
    ).header
    assert echo.is_own_replay(normal) is False
    assert echo.is_own_replay(own_uid) is True
    assert echo.is_own_replay(own_prefix) is True


def test_replay_rewrites_headers_and_preserves_frames(service_config, make_packet):
    echo, _ = make_echo(service_config)
    original = make_packet(uid=42, callsign="FMOTEST", n_frames=2)
    echo.handle(make_event([original]))
    rewritten = PacketParser.parse(echo.transport.published[0])
    parsed = PacketParser.parse(original)
    assert rewritten.header.vendor == 0x2000
    assert rewritten.header.uid == 65535
    assert rewritten.header.callsign == "RE>FMOTEST"
    assert rewritten.header.stream_begin_utc != parsed.header.stream_begin_utc
    assert rewritten.frames == parsed.frames


def test_one_replay_uses_one_stream_begin(service_config, make_packet):
    echo, _ = make_echo(service_config)
    packets = [make_packet(uid=42), make_packet(uid=42)]
    echo.handle(make_event(packets))
    parsed = [PacketParser.parse(payload) for payload in echo.transport.published]
    assert len(parsed) == 2
    assert parsed[0].header.stream_begin_utc == parsed[1].header.stream_begin_utc


def test_busy_channel_rejects_echo_without_queueing(service_config, make_packet):
    coordinator = ChannelCoordinator()
    echo, _ = make_echo(service_config, coordinator)
    # 将协调器的时间基准放在当前 monotonic 附近，确保路由仍活跃。
    import time
    now = time.monotonic()
    coordinator.accept_network_packet(7, 1000, now)
    echo.handle(make_event([make_packet()]))
    assert echo.transport.published == []


def test_max_duration_truncates_echo_only(service_config, make_packet):
    service_config["echo"]["max_duration"] = 1.0
    echo, _ = make_echo(service_config)
    packets = [make_packet(), make_packet(), make_packet()]
    echo.handle(make_event(packets, [0.0, 1.0, 2.0]))
    assert len(echo.transport.published) == 2


def test_network_preemption_stops_remaining_echo(service_config, make_packet):
    coordinator = ChannelCoordinator()
    echo = EchoService(service_config, coordinator)
    first_published = threading.Event()

    def preempt_after_first():
        assert first_published.wait(1.0)
        route = coordinator.snapshot()
        coordinator.accept_network_packet(
            1, (route.stream_begin_utc - 1) & 0xFFFFFFFF, route.last_activity + 0.01
        )

    client = MockTransport(first_published.set)
    echo.transport = client
    preempt_thread = threading.Thread(target=preempt_after_first)
    preempt_thread.start()
    echo.handle(make_event([make_packet(), make_packet()], [0.0, 0.05]))
    preempt_thread.join(timeout=1.0)
    assert len(client.published) == 1


def test_stop_interrupts_replay_wait_immediately(service_config, make_packet):
    echo, _ = make_echo(service_config)
    event = make_event([make_packet(), make_packet()], [0.0, 5.0])
    thread = threading.Thread(target=echo.handle, args=(event,))
    thread.start()
    for _ in range(100):
        if echo.transport.published:
            break
        import time
        time.sleep(0.005)
    echo.stop()
    thread.join(timeout=0.5)
    assert thread.is_alive() is False
    assert len(echo.transport.published) == 1


def test_stop_cancels_publish_confirmation(service_config, make_packet):
    class BlockingTransport(MockTransport):
        def __init__(self):
            super().__init__()
            self.waiting = threading.Event()

        def wait_for_publish(self, ticket, cancel=None):
            self.waiting.set()
            assert cancel.wait(1.0)
            return PublishOutcome(False, "cancelled", 0)

    echo, _ = make_echo(service_config)
    echo.transport = BlockingTransport()
    thread = threading.Thread(
        target=echo.handle, args=(make_event([make_packet()]),)
    )
    thread.start()
    assert echo.transport.waiting.wait(1.0)
    echo.stop()
    thread.join(timeout=0.5)
    assert thread.is_alive() is False
