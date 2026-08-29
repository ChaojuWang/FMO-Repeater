"""OPUS 编解码封装

依据规范 §6.1：
- 采样率 8000Hz / 单声道
- 应用模式 VoIP（OPUS_APPLICATION_VOIP）
- 比特率自动（OPUS_AUTO，8kHz 下约 9500bps）
- 复杂度 4 / 信号类型语音（OPUS_SIGNAL_VOICE）
- VBR 启用
- 帧长 40ms（320 样本 / 640B PCM）

可选依赖：opuslib（PyPI，绑定系统 libopus）。缺失时 is_available()==False，
构造编码/解码器抛出 OpusUnavailable；RADPCM/协议/Echo 功能不受影响（决策 D4）。
"""

from __future__ import annotations

SAMPLE_RATE = 8000
CHANNELS = 1
FRAME_MS = 40
SAMPLES_PER_FRAME = 320                 # 40ms @ 8kHz
PCM_BYTES_PER_FRAME = SAMPLES_PER_FRAME * 2   # 640B
MAX_PACKET_BYTES = 1024                 # 单帧编码输出上界（40ms @ ~9.5kbps 远小于此）

try:
    from opuslib import Encoder as _OpusLibEncoder, Decoder as _OpusLibDecoder
    from opuslib import (
        APPLICATION_VOIP, AUTO as OPUS_AUTO,
        SIGNAL_VOICE,
    )

    _OPUS_AVAILABLE = True
    _IMPORT_ERROR: Exception | None = None
except Exception as _e:  # pragma: no cover - 环境相关
    _OPUS_AVAILABLE = False
    _IMPORT_ERROR = _e


def _try_opuslib_api() -> bool:
    """opuslib 可用性实际探测（导入成功后仍需验证 API 可调用）"""
    if not _OPUS_AVAILABLE:
        return False
    try:
        enc = _OpusLibEncoder(SAMPLE_RATE, CHANNELS, APPLICATION_VOIP)
        enc.reset_state()
        return True
    except Exception:
        return False


class OpusUnavailable(RuntimeError):
    """系统缺少 opuslib 或 libopus 时抛出"""


def is_available() -> bool:
    """OPUS 支持是否可用（首次调用做实际探测并缓存）"""
    global _AVAILABILITY_CACHE
    if _AVAILABILITY_CACHE is None:
        _AVAILABILITY_CACHE = _try_opuslib_api()
    return _AVAILABILITY_CACHE


_AVAILABILITY_CACHE = None


def _require_available():
    if not is_available():
        raise OpusUnavailable(
            f"OPUS 不可用：opuslib/libopus 导入失败（{_IMPORT_ERROR}）。"
            f"请安装 opuslib（pip install opuslib）与系统 libopus。"
        )


class OpusEncoder:
    """OPUS 编码器（8kHz / mono / VoIP / auto bitrate / complexity 4 / voice / VBR / 40ms）"""

    def __init__(self):
        _require_available()
        self._enc = _OpusLibEncoder(SAMPLE_RATE, CHANNELS, APPLICATION_VOIP)
        self._enc.bitrate = OPUS_AUTO
        self._enc.complexity = 4
        self._enc.signal = SIGNAL_VOICE
        self._enc.vbr = 1
        # opuslib 3.x 的 inband_fec 属性 setter 存在漏参 bug，
        # 直接经底层 encoder_ctl 设置前向纠错
        from opuslib.api import encoder as _api_encoder
        from opuslib.api import ctl as _api_ctl
        _api_encoder.encoder_ctl(
            self._enc.encoder_state, _api_ctl.set_inband_fec, 1
        )

    def encode(self, pcm: bytes) -> bytes:
        """编码 640B PCM（320 样本 / 40ms）为变长 OPUS 帧"""
        if len(pcm) != PCM_BYTES_PER_FRAME:
            raise ValueError(
                f"OPUS 编码输入须为 {PCM_BYTES_PER_FRAME}B：实际 {len(pcm)}B"
            )
        return self._enc.encode(pcm, SAMPLES_PER_FRAME)

    def reset(self):
        """重置编码器状态（丢包恢复后调用）"""
        self._enc.reset_state()


class OpusDecoder:
    """OPUS 解码器（8kHz / mono / 40ms）"""

    def __init__(self):
        _require_available()
        self._dec = _OpusLibDecoder(SAMPLE_RATE, CHANNELS)

    def decode(self, data: bytes) -> bytes:
        """解码一个 OPUS 帧为 640B PCM

        data 为空时按丢包处理（PLC，舒适噪声填充）。
        """
        return self._dec.decode(data, SAMPLES_PER_FRAME)

    def reset(self):
        """重置解码器状态"""
        self._dec.reset_state()
