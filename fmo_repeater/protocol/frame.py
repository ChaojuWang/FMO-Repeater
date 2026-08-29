"""传输帧与编码语音帧

依据规范 §3、§4（全部小端）：

传输帧（8B 头）—— 消息包与编码语音帧之间的传输层单元：
    偏移  大小  字段
    0     2    index     帧序号，从 1 开始递增
    2     2    length    含本帧头在内的长度
    4     4    reserved  保留
    8     -    data      编码语音帧载荷

编码语音帧（8B 头）—— 一次编码的输出，自带 compressMode 与长度：
    偏移  大小  字段
    0     1    compress_mode  编码类型（0=PCM 预留, 1=OPUS, 2=RADPCM）
    1     2    length         含头在内的整个编码语音帧长度
    3     5    reserved       保留（编码参数扩展位）
    8     -    raw            编码载荷起始
"""

from __future__ import annotations

import struct

from .common import ProtocolError

# ---------------------------------------------------------------------------
# 编码类型（规范 §4.3）
# ---------------------------------------------------------------------------
COMPRESS_PCM = 0      # PCM（预留，不用于网络传输）
COMPRESS_OPUS = 1
COMPRESS_RADPCM = 2

#: compress_mode -> 帧时长（ms）；PCM 预留为 0
_DURATION_MS = {
    COMPRESS_PCM: 0,
    COMPRESS_OPUS: 40,
    COMPRESS_RADPCM: 80,
}


def frame_duration_ms(compress_mode: int) -> int:
    """返回编码类型对应的单帧时长（ms）"""
    try:
        return _DURATION_MS[compress_mode]
    except KeyError:
        raise ProtocolError(
            "bad_compress_mode", f"未知编码类型: {compress_mode}"
        ) from None


class EncodedVoiceFrame:
    """编码语音帧（8B 头 + 变长载荷）"""

    __slots__ = ("compress_mode", "reserved", "raw")

    HEADER_SIZE = 8

    _STRUCT = struct.Struct("<BH5s")

    def __init__(self, compress_mode: int, raw: bytes, reserved: bytes = b""):
        if compress_mode not in (COMPRESS_PCM, COMPRESS_OPUS, COMPRESS_RADPCM):
            raise ProtocolError(
                "bad_compress_mode", f"未知编码类型: {compress_mode}"
            )
        total = self.HEADER_SIZE + len(raw)
        if total > 0xFFFF:
            raise ProtocolError(
                "frame_too_long", f"编码语音帧超过 uint16 长度: {total}"
            )
        self.compress_mode = compress_mode
        self.raw = bytes(raw)
        self.reserved = bytes(reserved[:5]).ljust(5, b"\x00")

    @property
    def length(self) -> int:
        """含头总长（字节）"""
        return self.HEADER_SIZE + len(self.raw)

    @property
    def duration_ms(self) -> int:
        """该帧承载的语音时长（ms）"""
        return frame_duration_ms(self.compress_mode)

    @classmethod
    def from_bytes(cls, data: bytes) -> "EncodedVoiceFrame":
        """从字节流解析（可含后续多余数据，仅消费本帧）"""
        if len(data) < cls.HEADER_SIZE:
            raise ProtocolError(
                "too_short", f"编码语音帧数据不足 8B：实际 {len(data)}B"
            )
        compress_mode, length, reserved = cls._STRUCT.unpack(
            data[:cls.HEADER_SIZE]
        )
        if length < cls.HEADER_SIZE:
            raise ProtocolError(
                "bad_length", f"编码语音帧长度字段非法: {length}"
            )
        end = cls.HEADER_SIZE + (length - cls.HEADER_SIZE)
        if len(data) < end:
            raise ProtocolError(
                "bad_length",
                f"编码语音帧载荷不完整：声明 {length}B，实际 {len(data)}B",
            )
        return cls(compress_mode, data[cls.HEADER_SIZE:end], reserved)

    def to_bytes(self) -> bytes:
        return self._STRUCT.pack(
            self.compress_mode, self.length, self.reserved
        ) + self.raw

    def __repr__(self) -> str:
        return (
            f"EncodedVoiceFrame(compress_mode={self.compress_mode}, "
            f"length={self.length}, raw={len(self.raw)}B)"
        )

    def __eq__(self, other) -> bool:
        if not isinstance(other, EncodedVoiceFrame):
            return NotImplemented
        return self.to_bytes() == other.to_bytes()


class TransportFrame:
    """传输帧（8B 头 + 一个编码语音帧）"""

    __slots__ = ("index", "reserved", "encoded")

    HEADER_SIZE = 8

    _STRUCT = struct.Struct("<HH4s")

    def __init__(self, index: int, encoded: EncodedVoiceFrame, reserved: bytes = b""):
        total = self.HEADER_SIZE + encoded.length
        if total > 0xFFFF:
            raise ProtocolError(
                "frame_too_long", f"传输帧超过 uint16 长度: {total}"
            )
        self.index = index
        self.encoded = encoded
        self.reserved = bytes(reserved[:4]).ljust(4, b"\x00")

    @property
    def length(self) -> int:
        """含头总长（字节）"""
        return self.HEADER_SIZE + self.encoded.length

    @classmethod
    def from_bytes(cls, data: bytes) -> "TransportFrame":
        """从字节流解析（仅消费本帧，返回对象不带剩余数据）"""
        if len(data) < cls.HEADER_SIZE:
            raise ProtocolError(
                "too_short", f"传输帧数据不足 8B：实际 {len(data)}B"
            )
        index, length, reserved = cls._STRUCT.unpack(data[:cls.HEADER_SIZE])
        if length < cls.HEADER_SIZE:
            raise ProtocolError(
                "bad_length", f"传输帧长度字段非法: {length}"
            )
        end = length
        if len(data) < end:
            raise ProtocolError(
                "bad_length",
                f"传输帧载荷不完整：声明 {length}B，实际 {len(data)}B",
            )
        encoded = EncodedVoiceFrame.from_bytes(
            data[cls.HEADER_SIZE:end]
        )
        return cls(index, encoded, reserved)

    def to_bytes(self) -> bytes:
        return self._STRUCT.pack(
            self.index, self.length, self.reserved
        ) + self.encoded.to_bytes()

    def __repr__(self) -> str:
        return (
            f"TransportFrame(index={self.index}, length={self.length}, "
            f"encoded={self.encoded!r})"
        )

    def __eq__(self, other) -> bool:
        if not isinstance(other, TransportFrame):
            return NotImplemented
        return self.to_bytes() == other.to_bytes()
