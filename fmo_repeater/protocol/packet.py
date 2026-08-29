"""消息包组包（PacketBuilder）与拆包（PacketParser）

依据规范 §2、§3：
- 消息包 = 64B 消息头 + N 个传输帧
- checkSum 为 CRC32，仅覆盖帧区（头部 64B 之后的所有字节）
- 聚合规则（§3.3）：追加后超过 MTU=1400B，或聚合时长达到 250ms，
  结束当前消息包并发布
"""

from __future__ import annotations

import time
import zlib
from dataclasses import dataclass, field
from typing import List, Optional

from .common import MTU, AGGREGATION_MS, ProtocolError
from .header import HEADER_SIZE, MessageHeader
from .frame import TransportFrame, EncodedVoiceFrame


def frame_region_checksum(frames_bytes: bytes) -> int:
    """计算帧区 CRC32（zlib.crc32，规范 §2.2 checkSum 字段）"""
    return zlib.crc32(frames_bytes) & 0xFFFFFFFF


@dataclass
class ParsedPacket:
    """拆包产物：消息头 + 有序传输帧列表"""

    header: MessageHeader
    frames: List[TransportFrame] = field(default_factory=list)

    @property
    def data(self) -> bytes:
        """原始消息包字节（重建）"""
        return self.header.to_bytes() + b"".join(
            f.to_bytes() for f in self.frames
        )


class PacketParser:
    """消息包解析器（无状态，可复用）"""

    @staticmethod
    def parse(data: bytes) -> ParsedPacket:
        """解析并校验一条完整消息包

        校验顺序：
        1. 长度 >= 64B 且 header.length == len(data)
        2. CRC32(帧区) == header.checksum
        3. 帧区恰好切分为 frame_num 个传输帧，index 连续递增

        Raises:
            ProtocolError: reason ∈ {'too_short','bad_length','bad_checksum',
                           'bad_frame_num','bad_index','bad_length_frames'}
        """
        if len(data) < HEADER_SIZE:
            raise ProtocolError(
                "too_short", f"消息包不足 64B：实际 {len(data)}B"
            )
        header = MessageHeader.from_bytes(data)
        if header.length != len(data):
            raise ProtocolError(
                "bad_length",
                f"头部长度字段 {header.length} 与实际包长 {len(data)} 不符",
            )

        frames_region = data[HEADER_SIZE:]
        crc = frame_region_checksum(frames_region)
        if crc != header.checksum:
            raise ProtocolError(
                "bad_checksum",
                f"CRC 不符：期望 {header.checksum:#010x}，实际 {crc:#010x}",
            )

        frames: List[TransportFrame] = []
        offset = 0
        for i in range(header.frame_num):
            if offset >= len(frames_region):
                raise ProtocolError(
                    "bad_frame_num",
                    f"帧区仅含 {i} 帧，头部声明 {header.frame_num} 帧",
                )
            try:
                tf = TransportFrame.from_bytes(frames_region[offset:])
            except ProtocolError as e:
                raise ProtocolError(
                    "bad_length_frames", f"第 {i + 1} 帧解析失败: {e}"
                ) from e
            if tf.index != i + 1:
                raise ProtocolError(
                    "bad_index",
                    f"第 {i + 1} 帧 index 应为 {i + 1}，实际 {tf.index}",
                )
            frames.append(tf)
            offset += tf.length

        if offset != len(frames_region):
            raise ProtocolError(
                "bad_frame_num",
                f"帧区存在 {len(frames_region) - offset}B 残余数据",
            )

        return ParsedPacket(header=header, frames=frames)


class PacketBuilder:
    """消息包聚合构造器

    按规范 §3.3 聚合：追加编码语音帧后若总长超过 MTU=1400B，
    或聚合时长达到 250ms，则封包返回完整消息包字节并重置缓冲。

    用法::

        builder = PacketBuilder(vendor=0x2000, uid=123, callsign='BD8BOJ')
        for frame in encoded_frames:
            packet = builder.add_frame(frame)
            if packet is not None:
                publish(packet)   # 已封包
        packet = builder.flush()   # 流结束，冲刷剩余
    """

    def __init__(
        self,
        vendor: int,
        uid: int,
        callsign: str,
        srv_uid: int = 0,
        smeter: int = 0,
    ):
        self.vendor = vendor
        self.uid = uid
        self.callsign = callsign
        self.srv_uid = srv_uid
        self.smeter = smeter

        self._frames: List[EncodedVoiceFrame] = []
        self._stream_begin_utc: Optional[int] = None

    # ------------------------------------------------------------------

    def _total_size(self, extra: EncodedVoiceFrame) -> int:
        """追加 extra 后的整包大小（64B 头 + 帧区）"""
        frames_size = sum(8 + f.length for f in self._frames)
        return HEADER_SIZE + frames_size + 8 + extra.length

    def _aggregated_ms(self, extra: EncodedVoiceFrame) -> int:
        return sum(f.duration_ms for f in self._frames) + extra.duration_ms

    def add_frame(self, encoded: EncodedVoiceFrame) -> Optional[bytes]:
        """追加一个编码语音帧

        Returns:
            bytes: 触发聚合上限时返回完整消息包（并重置缓冲），否则 None
        """
        would_exceed_mtu = self._total_size(encoded) > MTU
        would_exceed_time = self._aggregated_ms(encoded) > AGGREGATION_MS

        # 空缓冲仍超限时（单帧过大/过长），必须强制封包避免死锁
        force = not self._frames

        if (would_exceed_mtu or would_exceed_time) and not force:
            packet = self._build()
            self._frames = [encoded]
            self._stream_begin_utc = None
            return packet

        self._frames.append(encoded)
        if self._stream_begin_utc is None:
            self._stream_begin_utc = _utc_ms()
        if would_exceed_mtu or would_exceed_time:
            # 单帧即超限：立即封包
            return self._build_and_reset()
        return None

    def flush(self) -> Optional[bytes]:
        """冲刷剩余缓冲（流结束时调用）"""
        if not self._frames:
            return None
        return self._build_and_reset()

    # ------------------------------------------------------------------

    def _build_and_reset(self) -> bytes:
        packet = self._build()
        self._frames = []
        self._stream_begin_utc = None
        return packet

    def _build(self) -> bytes:
        """将当前缓冲封为完整消息包字节"""
        assert self._frames, "缓冲为空"
        if self._stream_begin_utc is None:
            self._stream_begin_utc = _utc_ms()

        transport_frames = [
            TransportFrame(index=i + 1, encoded=f)
            for i, f in enumerate(self._frames)
        ]
        frames_bytes = b"".join(tf.to_bytes() for tf in transport_frames)

        header = MessageHeader(
            vendor=self.vendor,
            uid=self.uid,
            callsign=self.callsign,
            stream_begin_utc=self._stream_begin_utc,
            timestamp=_utc_ms(),
            length=HEADER_SIZE + len(frames_bytes),
            frame_num=len(transport_frames),
            checksum=frame_region_checksum(frames_bytes),
            smeter=self.smeter,
            srv_uid=self.srv_uid,
        )
        return header.to_bytes() + frames_bytes


def _utc_ms() -> int:
    """当前 UTC 毫秒（uint32 回绕由协议字段宽度决定，透传即可）"""
    return int(time.time() * 1000) & 0xFFFFFFFF
