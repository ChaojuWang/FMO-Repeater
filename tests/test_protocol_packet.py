"""帧结构（传输帧/编码语音帧）与消息包（组包/拆包/CRC/聚合）测试"""

import pytest

from fmo_repeater.protocol import (
    AGGREGATION_MS,
    COMPRESS_OPUS,
    COMPRESS_RADPCM,
    EncodedVoiceFrame,
    HEADER_SIZE,
    MTU,
    PacketBuilder,
    PacketParser,
    ProtocolError,
    TransportFrame,
    frame_region_checksum,
    frame_duration_ms,
)
from fmo_repeater.codecs import RadpcmEncoder, opus_is_available
from conftest import sine_pcm16


class TestVendorFrame:
    def test_compress_mode_duration(self):
        assert frame_duration_ms(COMPRESS_OPUS) == 40
        assert frame_duration_ms(COMPRESS_RADPCM) == 80
        with pytest.raises(ProtocolError):
            frame_duration_ms(99)

    def test_encoded_voice_frame_roundtrip(self):
        evf = EncodedVoiceFrame(COMPRESS_RADPCM, b"\x01\x02\x03", reserved=b"\xAA")
        data = evf.to_bytes()
        assert len(data) == 8 + 3
        evf2 = EncodedVoiceFrame.from_bytes(data)
        assert evf2 == evf
        assert evf2.reserved == b"\xAA\x00\x00\x00\x00"

    def test_encoded_voice_frame_from_trailing_data(self):
        evf = EncodedVoiceFrame(COMPRESS_OPUS, b"XYZ")
        # 尾部多余字节不影响解析
        evf2 = EncodedVoiceFrame.from_bytes(evf.to_bytes() + b"next-frame...")
        assert evf2 == evf

    def test_encoded_voice_frame_bad_mode(self):
        with pytest.raises(ProtocolError):
            EncodedVoiceFrame(77, b"")

    def test_transport_frame_roundtrip(self):
        evf = EncodedVoiceFrame(COMPRESS_RADPCM, b"\x00" * 328)
        tf = TransportFrame(index=3, encoded=evf)
        data = tf.to_bytes()
        assert data[:2] == b"\x03\x00"  # index=3 小端
        assert data[2:4] == (8 + 336).to_bytes(2, "little")
        tf2 = TransportFrame.from_bytes(data)
        assert tf2 == tf

    def test_transport_frame_too_short(self):
        with pytest.raises(ProtocolError):
            TransportFrame.from_bytes(b"\x00" * 7)


class TestParser:
    def test_parse_valid(self, make_packet):
        packet = make_packet(uid=42, callsign="FMOTEST", n_frames=2)
        parsed = PacketParser.parse(packet)
        assert parsed.header.uid == 42
        assert parsed.header.callsign == "FMOTEST"
        assert parsed.header.frame_num == 2
        assert len(parsed.frames) == 2
        assert [tf.index for tf in parsed.frames] == [1, 2]
        assert all(
            tf.encoded.compress_mode == COMPRESS_RADPCM for tf in parsed.frames
        )
        # 重建字节一致
        assert parsed.data == packet

    def test_reject_too_short(self):
        with pytest.raises(ProtocolError) as e:
            PacketParser.parse(b"\x00" * 10)
        assert e.value.reason == "too_short"

    def test_reject_bad_length(self, make_packet):
        packet = make_packet()
        tampered = bytearray(packet)
        tampered[30] ^= 0xFF  # 破坏 length 字段
        with pytest.raises(ProtocolError) as e:
            PacketParser.parse(bytes(tampered))
        assert e.value.reason == "bad_length"

    def test_reject_bad_checksum(self, make_packet):
        packet = make_packet()
        tampered = bytearray(packet)
        tampered[-1] ^= 0xFF  # 破坏帧区 → CRC 失配
        with pytest.raises(ProtocolError) as e:
            PacketParser.parse(bytes(tampered))
        assert e.value.reason == "bad_checksum"

    def test_reject_bad_index(self, make_packet):
        packet = make_packet(n_frames=2)
        tampered = bytearray(packet)
        # 第二个传输帧的 index 字段（帧区第 2 帧头 2 字节）破坏
        # 帧区布局: 64 头 + 帧1(8+336) + 帧2头
        off = HEADER_SIZE + (8 + 336) + 8
        tampered[off - 8: off - 6] = b"\x09\x00"  # index 改为 9
        # 破坏 index 后帧区字节变化，CRC 需同步重算才能到达 index 校验
        region = bytes(tampered[HEADER_SIZE:])
        crc = frame_region_checksum(region)
        tampered[36:40] = crc.to_bytes(4, "little")
        with pytest.raises(ProtocolError) as e:
            PacketParser.parse(bytes(tampered))
        assert e.value.reason == "bad_index"

    def test_reject_residual_frames(self, make_packet):
        packet = make_packet(n_frames=1)
        parsed = PacketParser.parse(packet)
        # 追加一段合法帧但 frame_num/length 不变 → header.length 失配
        extra = parsed.frames[0].to_bytes()
        region = packet[HEADER_SIZE:] + extra
        tampered = bytearray(packet)
        crc = frame_region_checksum(region)
        tampered[36:40] = crc.to_bytes(4, "little")
        tampered.extend(extra)
        with pytest.raises(ProtocolError) as e:
            PacketParser.parse(bytes(tampered))
        assert e.value.reason == "bad_length"  # header.length 仍为旧值

    def test_reject_residual_with_fixed_length(self, make_packet):
        """修正 length 与 CRC 后仅 frame_num 不符 → bad_frame_num（残余）"""
        packet = make_packet(n_frames=1)
        parsed = PacketParser.parse(packet)
        extra = parsed.frames[0].to_bytes()
        region = packet[HEADER_SIZE:] + extra
        tampered = bytearray(packet)
        import struct as _s
        tampered[30:34] = _s.pack("<I", len(packet) + len(extra))  # 修正 length
        tampered[36:40] = frame_region_checksum(region).to_bytes(4, "little")
        tampered.extend(extra)
        with pytest.raises(ProtocolError) as e:
            PacketParser.parse(bytes(tampered))
        assert e.value.reason == "bad_frame_num"


