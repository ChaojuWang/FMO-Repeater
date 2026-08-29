"""消息头（64B）测试"""

import pytest

from fmo_repeater.protocol import HEADER_SIZE, VERSION, MessageHeader, ProtocolError


class TestHeaderRoundtrip:
    def test_roundtrip_all_fields(self):
        h = MessageHeader(
            version=1, vendor=0x2000, uid=123, callsign="FMOTEST",
            stream_begin_utc=1700000000000 & 0xFFFFFFFF,
            timestamp=1700000001234 & 0xFFFFFFFF,
            length=408, frame_num=1, checksum=0xDEADBEEF,
            smeter=7, srv_uid=9, reserved=b"\x01\x02",
        )
        data = h.to_bytes()
        assert len(data) == HEADER_SIZE == 64
        h2 = MessageHeader.from_bytes(data)
        assert h2.version == 1
        assert h2.vendor == 0x2000
        assert h2.uid == 123
        assert h2.callsign == "FMOTEST"
        assert h2.stream_begin_utc == 1700000000000 & 0xFFFFFFFF
        assert h2.timestamp == 1700000001234 & 0xFFFFFFFF
        assert h2.length == 408
        assert h2.frame_num == 1
        assert h2.checksum == 0xDEADBEEF
        assert h2.smeter == 7
        assert h2.srv_uid == 9
        assert h2 == h

    def test_defaults(self):
        h = MessageHeader()
        assert h.version == VERSION
        assert h.length == HEADER_SIZE
        assert h.frame_num == 0
        assert h.callsign == ""


class TestHeaderLayout:
    def test_field_offsets(self):
        """字段偏移必须与规范 §2.2 一致（小端）"""
        import struct
        data = MessageHeader(
            version=1, vendor=0xAABBCCDD, uid=0x11223344,
            callsign="AB", stream_begin_utc=0x01020304,
            timestamp=0x05060708, length=0x1122, frame_num=0x3344,
            checksum=0x55667788, smeter=0x99, srv_uid=0x0A0B0C0D,
        ).to_bytes()
        assert struct.unpack_from("<H", data, 0)[0] == 1          # version @0
        assert struct.unpack_from("<I", data, 2)[0] == 0xAABBCCDD  # vendor @2
        assert struct.unpack_from("<I", data, 6)[0] == 0x11223344  # uid @6
        assert data[10:12] == b"AB"                                 # callsign @10
        assert struct.unpack_from("<I", data, 22)[0] == 0x01020304  # streamBegin @22
        assert struct.unpack_from("<I", data, 26)[0] == 0x05060708  # timestamp @26
        assert struct.unpack_from("<I", data, 30)[0] == 0x1122      # len @30
        assert struct.unpack_from("<H", data, 34)[0] == 0x3344      # frameNum @34
        assert struct.unpack_from("<I", data, 36)[0] == 0x55667788  # checkSum @36
        assert data[40] == 0x99                                      # smeter @40
        assert struct.unpack_from("<I", data, 41)[0] == 0x0A0B0C0D  # srvUID @41
        assert data[45:64] == b"\x00" * 19                           # reserved @45

    def test_callsign_truncation_and_padding(self):
        h = MessageHeader(callsign="FMOTEST-LONGCALLSIGN")
        assert len(h.to_bytes()) == HEADER_SIZE
        h2 = MessageHeader.from_bytes(h.to_bytes())
        # 12 字节截断："FMOTEST-LONG"
        assert h2.callsign == "FMOTEST-LONG"

    def test_callsign_multibyte(self):
        h = MessageHeader(callsign="乙1")
        data = h.to_bytes()
        h2 = MessageHeader.from_bytes(data)
        # '乙1' UTF-8 为 4 字节，未超 12B
        assert h2.callsign == "乙1"


class TestHeaderErrors:
    def test_too_short(self):
        with pytest.raises(ProtocolError) as e:
            MessageHeader.from_bytes(b"\x00" * 63)
        assert e.value.reason == "too_short"

    def test_exact_64_ok_with_trailing(self):
        h = MessageHeader(uid=5)
        # from_bytes 容忍多余数据（只消费 64B）
        h2 = MessageHeader.from_bytes(h.to_bytes() + b"XXXX")
        assert h2.uid == 5


class TestCopyWith:
    def test_copy_with_replaces(self):
        h = MessageHeader(uid=1, callsign="A", vendor=0x1111)
        h2 = h.copy_with(uid=2, callsign="B")
        assert h2.uid == 2 and h2.callsign == "B"
        # 原对象不变
        assert h.uid == 1 and h.callsign == "A"
        # 未指定字段保持
        assert h2.vendor == 0x1111

    def test_copy_with_unknown_field(self):
        with pytest.raises(AttributeError):
            MessageHeader().copy_with(no_such_field=1)
