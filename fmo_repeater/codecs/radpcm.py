"""RADPCM 编解码器（IMA ADPCM 变体）

依据规范 §5.2、§6.2：

帧结构（固定 328B = 8B 帧头 + 320B 数据区）::

    偏移  大小  字段
    0     2    frame_index   帧序号（0 起，uint16 循环）
    2     2    recover_pcm   编码开始时预测器值
    4     1    step_index    编码开始时步长索引（0-88）
    5     1    reserved      保留
    6     2    adpcm_bytes   数据区有效字节数（=320）
    8     320  data          每字节 2 个 4-bit 样本（高 nibble 在前），共 640 样本（80ms）

- 采样率 8000Hz / 16-bit 有符号 / 单声道
- 编码器侧直流抑制：y[n] = x[n] - x[n-1] + R·y[n-1]，R=0.999（Q15 32735），
  解码器做逆滤波还原（设计决策 D2）
- 丢包恢复：解码器维护 last_frame，按规范四情形处理
- adpcmBytes 兼容：新实现按 uint16 读写；旧包 8-bit 字段（线值 64）以低字节判定
"""

from __future__ import annotations

import struct

from ..protocol.common import ProtocolError
from ..protocol.frame import ProtocolError as _PE  # noqa: F401  (re-export convenience)

# ---------------------------------------------------------------------------
# 常量
# ---------------------------------------------------------------------------
SAMPLE_RATE = 8000
SAMPLES_PER_FRAME = 640          # 80ms @ 8kHz
FRAME_MS = 80
DATA_BYTES = 320                 # 640 样本 × 4bit
FRAME_BYTES = 8 + DATA_BYTES     # 328

#: 直流抑制系数 R = 0.999 的 Q15 表示
DC_R_Q15 = 32735

#: IMA 步长表（89 级，规范 §6.2 原文）
IMA_STEP_TABLE = (
    7, 8, 9, 10, 11, 12, 13, 14, 16, 17, 19, 21, 23, 25, 28, 31, 34,
    37, 41, 45, 50, 55, 60, 66, 73, 80, 88, 97, 107, 118, 130, 143,
    157, 173, 190, 209, 230, 253, 279, 307, 337, 371, 408, 449, 494,
    544, 598, 658, 724, 796, 876, 963, 1060, 1166, 1282, 1411, 1552,
    1707, 1878, 2066, 2272, 2499, 2749, 3024, 3327, 3660, 4026, 4428,
    4871, 5358, 5894, 6484, 7132, 7845, 8630, 9493, 10442, 11487,
    12635, 13899, 15289, 16818, 18500, 20350, 22385, 24623, 27086,
    29794, 32767,
)

#: IMA 索引表（16 项，规范 §6.2 原文）
IMA_INDEX_TABLE = (-1, -1, -1, -1, 2, 4, 6, 8, -1, -1, -1, -1, 2, 4, 6, 8)

#: 丢包恢复中的"首帧"哨兵值
FIRST_FRAME_SENTINEL = 0xFFFF

_FRAME_STRUCT = struct.Struct("<HhBBH320s")

_MIN_PCM = -32768
_MAX_PCM = 32767


def _clamp_pcm(v: int) -> int:
    return _MIN_PCM if v < _MIN_PCM else _MAX_PCM if v > _MAX_PCM else v


# ---------------------------------------------------------------------------
# 帧结构解析/构造
# ---------------------------------------------------------------------------
class RadpcmFrameInfo:
    """RADPCM 帧头信息（不含数据区）"""

    __slots__ = ("frame_index", "recover_pcm", "step_index", "adpcm_bytes", "legacy")

    def __init__(
        self,
        frame_index: int,
        recover_pcm: int,
        step_index: int,
        adpcm_bytes: int,
        legacy: bool = False,
    ):
        self.frame_index = frame_index
        self.recover_pcm = recover_pcm
        self.step_index = step_index
        self.adpcm_bytes = adpcm_bytes
        self.legacy = legacy


def parse_frame(frame: bytes) -> tuple[RadpcmFrameInfo, bytes]:
    """解析 328B RADPCM 帧为（帧头信息, 数据区字节）

    adpcmBytes 兼容性（规范 §5.2 注记）：新实现 16-bit 读写（=320）；
    旧实现 8-bit 字段线值 64——以低字节判定。
    """
    if len(frame) < FRAME_BYTES:
        raise ProtocolError(
            "too_short", f"RADPCM 帧不足 {FRAME_BYTES}B：实际 {len(frame)}B"
        )
    frame_index, recover_pcm, step_index, _reserved, adpcm_raw, _data = \
        _FRAME_STRUCT.unpack_from(frame, 0)
    data = frame[8:8 + DATA_BYTES]

    adpcm_low = adpcm_raw & 0xFF
    adpcm_high = (adpcm_raw >> 8) & 0xFF
    if adpcm_raw == DATA_BYTES:
        # 新格式：uint16 = 320
        legacy = False
        adpcm_bytes = DATA_BYTES
    elif adpcm_high == 0 and adpcm_low == 64:
        # 旧格式：8-bit 字段，线值 64（低字节）且高字节为填充
        legacy = True
        adpcm_bytes = DATA_BYTES
    else:
        raise ProtocolError(
            "bad_length",
            f"adpcmBytes 非法: {adpcm_raw}（既非 320 也非旧格式 64）",
        )

    info = RadpcmFrameInfo(
        frame_index=frame_index,
        recover_pcm=recover_pcm,
        step_index=step_index,
        adpcm_bytes=adpcm_bytes,
        legacy=legacy,
    )
    return info, data


