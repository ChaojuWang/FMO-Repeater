"""消息包头（64B）解析与序列化

依据规范 §2.2（全部小端）：

    偏移  大小  字段
    0     2    version          包版本，当前为 1
    2     4    vendor           厂家标识
    6     4    uid              发送者用户 ID
    10    12   callsign         呼号，不足补 0
    22    4    stream_begin_utc 语音流起始时间（UTC ms）
    26    4    timestamp        本包生成时间（UTC ms）
    30    4    length           整个消息包长度（含头）
    34    2    frame_num        本包内传输帧个数
    36    4    checksum         CRC32，仅覆盖帧区
    40    1    smeter           S 表强度
    41    4    srv_uid          当前连接服务器 UID
    45    19   reserved         扩展区，总头长固定 64B
"""

from __future__ import annotations

import struct

from .common import ProtocolError

#: struct 格式（小端）：<H + I + I + 12s + I + I + I + H + I + B + I + 19s
_HEADER_STRUCT = struct.Struct("<HII12sIIIHIBI19s")

#: 头部总长（字节）
HEADER_SIZE = _HEADER_STRUCT.size  # 64

#: 当前协议版本
VERSION = 1

#: 呼号字段宽度（字节）
CALLSIGN_SIZE = 12


class MessageHeader:
    """FMO 消息包 64 字节头部"""

    __slots__ = (
        "version", "vendor", "uid", "callsign",
        "stream_begin_utc", "timestamp", "length", "frame_num",
        "checksum", "smeter", "srv_uid", "reserved",
    )

    def __init__(
        self,
        version: int = VERSION,
        vendor: int = 0,
        uid: int = 0,
        callsign: str = "",
        stream_begin_utc: int = 0,
        timestamp: int = 0,
        length: int = HEADER_SIZE,
        frame_num: int = 0,
        checksum: int = 0,
        smeter: int = 0,
        srv_uid: int = 0,
        reserved: bytes = b"",
    ):
        self.version = version
        self.vendor = vendor
        self.uid = uid
        self.callsign = callsign
        self.stream_begin_utc = stream_begin_utc
        self.timestamp = timestamp
        self.length = length
        self.frame_num = frame_num
        self.checksum = checksum
        self.smeter = smeter
        self.srv_uid = srv_uid
        self.reserved = reserved

    # ------------------------------------------------------------------
    # 序列化 / 反序列化
    # ------------------------------------------------------------------

    @classmethod
    def from_bytes(cls, data: bytes) -> "MessageHeader":
        """从字节流解析 64B 头部

        Args:
            data: 至少 64 字节

        Returns:
            MessageHeader

        Raises:
            ProtocolError: 数据不足 64B（reason='too_short'）
        """
        if len(data) < HEADER_SIZE:
            raise ProtocolError(
                "too_short",
                f"数据不足 64B：实际 {len(data)}B",
            )
        (
            version, vendor, uid, callsign_bytes,
            stream_begin_utc, timestamp, length, frame_num,
            checksum, smeter, srv_uid, reserved,
        ) = _HEADER_STRUCT.unpack(data[:HEADER_SIZE])

        callsign = callsign_bytes.rstrip(b"\x00").decode("utf-8", errors="replace")
        return cls(
            version=version, vendor=vendor, uid=uid, callsign=callsign,
            stream_begin_utc=stream_begin_utc, timestamp=timestamp,
            length=length, frame_num=frame_num, checksum=checksum,
            smeter=smeter, srv_uid=srv_uid, reserved=reserved,
        )

    def to_bytes(self) -> bytes:
        """序列化为 64 字节"""
        callsign = self.callsign.encode("utf-8")[:CALLSIGN_SIZE]
        callsign = callsign.ljust(CALLSIGN_SIZE, b"\x00")
        reserved = bytes(self.reserved[:19]).ljust(19, b"\x00")
        return _HEADER_STRUCT.pack(
            self.version, self.vendor, self.uid, callsign,
            self.stream_begin_utc, self.timestamp, self.length,
            self.frame_num, self.checksum, self.smeter, self.srv_uid,
            reserved,
        )

    def copy_with(self, **fields) -> "MessageHeader":
        """返回替换指定字段后的新头部（未指定的字段保持原值）"""
        import copy

        new = copy.copy(self)
        for key, value in fields.items():
            if not hasattr(new, key):
                raise AttributeError(f"MessageHeader 没有属性 '{key}'")
            setattr(new, key, value)
        return new

    # ------------------------------------------------------------------
    # 工具
    # ------------------------------------------------------------------

    def __repr__(self) -> str:
        return (
            f"MessageHeader(version={self.version}, vendor={self.vendor:#06x}, "
            f"uid={self.uid}, callsign='{self.callsign}', "
            f"stream_begin_utc={self.stream_begin_utc}, timestamp={self.timestamp}, "
            f"length={self.length}, frame_num={self.frame_num}, "
            f"checksum={self.checksum:#010x}, smeter={self.smeter}, "
            f"srv_uid={self.srv_uid})"
        )

    def __eq__(self, other) -> bool:
        if not isinstance(other, MessageHeader):
            return NotImplemented
        return self.to_bytes() == other.to_bytes()
