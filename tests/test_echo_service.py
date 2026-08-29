"""Echo 服务测试（mock MQTT，不起网络）"""

import time

import pytest

from fmo_repeater.protocol import PacketParser
from fmo_repeater.service import EchoService, EventLog
from conftest import sine_pcm16


class MockMQTTMessage:
    def __init__(self, payload):
        self.payload = payload
        self.topic = "TEST/FMO/RAW"


class MockMQTTClient:
    def __init__(self):
        self.published = []

    def publish(self, topic, payload, qos=0):
        self.published.append((topic, payload))

        class Result:
            rc = 0
        return Result()


@pytest.fixture
def service(service_config):
    svc = EchoService(service_config)
    svc.mqtt_client = MockMQTTClient()
    return svc


def consume_queue(svc):
    """同步消费：把队列里已入队的包全部消费进 buffer（不触发超时回放）。"""
    while not svc._queue.empty():
        svc._consume_once(timeout=0.0)


def trigger_replay(svc):
    """模拟「停止收包满 timeout 秒」→ 触发一次超时回放。"""
    # 把 last_message_time 拨回过去，再走空闲判定
    if svc.last_message_time is not None:
        svc.last_message_time -= (svc.stream_timeout + 1.0)
    svc._maybe_replay_if_idle()



class TestLoopPrevention:
    def test_normal_packet_buffered(self, service, make_packet):
        service._on_message(None, None, MockMQTTMessage(make_packet()))
        consume_queue(service)
        assert len(service._buffer) == 1
        assert service.loop_packets == 0
        assert service.invalid_packets == 0

    def test_own_replay_skipped(self, service, make_packet):
        """vendor 匹配 + 呼号前缀匹配 → 跳过（防循环双条件）"""
        packet = make_packet()
        service._on_message(None, None, MockMQTTMessage(packet))
        consume_queue(service)
        replayed = service._rewrite_packet(service._buffer[0][0])
        service._on_message(None, None, MockMQTTMessage(replayed))
        consume_queue(service)
        assert len(service._buffer) == 1
        assert service.loop_packets == 1

    def test_same_vendor_no_prefix_not_skipped(self, service, make_packet):
        """他人软件区同 vendor 但无前缀 → 不跳过"""
        packet = make_packet(vendor=0x2000, callsign="BG5ESN")
        service._on_message(None, None, MockMQTTMessage(packet))
        consume_queue(service)
        assert len(service._buffer) == 1
        assert service.loop_packets == 0

    def test_prefix_but_other_vendor_not_skipped(self, service, make_packet):
        """呼号带 RE> 但 vendor 不同 → 不跳过"""
        packet = make_packet(callsign="RE>XX")
        service._on_message(None, None, MockMQTTMessage(packet))
        consume_queue(service)
        assert len(service._buffer) == 1
        assert service.loop_packets == 0

    def test_uid_match_skipped(self, service_config, make_packet):
        """echo.uid != 0 时，UID 匹配也触发跳过"""
        service_config['echo']['uid'] = 4242
        svc = EchoService(service_config)
        svc.mqtt_client = MockMQTTClient()
        packet = make_packet(uid=4242, vendor=0x2000, callsign="NOBODY")
        svc._on_message(None, None, MockMQTTMessage(packet))
        consume_queue(svc)
        assert len(svc._buffer) == 0
        assert svc.loop_packets == 1