def build_frame(
    frame_index: int,
    recover_pcm: int,
    step_index: int,
    data: bytes,
) -> bytes:
    """构造 328B RADPCM 帧（adpcmBytes 按 16-bit 语义 = 320）"""
    if len(data) != DATA_BYTES:
        raise ProtocolError(
            "bad_length", f"RADPCM 数据区必须 {DATA_BYTES}B：实际 {len(data)}B"
        )
    return _FRAME_STRUCT.pack(
        frame_index, recover_pcm, step_index, 0, DATA_BYTES, data
    )


# ---------------------------------------------------------------------------
# IMA ADPCM 核心
# ---------------------------------------------------------------------------
def _ima_encode_sample(pcm: int, predictor: int, step_index: int) -> tuple[int, int, int]:
    """编码单样本，返回 (nibble, predictor, step_index)"""
    step = IMA_STEP_TABLE[step_index]
    diff = pcm - predictor
    code = 0
    if diff < 0:
        code = 8
        diff = -diff
    if diff >= step:
        code |= 4
        diff -= step
    if diff >= step >> 1:
        code |= 2
        diff -= step >> 1
    if diff >= step >> 2:
        code |= 1

    # 重建 delta：基线 step>>3，各 bit 增量
    delta = step >> 3
    if code & 4:
        delta += step
    if code & 2:
        delta += step >> 1
    if code & 1:
        delta += step >> 2

    if code & 8:
        predictor -= delta
    else:
        predictor += delta
    predictor = _clamp_pcm(predictor)

    step_index += IMA_INDEX_TABLE[code]
    if step_index < 0:
        step_index = 0
    elif step_index > 88:
        step_index = 88
    return code, predictor, step_index


def _ima_decode_sample(code: int, predictor: int, step_index: int) -> tuple[int, int, int]:
    """解码单样本，返回 (pcm, predictor, step_index)"""
    step = IMA_STEP_TABLE[step_index]
    delta = step >> 3
    if code & 4:
        delta += step
    if code & 2:
        delta += step >> 1
    if code & 1:
        delta += step >> 2

    if code & 8:
        predictor -= delta
    else:
        predictor += delta
    predictor = _clamp_pcm(predictor)

    step_index += IMA_INDEX_TABLE[code]
    if step_index < 0:
        step_index = 0
    elif step_index > 88:
        step_index = 88
    return predictor, predictor, step_index


