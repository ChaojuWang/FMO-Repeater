"""PTT 完成事件生产与进程内广播测试。"""

import logging
import threading
import time

from fmo_repeater.protocol import ChannelCoordinator
from fmo_repeater.service import EventLog
from fmo_repeater.service.transmission import (
    TimedPacket,
    TransmissionCompleted,
    TransmissionEventBus,
    TransmissionProducer,
)


class CollectBus:
    def __init__(self):
        self.events = []

    def publish(self, event):
        self.events.append(event)


class SnapshotBus(CollectBus):
    def __init__(self, coordinator):
        super().__init__()
        self.coordinator = coordinator
        self.route_at_publish = "unset"

    def publish(self, event):
        self.route_at_publish = self.coordinator.snapshot()
        super().publish(event)


def make_producer(service_config, own_filter=lambda header: False, max_uplink=60):
    bus = CollectBus()
    producer = TransmissionProducer(
        coordinator=ChannelCoordinator(max_uplink_duration=max_uplink),
        event_bus=bus,
        idle_timeout=2.0,
        event_log=EventLog(service_config),
        logger=logging.getLogger("test-transmission"),
        own_replay_filter=own_filter,
    )
    return producer, bus


def test_idle_timeout_emits_immutable_transmission(service_config, make_packet):
    producer, bus = make_producer(service_config)
    payload = make_packet(uid=10, stream_begin_utc=1000)
    assert producer.process_packet(payload, 5.0)
    assert producer.poll(6.999) is False
    assert producer.poll(7.0) is True
    event = bus.events[0]
    assert event.uid == 10
    assert event.reason == "idle_timeout"
    assert event.packets == (TimedPacket(payload, 0.0),)


def test_conflicting_packet_is_not_mixed(service_config, make_packet):
    producer, bus = make_producer(service_config)
    first = make_packet(uid=10, stream_begin_utc=1000)
    loser = make_packet(uid=20, stream_begin_utc=2000)
    assert producer.process_packet(first, 5.0)
    assert producer.process_packet(loser, 5.1) is False
    producer.poll(7.0)
    assert len(bus.events) == 1
    assert [packet.payload for packet in bus.events[0].packets] == [first]


def test_preemption_finishes_old_and_starts_winner(service_config, make_packet):
    producer, bus = make_producer(service_config)
    old = make_packet(uid=20, stream_begin_utc=5000)
    winner = make_packet(uid=30, stream_begin_utc=4000)
    producer.process_packet(old, 5.0)
    producer.process_packet(winner, 5.1)
    assert bus.events[0].uid == 20
    assert bus.events[0].reason == "preempted"
    producer.poll(7.1)
    assert bus.events[1].uid == 30


def test_same_uid_at_idle_boundary_starts_new_ptt(service_config, make_packet):
    producer, bus = make_producer(service_config)
    first = make_packet(uid=10, stream_begin_utc=1000)
    second = make_packet(uid=10, stream_begin_utc=2000)
    producer.process_packet(first, 5.0)
    producer.process_packet(second, 7.0)
    assert len(bus.events) == 1
    assert bus.events[0].packets[0].payload == first
    producer.poll(9.0)
    assert len(bus.events) == 2
    assert bus.events[1].packets[0].payload == second


def test_queued_packets_use_receipt_time_not_worker_delay(service_config, make_packet):
    producer, bus = make_producer(service_config)
    producer.submit(make_packet(uid=10), 0.0)
    producer.submit(make_packet(uid=10), 1.0)
    producer.start()
    deadline = time.monotonic() + 1.0
    while not bus.events and time.monotonic() < deadline:
        time.sleep(0.01)
    producer.stop()
    assert len(bus.events) == 1
    assert len(bus.events[0].packets) == 2


def test_idle_route_is_released_before_event_publish(service_config, make_packet):
    coordinator = ChannelCoordinator()
    bus = SnapshotBus(coordinator)
    producer = TransmissionProducer(
        coordinator=coordinator,
        event_bus=bus,
        idle_timeout=0.5,
        event_log=EventLog(service_config),
        logger=logging.getLogger("test-release-before-publish"),
        own_replay_filter=lambda header: False,
    )
    producer.process_packet(make_packet(uid=10), 5.0)
    producer.poll(5.5)
    assert bus.route_at_publish is None


def test_invalid_and_own_replay_are_filtered(service_config, make_packet):
    producer, bus = make_producer(
        service_config, own_filter=lambda header: header.uid == 99
    )
    assert producer.process_packet(b"bad", 1.0) is False
    assert producer.process_packet(make_packet(uid=99), 2.0) is False
    assert producer.invalid_packets == 1
    assert producer.loop_packets == 1
    assert bus.events == []


def test_duration_limit_emits_accepted_prefix(service_config, make_packet):
    producer, bus = make_producer(service_config, max_uplink=30)
    for second in range(30):
        assert producer.process_packet(make_packet(uid=10), float(second))
    assert producer.process_packet(make_packet(uid=10), 30.0) is False
    assert bus.events[0].reason == "duration_limit"
    assert len(bus.events[0].packets) == 30


def test_event_bus_isolates_consumers():
    bus = TransmissionEventBus(logging.getLogger("test-bus"))
    delivered = []
    done = threading.Event()

    def broken(event):
        raise RuntimeError("boom")

    def healthy(event):
        delivered.append(event)
        done.set()

    bus.subscribe("broken", broken)
    bus.subscribe("healthy", healthy)
    bus.start()
    event = TransmissionCompleted(1, 2, "T", 3, 0.0, 0.0, (), "idle_timeout")
    bus.publish(event)
    assert done.wait(1.0)
    bus.stop()
    assert delivered == [event]


def test_stopped_event_bus_rejects_late_publish():
    bus = TransmissionEventBus(logging.getLogger("test-stopped-bus"))
    delivered = []
    bus.subscribe("consumer", delivered.append)
    bus.start()
    bus.stop()
    event = TransmissionCompleted(1, 2, "T", 3, 0.0, 0.0, (), "idle_timeout")
    bus.publish(event)
    time.sleep(0.02)
    assert delivered == []


def test_event_bus_reports_consumer_join_timeout():
    bus = TransmissionEventBus(logging.getLogger("test-timeout-bus"))
    entered = threading.Event()
    release = threading.Event()

    def blocking(event):
        entered.set()
        release.wait(1.0)

    bus.subscribe("blocking", blocking)
    bus.start()
    event = TransmissionCompleted(1, 2, "T", 3, 0.0, 0.0, (), "idle_timeout")
    bus.publish(event)
    assert entered.wait(1.0)
    assert bus.stop(timeout=0.01) is False
    release.set()
    subscription = bus._subscriptions[0]
    subscription.thread.join(timeout=1.0)
    assert subscription.thread.is_alive() is False