class TestHeaderRewrite:
    def test_rewrite_fields(self, service, make_packet):
        packet = make_packet(uid=42, callsign="BD8BOJ", vendor=0x1111)
        parsed = PacketParser.parse(packet)
        rewritten = service._rewrite_packet(parsed)
        new = PacketParser.parse(rewritten)

        assert new.header.vendor == 0x2000
        assert new.header.callsign == "RE>BD8BOJ"
        assert new.header.uid == 65535  # 默认重放 UID（D8：防客户端自过滤）
        assert new.header.timestamp >= parsed.header.timestamp
        # stream_begin_utc 更新为回放时刻（新流，非原发送者值）
        assert new.header.stream_begin_utc != parsed.header.stream_begin_utc
        assert new.header.stream_begin_utc == new.header.timestamp

    def test_frames_and_crc_preserved(self, service, make_packet):
        """帧区字节与 CRC 不变（重放后 CRC 仍有效 → parse 成功）"""
        packet = make_packet(n_frames=3)
        parsed = PacketParser.parse(packet)
        rewritten = service._rewrite_packet(parsed)
        new = PacketParser.parse(rewritten)  # CRC 校验通过
        assert new.frames == parsed.frames

    def test_rewrite_with_uid(self, service_config, make_packet):
        service_config['echo']['uid'] = 999
        svc = EchoService(service_config)
        parsed = PacketParser.parse(make_packet(uid=42))
        new = PacketParser.parse(svc._rewrite_packet(parsed))
        assert new.header.uid == 999


class TestInvalidPackets:
    def test_short_packet_tolerated(self, service):
        service._on_message(None, None, MockMQTTMessage(b"\x01" * 30))
        assert service.invalid_packets == 1
        assert len(service._buffer) == 0

    def test_corrupted_crc_tolerated(self, service, make_packet):
        packet = bytearray(make_packet())
        packet[-1] ^= 0xFF
        service._on_message(None, None, MockMQTTMessage(bytes(packet)))
        assert service.invalid_packets == 1

    def test_service_keeps_running_after_invalid(self, service, make_packet):
        service._on_message(None, None, MockMQTTMessage(b"junk"))
        service._on_message(None, None, MockMQTTMessage(make_packet()))
        consume_queue(service)
        assert service.invalid_packets == 1
        assert len(service._buffer) == 1


class TestTimeoutReplay:
    def test_no_timeout_no_replay(self, service, make_packet):
        service._on_message(None, None, MockMQTTMessage(make_packet()))
        consume_queue(service)  # 消费入队包，但不触发超时回放
        assert service.mqtt_client.published == []
        assert len(service._buffer) == 1

    def test_timeout_triggers_replay(self, service, make_packet):
        for _ in range(3):
            service._on_message(None, None, MockMQTTMessage(make_packet()))
        consume_queue(service)
        trigger_replay(service)  # 队列空 → 超时回放
        assert len(service.mqtt_client.published) == 3
        assert len(service._buffer) == 0
        assert service.last_message_time is None

    def test_replayed_content(self, service, make_packet):
        service._on_message(None, None, MockMQTTMessage(make_packet(uid=7, callsign="BG5ESN")))
        consume_queue(service)
        trigger_replay(service)
        topic, payload = service.mqtt_client.published[0]
        assert topic == "TEST/FMO/RAW"
        parsed = PacketParser.parse(payload)
        assert parsed.header.callsign == "RE>BG5ESN"
        assert parsed.header.vendor == 0x2000

    def test_replay_clears_state(self, service, make_packet):
        service._on_message(None, None, MockMQTTMessage(make_packet()))
        consume_queue(service)
        trigger_replay(service)
        trigger_replay(service)  # 空 buffer，无更多回放
        assert len(service.mqtt_client.published) == 1