def _pack_nibbles(codes: list[int]) -> bytes:
    """640 个 4-bit 码 → 320B（高 nibble 为第 1 个样本）"""
    out = bytearray(DATA_BYTES)
    for i in range(0, len(codes), 2):
        hi = codes[i]
        lo = codes[i + 1] if i + 1 < len(codes) else 0
        out[i // 2] = ((hi & 0x0F) << 4) | (lo & 0x0F)
    return bytes(out)


def _unpack_nibbles(data: bytes) -> list[int]:
    """320B → 640 个 4-bit 码（高 nibble 在前）"""
    codes = []
    for b in data:
        codes.append((b >> 4) & 0x0F)
        codes.append(b & 0x0F)
    return codes


# ---------------------------------------------------------------------------
# 直流抑制（编码器侧）与逆滤波（解码器侧）
# ---------------------------------------------------------------------------
class DCBlocker:
    """一阶高通：y[n] = x[n] - x[n-1] + R·y[n-1]，R=0.999（Q15 32735）"""

    def __init__(self):
        self._x_prev = 0
        self._y_prev = 0

    def reset(self):
        self._x_prev = 0
        self._y_prev = 0

    def process(self, samples: list[int]) -> list[int]:
        out = []
        xp, yp = self._x_prev, self._y_prev
        for x in samples:
            y = (x - xp) + ((yp * DC_R_Q15) >> 15)
            y = _clamp_pcm(y)
            out.append(y)
            xp, yp = x, y
        self._x_prev = xp
        self._y_prev = yp
        return out

    def restore(self, samples: list[int]) -> list[int]:
        """逆滤波：x[n] = y[n] + x[n-1] - R·y[n-1]"""
        out = []
        xp, yp = self._x_prev, self._y_prev
        for y in samples:
            x = y + xp - ((yp * DC_R_Q15) >> 15)
            x = _clamp_pcm(x)
            out.append(x)
            xp, yp = x, y
        self._x_prev = xp
        self._y_prev = yp
        return out


# ---------------------------------------------------------------------------
# 编解码器（有状态，跨帧延续）
# ---------------------------------------------------------------------------
def pcm_bytes_to_samples(pcm: bytes) -> list[int]:
    if len(pcm) % 2:
        raise ProtocolError("bad_length", f"PCM 字节长度须为偶数：{len(pcm)}")
    return list(struct.unpack(f"<{len(pcm) // 2}h", pcm))


def samples_to_pcm_bytes(samples: list[int]) -> bytes:
    return struct.pack(f"<{len(samples)}h", *samples)


class RadpcmEncoder:
    """RADPCM 编码器（predictor/step_index 跨帧延续，帧恢复点随帧携带）"""

    def __init__(self, frame_index_start: int = 0):
        self.predictor = 0
        self.step_index = 0
        self._dc = DCBlocker()
        self._next_frame_index = frame_index_start & 0xFFFF

    def encode(self, pcm: bytes) -> bytes:
        """编码 1280B PCM（640 样本）为 328B RADPCM 帧"""
        if len(pcm) != SAMPLES_PER_FRAME * 2:
            raise ProtocolError(
                "bad_length",
                f"RADPCM 编码输入须为 {SAMPLES_PER_FRAME * 2}B：实际 {len(pcm)}B",
            )
        raw_samples = pcm_bytes_to_samples(pcm)
        filtered = self._dc.process(raw_samples)

        recover_pcm = self.predictor
        recover_step = self.step_index

        codes = []
        predictor, step_index = self.predictor, self.step_index
        for s in filtered:
            code, predictor, step_index = _ima_encode_sample(
                s, predictor, step_index
            )
            codes.append(code)
        self.predictor = predictor
        self.step_index = step_index

        frame = build_frame(
            frame_index=self._next_frame_index,
            recover_pcm=recover_pcm,
            step_index=recover_step,
            data=_pack_nibbles(codes),
        )
        self._next_frame_index = (self._next_frame_index + 1) & 0xFFFF
        return frame


class RadpcmDecoder:
    """RADPCM 解码器（含丢包检测与帧恢复点重置）"""

    def __init__(self):
        self.predictor = 0
        self.step_index = 0
        self.last_frame: int = FIRST_FRAME_SENTINEL
        self._dc = DCBlocker()
        self.dropped_frames = 0  # 检测到的丢包次数（统计用）

    def decode(self, frame: bytes) -> bytes:
        """解码 328B RADPCM 帧为 1280B PCM

        丢包恢复逻辑（规范 §6.2）：
        1. last_frame == 0xFFFF（首帧）→ 用帧恢复点重置状态
        2. frame_index == 0           → 新语音序列，重置状态
        3. frame_index != last+1      → 丢包，用帧恢复点重置状态
        4. 其余                       → 连续帧，正常解码
        """
        info, data = parse_frame(frame)

        need_reset = False
        if self.last_frame == FIRST_FRAME_SENTINEL:
            need_reset = True                      # 情形 1
        elif info.frame_index == 0:
            need_reset = True                      # 情形 2
        elif info.frame_index != ((self.last_frame + 1) & 0xFFFF):
            need_reset = True                      # 情形 3
            self.dropped_frames += 1

        if need_reset:
            self.predictor = info.recover_pcm
            self.step_index = info.step_index
            self._dc.reset()
        self.last_frame = info.frame_index

        codes = _unpack_nibbles(data)
        predictor, step_index = self.predictor, self.step_index
        decoded = []
        for code in codes:
            _, predictor, step_index = _ima_decode_sample(
                code, predictor, step_index
            )
            decoded.append(predictor)
        self.predictor = predictor
        self.step_index = step_index

        restored = self._dc.restore(decoded)
        return samples_to_pcm_bytes(restored)


# ---------------------------------------------------------------------------
# 无状态便捷函数（单帧独立编解码，帧恢复点即初始状态）
# ---------------------------------------------------------------------------
def encode_frame(pcm: bytes, frame_index: int = 0) -> bytes:
    """无状态单帧编码（直流抑制状态不跨帧）"""
    return RadpcmEncoder(frame_index_start=frame_index).encode(pcm)


def decode_frame(frame: bytes) -> bytes:
    """无状态单帧解码（始终以帧恢复点为初始状态）"""
    decoder = RadpcmDecoder()
    decoder.decode(frame)  # 首帧自动重置
    # 重新解码以获得干净状态下的输出
    d2 = RadpcmDecoder()
    return d2.decode(frame)
