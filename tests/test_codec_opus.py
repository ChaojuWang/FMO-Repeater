"""OPUS 编解码测试（缺 opuslib/libopus 自动跳过）"""

import math

import pytest

from fmo_repeater.codecs import (
    OpusDecoder,
    OpusEncoder,
    OpusUnavailable,
    opus_is_available,
)
from conftest import sine_pcm16

pytestmark = pytest.mark.skipif(
    not opus_is_available(), reason="opuslib/libopus 不可用"
)


class TestAvailability:
    def test_available_and_constructible(self):
        assert opus_is_available() is True
        OpusEncoder()
        OpusDecoder()

    def test_unavailable_message(self, monkeypatch):
        import fmo_repeater.codecs.opus_codec as mod
        monkeypatch.setattr(mod, "_AVAILABILITY_CACHE", False)
        with pytest.raises(OpusUnavailable):
            mod.OpusEncoder()
        with pytest.raises(OpusUnavailable):
            mod.OpusDecoder()


class TestRoundtrip:
    def test_single_frame(self):
        enc, dec = OpusEncoder(), OpusDecoder()
        pcm = sine_pcm16(320, 12000, 440)
        packet = enc.encode(pcm)
        assert 0 < len(packet) <= 400  # 40ms @ ~9.5kbps VBR
        out = dec.decode(packet)
        assert len(out) == 640

    def test_variable_length(self):
        """VBR：不同内容帧长不同"""
        enc = OpusEncoder()
        loud = enc.encode(sine_pcm16(320, 30000, 440))
        silence = enc.encode(b"\x00" * 640)
        # 静音帧通常显著短于响亮帧
        assert len(silence) < len(loud)

    def test_continuous_stream(self):
        enc, dec = OpusEncoder(), OpusDecoder()
        outs = []
        for i in range(10):
            pcm = sine_pcm16(320, 15000, 440, i * 320)
            outs.append(dec.decode(enc.encode(pcm)))
        assert all(len(o) == 640 for o in outs)

    def test_plc_empty_frame(self):
        """空帧 → PLC 舒适噪声，输出定长"""
        dec = OpusDecoder()
        out = dec.decode(b"")
        assert len(out) == 640

    def test_input_size_check(self):
        enc = OpusEncoder()
        with pytest.raises(ValueError):
            enc.encode(b"\x00" * 100)

    def test_reset(self):
        enc, dec = OpusEncoder(), OpusDecoder()
        enc.encode(sine_pcm16(320, 10000, 440))
        enc.reset()
        dec.reset()
        # 重置后仍可正常工作
        p = enc.encode(sine_pcm16(320, 10000, 440))
        assert len(dec.decode(p)) == 640