class TestStreamBoundary:
    """超时回放语义（changes/003 fix-replay-timeout-and-sbu）"""

    def test_gap_shorter_than_timeout_no_replay(self, service, make_packet):
        """收包间隔 < 2s（timeout）→ 持续累积不回放"""
        stream = 1700000000000 & 0xFFFFFFFF
        for _ in range(4):
            service._on_message(None, None, MockMQTTMessage(
                make_packet(uid=42, stream_begin_utc=stream)))
        consume_queue(service)
        assert len(service.mqtt_client.published) == 0
        assert len(service._buffer) == 4

    def test_gap_exceeds_timeout_replays_all(self, service, make_packet):
        """停止收包超 2s → 整段回放并清空缓冲"""
        stream = 1700000000000 & 0xFFFFFFFF
        for _ in range(3):
            service._on_message(None, None, MockMQTTMessage(
                make_packet(uid=42, stream_begin_utc=stream)))
        consume_queue(service)
        trigger_replay(service)
        assert len(service.mqtt_client.published) == 3
        assert len(service._buffer) == 0
        assert service.last_message_time is None

    def test_replay_rewrites_stream_begin_utc(self, service, make_packet):
        """回放的包 stream_begin_utc 更新为回放时刻，非原发送者值"""
        stream = 1700000000000 & 0xFFFFFFFF
        service._on_message(None, None, MockMQTTMessage(
            make_packet(uid=42, stream_begin_utc=stream)))
        consume_queue(service)
        trigger_replay(service)
        _, payload = service.mqtt_client.published[0]
        new = PacketParser.parse(payload)
        assert new.header.stream_begin_utc != stream
        # 回放时 stream_begin_utc 与 timestamp 均为回放时刻（同一时刻）
        assert new.header.stream_begin_utc == new.header.timestamp

    def test_packets_arriving_during_replay_are_dropped(self, service, make_packet):
        """回放期间到达的包被丢弃，回放完成后重新缓存（不切分下一段）"""
        stream = 1700000000000 & 0xFFFFFFFF
        # 先积累 3 包
        for _ in range(3):
            service._on_message(None, None, MockMQTTMessage(
                make_packet(uid=42, stream_begin_utc=stream)))
        consume_queue(service)
        # 回放期间再到达 5 包（模拟回声播放期间用户按键，应被丢弃）
        for _ in range(5):
            service._on_message(None, None, MockMQTTMessage(
                make_packet(uid=42, stream_begin_utc=stream)))
        # 触发回放：回放前+drain 会清队列（丢弃这 5 包）
        trigger_replay(service)
        # 只回放了最初的 3 包
        assert len(service.mqtt_client.published) == 3
        # 回放后 buffer 空、队列空
        assert len(service._buffer) == 0
        assert service._queue.empty()


class TestEventLogIntegration:
    def test_events_written(self, service_config, make_packet):
        evlog = EventLog(service_config)
        service_config['event_log'] = service_config['event_log']
        svc = EchoService(service_config, event_log=evlog)
        svc.mqtt_client = MockMQTTClient()

        svc._on_message(None, None, MockMQTTMessage(make_packet()))
        consume_queue(svc)
        replayed = svc._rewrite_packet(svc._buffer[0][0])
        svc._on_message(None, None, MockMQTTMessage(replayed))
        consume_queue(svc)
        trigger_replay(svc)

        import json
        with open(service_config['event_log']['file'], encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
        names = [e["event"] for e in events]
        assert "stream_start" in names
        assert names.count("stream_start") == 1  # 仅首包一次
        assert "packet_received" in names
        assert "loop_detected" in names
        assert "replay_started" in names
        assert "replay_finished" in names
        # 所有行都有 ts 且 event 在第二位
        for e in events:
            assert list(e.keys())[:2] == ["ts", "event"]

    def test_stream_start_fields(self, service_config, make_packet):
        """stream_start 事件字段完整，且仅在新流首包触发"""
        evlog = EventLog(service_config)
        svc = EchoService(service_config, event_log=evlog)
        svc.mqtt_client = MockMQTTClient()

        packet = make_packet(uid=7, callsign="BD8BOJ")
        expected = PacketParser.parse(packet).header
        assert expected.stream_begin_utc == 1700000000000 & 0xFFFFFFFF  # 参数已透传
        svc._on_message(None, None, MockMQTTMessage(packet))
        svc._on_message(None, None, MockMQTTMessage(make_packet(uid=7, callsign="BD8BOJ")))
        consume_queue(svc)

        import json
        with open(service_config['event_log']['file'], encoding="utf-8") as f:
            events = [json.loads(line) for line in f if line.strip()]
        starts = [e for e in events if e["event"] == "stream_start"]
        assert len(starts) == 1
        s = starts[0]
        assert s["uid"] == 7
        assert s["callsign"] == "BD8BOJ"
        assert s["vendor"] == expected.vendor
        assert s["stream_begin_utc"] == expected.stream_begin_utc
        assert s["frames"] == expected.frame_num
