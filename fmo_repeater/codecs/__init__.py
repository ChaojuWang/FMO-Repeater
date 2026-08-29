"""编解码层：RADPCM（IMA ADPCM）与 OPUS

公共音频参数（规范 §1）：采样率 8000Hz / 16-bit 有符号 / 单声道
"""

from .radpcm import (
    SAMPLE_RATE,
    SAMPLES_PER_FRAME,
    FRAME_MS,
    DATA_BYTES,
    FRAME_BYTES,
    DC_R_Q15,
    IMA_STEP_TABLE,
    IMA_INDEX_TABLE,
    FIRST_FRAME_SENTINEL,
    RadpcmEncoder,
    RadpcmDecoder,
    RadpcmFrameInfo,
    DCBlocker,
    encode_frame,
    decode_frame,
    parse_frame,
    build_frame,
    pcm_bytes_to_samples,
    samples_to_pcm_bytes,
)
from .opus_codec import (
    OpusEncoder,
    OpusDecoder,
    OpusUnavailable,
    is_available as opus_is_available,
)

__all__ = [
    "SAMPLE_RATE", "SAMPLES_PER_FRAME", "FRAME_MS", "DATA_BYTES",
    "FRAME_BYTES", "DC_R_Q15", "IMA_STEP_TABLE", "IMA_INDEX_TABLE",
    "FIRST_FRAME_SENTINEL",
    "RadpcmEncoder", "RadpcmDecoder", "RadpcmFrameInfo", "DCBlocker",
    "encode_frame", "decode_frame", "parse_frame", "build_frame",
    "pcm_bytes_to_samples", "samples_to_pcm_bytes",
    "OpusEncoder", "OpusDecoder", "OpusUnavailable", "opus_is_available",
]