class TestBuilderAggregation:
    def _radpcm_frames(self, n):
        enc = RadpcmEncoder(frame_index_start=0)
        return [
            enc.encode(sine_pcm16(640, 9000, 300, i * 640)) for i in range(n)
        ]

    def test_radpcm_max_3_frames_per_packet(self):
        """250ms 上限：RADPCM 80ms/帧 → 每包 ≤3 帧"""
        frames = self._radpcm_frames(7)
        builder = PacketBuilder(vendor=0x2000, uid=1, callsign="T")
        packets, sizes = [], []
        for f in frames:
            p = builder.add_frame(EncodedVoiceFrame(COMPRESS_RADPCM, f))
            if p is not None:
                packets.append(p)
        tail = builder.flush()
        if tail:
            packets.append(tail)
        sizes = [PacketParser.parse(p).header.frame_num for p in packets]
        assert sizes == [3, 3, 1]
        assert all(len(p) <= MTU for p in packets)

    @pytest.mark.skipif(not opus_is_available(), reason="opuslib 不可用")
    def test_opus_max_6_frames_per_packet(self):
        """250ms 上限：OPUS 40ms/帧 → 每包 ≤6 帧"""
        from fmo_repeater.codecs import OpusEncoder
        enc = OpusEncoder()
        builder = PacketBuilder(vendor=0x2000, uid=1, callsign="T")
        packets = []
        for i in range(7):
            pcm = sine_pcm16(320, 8000, 440, i * 320)
            p = builder.add_frame(EncodedVoiceFrame(COMPRESS_OPUS, enc.encode(pcm)))
            if p is not None:
                packets.append(p)
        tail = builder.flush()
        if tail:
            packets.append(tail)
        sizes = [PacketParser.parse(p).header.frame_num for p in packets]
        assert sizes == [6, 1]

    def test_mtu_enforcement_with_large_opus_frames(self):
        """构造大载荷帧验证 MTU 优先触发（1400B 上限）"""
        big_raw = b"\x00" * 600  # 8+600 每帧
        builder = PacketBuilder(vendor=0x2000, uid=1, callsign="T")
        packets = []
        for i in range(6):
            p = builder.add_frame(EncodedVoiceFrame(COMPRESS_OPUS, big_raw))
            if p is not None:
                packets.append(p)
        tail = builder.flush()
        if tail:
            packets.append(tail)
        assert len(packets) >= 2
        assert all(len(p) <= MTU for p in packets)
        # 64 + n*(8+8+600) ≤ 1400 → n ≤ 2（632*2=1264+64=1328 ≤1400；3 帧 1896 超限）
        sizes = [PacketParser.parse(p).header.frame_num for p in packets]
        assert all(s <= 2 for s in sizes)

    def test_builder_flush_empty(self):
        builder = PacketBuilder(vendor=0x2000, uid=1, callsign="T")
        assert builder.flush() is None

    def test_stream_begin_utc_kept_across_packet(self):
        """stream_begin_utc 为缓冲首帧时刻，封包后重置"""
        frames = self._radpcm_frames(4)
        builder = PacketBuilder(vendor=0x2000, uid=1, callsign="T")
        p1 = None
        for f in frames[:3]:
            p1 = builder.add_frame(EncodedVoiceFrame(COMPRESS_RADPCM, f)) or p1
        p1 = builder.flush()
        parsed1 = PacketParser.parse(p1)
        # 再编一帧，新包 stream_begin_utc 应为新时刻（≥ 旧值）
        builder.add_frame(EncodedVoiceFrame(COMPRESS_RADPCM, frames[3]))
        p2 = builder.flush()
        parsed2 = PacketParser.parse(p2)
        assert parsed2.header.stream_begin_utc >= parsed1.header.stream_begin_utc


class TestRoundtrip:
    def test_multi_packet_roundtrip(self):
        enc = RadpcmEncoder(frame_index_start=0)
        builder = PacketBuilder(vendor=0x2001, uid=77, callsign="FMOTEST", srv_uid=5)
        original_frames = []
        packets = []
        for i in range(8):
            pcm = sine_pcm16(640, 15000, 500, i * 640)
            raw = enc.encode(pcm)
            original_frames.append(raw)
            p = builder.add_frame(EncodedVoiceFrame(COMPRESS_RADPCM, raw))
            if p is not None:
                packets.append(p)
        tail = builder.flush()
        if tail:
            packets.append(tail)

        assert len(packets) == 3  # 3+3+2
        recovered = []
        for p in packets:
            parsed = PacketParser.parse(p)
            assert parsed.header.vendor == 0x2001
            assert parsed.header.uid == 77
            for tf in parsed.frames:
                recovered.append(tf.encoded.raw)
        assert recovered == original_frames
