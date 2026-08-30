"""FMO §8 单信道路由仲裁测试。"""

import threading

from fmo_repeater.protocol import (
    ChannelCoordinator,
    RouteAction,
    stream_begin_delta_ms,
)


def test_same_uid_continues_route():
    coordinator = ChannelCoordinator()
    first = coordinator.accept_network_packet(10, 1000, 5.0)
    continued = coordinator.accept_network_packet(10, 2000, 5.5)
    assert first.action == RouteAction.ACQUIRED
    assert continued.action == RouteAction.CONTINUED
    assert continued.generation == first.generation


def test_route_window_boundary_replaces():
    coordinator = ChannelCoordinator()
    coordinator.accept_network_packet(10, 1000, 5.0)
    decision = coordinator.accept_network_packet(20, 2000, 6.5)
    assert decision.action == RouteAction.REPLACED
    assert decision.previous_uid == 10


def test_earlier_stream_preempts_inclusive_2000ms():
    coordinator = ChannelCoordinator()
    coordinator.accept_network_packet(20, 5000, 10.0)
    decision = coordinator.accept_network_packet(30, 3000, 10.1)
    assert decision.action == RouteAction.PREEMPTED
    assert decision.reason == "earlier_stream"


def test_earlier_stream_over_2000ms_is_rejected():
    coordinator = ChannelCoordinator()
    coordinator.accept_network_packet(20, 5000, 10.0)
    decision = coordinator.accept_network_packet(30, 2999, 10.1)
    assert decision.action == RouteAction.REJECTED


def test_equal_stream_begin_smaller_uid_preempts():
    coordinator = ChannelCoordinator()
    coordinator.accept_network_packet(20, 5000, 10.0)
    decision = coordinator.accept_network_packet(19, 5000, 10.1)
    assert decision.action == RouteAction.PREEMPTED
    assert decision.reason == "smaller_uid"


def test_stream_begin_comparison_handles_uint32_wrap():
    assert stream_begin_delta_ms(0xFFFFFFF0, 0x00000050) == -96
    coordinator = ChannelCoordinator()
    coordinator.accept_network_packet(20, 0x00000050, 10.0)
    decision = coordinator.accept_network_packet(30, 0xFFFFFFF0, 10.1)
    assert decision.action == RouteAction.PREEMPTED


def test_duration_limit_blocks_uid_until_silence():
    coordinator = ChannelCoordinator(max_uplink_duration=30)
    coordinator.accept_network_packet(10, 1000, 0.0)
    limited = coordinator.accept_network_packet(10, 1000, 30.0)
    blocked = coordinator.accept_network_packet(10, 1000, 30.5)
    assert limited.action == RouteAction.DURATION_LIMIT
    assert blocked.reason == "uplink_uid_blocked"
    # 从最后一次被阻断的包起静默满 1500ms 后可重新占用。
    resumed = coordinator.accept_network_packet(10, 2000, 32.0)
    assert resumed.action == RouteAction.ACQUIRED


def test_duration_zero_is_unlimited():
    coordinator = ChannelCoordinator(max_uplink_duration=0)
    first = coordinator.accept_network_packet(10, 1000, 0.0)
    later = coordinator.accept_network_packet(10, 1000, 999.0)
    assert later.action == RouteAction.REPLACED or later.accepted
    assert first.accepted and later.accepted


def test_echo_lease_uses_same_arbiter_and_can_be_preempted():
    coordinator = ChannelCoordinator()
    lease = coordinator.try_start_echo(65535, 5000, 10.0)
    assert lease is not None
    rejected = coordinator.accept_network_packet(100, 5001, 10.1)
    assert rejected.action == RouteAction.REJECTED
    preempted = coordinator.accept_network_packet(100, 4999, 10.2)
    assert preempted.action == RouteAction.PREEMPTED
    assert preempted.previous_origin == "echo"
    assert lease.refresh(10.3) is False


def test_echo_cannot_start_while_network_route_active():
    coordinator = ChannelCoordinator()
    coordinator.accept_network_packet(10, 1000, 5.0)
    assert coordinator.try_start_echo(65535, 2000, 5.1) is None


def test_same_uid_network_does_not_continue_echo_origin():
    coordinator = ChannelCoordinator()
    lease = coordinator.try_start_echo(42, 1000, 10.0)
    decision = coordinator.accept_network_packet(42, 2000, 10.1)
    assert decision.action == RouteAction.REJECTED
    assert lease.refresh(10.2) is True


def test_echo_publish_is_atomic_against_network_preemption():
    coordinator = ChannelCoordinator()
    lease = coordinator.try_start_echo(65535, 5000, 10.0)
    entered = threading.Event()
    release = threading.Event()
    network_done = threading.Event()
    publish_result = []
    decision_result = []

    def publisher():
        entered.set()
        assert release.wait(1.0)
        return "published"

    def publish_echo():
        publish_result.append(lease.publish(10.1, publisher))

    def preempt():
        assert entered.wait(1.0)
        decision_result.append(
            coordinator.accept_network_packet(1, 4999, 10.2)
        )
        network_done.set()

    echo_thread = threading.Thread(target=publish_echo)
    network_thread = threading.Thread(target=preempt)
    echo_thread.start()
    network_thread.start()
    assert entered.wait(1.0)
    assert network_done.wait(0.05) is False
    release.set()
    echo_thread.join(timeout=1.0)
    network_thread.join(timeout=1.0)
    assert publish_result == [(True, "published")]
    assert decision_result[0].action == RouteAction.PREEMPTED
