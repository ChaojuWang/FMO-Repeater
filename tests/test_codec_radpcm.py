"""RADPCM 编解码测试"""

import math
import struct

import pytest

from fmo_repeater.codecs import (
    DATA_BYTES,
    DC_R_Q15,
    FIRST_FRAME_SENTINEL,
    FRAME_BYTES,
    IMA_INDEX_TABLE,
    IMA_STEP_TABLE,
    SAMPLES_PER_FRAME,
    DCBlocker,
    RadpcmDecoder,
    RadpcmEncoder,
    build_frame,
    decode_frame,
    encode_frame,
    parse_frame,
    pcm_bytes_to_samples,
    samples_to_pcm_bytes,
)
from fmo_repeater.protocol import ProtocolError
from conftest import sine_pcm16


def snr_db(original: list, recon: list, remove_dc: bool = True) -> float:
    if remove_dc:
        def rm(xs):
            m = sum(xs) / len(xs)
            return [x - m for x in xs]
        original, recon = rm(original), rm(recon)
    sig = sum(x * x for x in original)
    noise = sum((a - b) ** 2 for a, b in zip(original, recon))
    if noise == 0:
        return float("inf")
    return 10 * math.log10(sig / noise)


class TestTables:
    def test_step_table(self):
        assert len(IMA_STEP_TABLE) == 89
        assert IMA_STEP_TABLE[0] == 7
        assert IMA_STEP_TABLE[-1] == 32767
        # 严格递增
        assert all(a < b for a, b in zip(IMA_STEP_TABLE, IMA_STEP_TABLE[1:]))

    def test_index_table(self):
        assert IMA_INDEX_TABLE == (-1, -1, -1, -1, 2, 4, 6, 8,
                                   -1, -1, -1, -1, 2, 4, 6, 8)


class TestFrameStructure:
    def test_frame_size(self):
        pcm = sine_pcm16(SAMPLES_PER_FRAME, 20000, 440)
        frame = encode_frame(pcm)
        assert len(frame) == FRAME_BYTES == 328

    def test_frame_header_fields(self):
        enc = RadpcmEncoder(frame_index_start=7)
        pcm_warm = sine_pcm16(SAMPLES_PER_FRAME, 10000, 300)
        enc.encode(pcm_warm)  # 预热，使 predictor/step_index 非零
        # 记录编码前状态（帧恢复点应为该值）
        pre_predictor, pre_step = enc.predictor, enc.step_index
        frame = enc.encode(sine_pcm16(SAMPLES_PER_FRAME, 20000, 440))
        info, data = parse_frame(frame)
        assert info.frame_index == 8
        assert info.recover_pcm == pre_predictor  # 帧恢复点 = 编码开始时状态
        assert info.step_index == pre_step
        assert 0 <= info.step_index <= 88
        assert len(data) == DATA_BYTES
        # adpcmBytes 新格式 = 320
        assert struct.unpack_from("<H", frame, 6)[0] == 320

    def test_frame_index_wraparound(self):
        enc = RadpcmEncoder(frame_index_start=0xFFFE)
        f1 = enc.encode(sine_pcm16(SAMPLES_PER_FRAME, 10000, 300))
        f2 = enc.encode(sine_pcm16(SAMPLES_PER_FRAME, 10000, 300))
        i1, _ = parse_frame(f1)
        i2, _ = parse_frame(f2)
        assert i1.frame_index == 0xFFFE
        assert i2.frame_index == 0xFFFF

    def test_legacy_8bit_adpcm_bytes(self):
        pcm = sine_pcm16(SAMPLES_PER_FRAME, 10000, 300)
        frame = bytearray(encode_frame(pcm))
        # 旧格式：8-bit 字段线值 64（低字节 64、高字节 0 填充）
        frame[6] = 64
        frame[7] = 0
        info, data = parse_frame(bytes(frame))
        assert info.legacy is True
        assert info.adpcm_bytes == DATA_BYTES
        assert len(data) == DATA_BYTES

    def test_bad_frame_rejected(self):
        with pytest.raises(ProtocolError):
            parse_frame(b"\x00" * 100)          # 过短
        # 328B 但 adpcmBytes 非法（=0：既非新格式 320 也非旧格式 64）
        bad = bytearray(b"\x00" * FRAME_BYTES)
        bad[6:8] = struct.pack("<H", 0)
        with pytest.raises(ProtocolError):
            parse_frame(bytes(bad))

    def test_build_frame_size_check(self):
        with pytest.raises(ProtocolError):
            build_frame(0, 0, 0, b"\x00" * 319)


class TestRoundtrip:
    def test_single_frame_sine_snr(self, pcm_sine_640):
        """无状态单帧：初始 predictor=0 需从零收敛，SNR 阈值放宽至 15dB；
        连续流质量见 test_continuous_stream_snr（≥20dB）"""
        frame = encode_frame(pcm_sine_640)
        out = decode_frame(frame)
        assert len(out) == SAMPLES_PER_FRAME * 2
        s = snr_db(
            pcm_bytes_to_samples(pcm_sine_640),
            pcm_bytes_to_samples(out),
        )
        assert s >= 15, f"SNR {s:.1f} < 15dB"

    def test_continuous_stream_snr(self):
        enc, dec = RadpcmEncoder(frame_index_start=0), RadpcmDecoder()
        orig, recon = [], []
        for i in range(10):
            pcm = sine_pcm16(SAMPLES_PER_FRAME, 30000, 440, i * SAMPLES_PER_FRAME, dc=500)
            out = dec.decode(enc.encode(pcm))
            orig += pcm_bytes_to_samples(pcm)
            recon += pcm_bytes_to_samples(out)
        s = snr_db(orig, recon)
        assert s >= 20, f"SNR {s:.1f} < 20dB"

    def test_multi_tone_noise_snr(self):
        import random
        random.seed(42)
        enc, dec = RadpcmEncoder(frame_index_start=0), RadpcmDecoder()
        orig, recon = [], []
        for i in range(10):
            samples = [
                int(0.5 * 28000 * math.sin(2 * math.pi * 300 * (i * 640 + n) / 8000)
                    + 0.3 * 28000 * math.sin(2 * math.pi * 1500 * (i * 640 + n) / 8000)
                    + 0.2 * random.randint(-3000, 3000))
                for n in range(SAMPLES_PER_FRAME)
            ]
            pcm = samples_to_pcm_bytes(samples)
            out = dec.decode(enc.encode(pcm))
            orig += samples
            recon += pcm_bytes_to_samples(out)
        s = snr_db(orig, recon)
        assert s >= 20, f"SNR {s:.1f} < 20dB"

    def test_silence_roundtrip(self):
        pcm = b"\x00" * (SAMPLES_PER_FRAME * 2)
        out = decode_frame(encode_frame(pcm))
        # 静音应保持接近静音
        assert max(abs(x) for x in pcm_bytes_to_samples(out)) < 500

    def test_encoder_input_size_check(self):
        with pytest.raises(ProtocolError):
            RadpcmEncoder().encode(b"\x00" * 100)


class TestStateContinuity:
    def test_decoder_last_frame_tracking(self):
        enc, dec = RadpcmEncoder(frame_index_start=100), RadpcmDecoder()
        pcm = sine_pcm16(SAMPLES_PER_FRAME, 10000, 440)
        dec.decode(enc.encode(pcm))
        assert dec.last_frame == 100
        dec.decode(enc.encode(pcm))
        assert dec.last_frame == 101
        assert dec.dropped_frames == 0

    def test_state_carried_between_frames(self):
        """连续两帧 vs 独立两帧编码：字节不同（状态延续）"""
        enc = RadpcmEncoder(frame_index_start=0)
        pcm1 = sine_pcm16(SAMPLES_PER_FRAME, 20000, 440, 0)
        pcm2 = sine_pcm16(SAMPLES_PER_FRAME, 20000, 440, SAMPLES_PER_FRAME)
        f1a, f2a = enc.encode(pcm1), enc.encode(pcm2)
        enc_b = RadpcmEncoder(frame_index_start=0)
        f1b = enc_b.encode(pcm1)
        enc_c = RadpcmEncoder(frame_index_start=1)
        f2c = enc_c.encode(pcm2)  # 独立编码（初始状态 0）
        assert f1a == f1b
        assert f2a != f2c  # 状态延续导致第二帧编码不同


class TestPacketLoss:
    def test_recovery_after_drop(self):
        enc, dec = RadpcmEncoder(frame_index_start=0), RadpcmDecoder()
        pcm = sine_pcm16(SAMPLES_PER_FRAME, 10000, 440)
        frames = [enc.encode(pcm) for _ in range(4)]
        dec.decode(frames[0])
        # 跳过 frames[1]、frames[2]
        out = dec.decode(frames[3])
        assert len(out) == SAMPLES_PER_FRAME * 2
        assert dec.dropped_frames == 1
        assert dec.last_frame == 3
        # 恢复后继续解码
        f4 = enc.encode(pcm)
        out2 = dec.decode(f4)
        assert len(out2) == SAMPLES_PER_FRAME * 2
        assert dec.dropped_frames == 1  # 无新丢包

    def test_frame_index_zero_resets(self):
        """frame_index == 0 视为新语音序列，重置状态"""
        enc, dec = RadpcmEncoder(frame_index_start=0), RadpcmDecoder()
        pcm = sine_pcm16(SAMPLES_PER_FRAME, 10000, 440)
        for _ in range(3):
            dec.decode(enc.encode(pcm))
        assert dec.last_frame == 2
        # 新序列 frame_index=0
        enc2 = RadpcmEncoder(frame_index_start=0)
        dec.decode(enc2.encode(pcm))
        assert dec.last_frame == 0
        assert dec.dropped_frames == 0  # 情形 2 不是丢包

    def test_first_frame_sentinel(self):
        dec = RadpcmDecoder()
        assert dec.last_frame == FIRST_FRAME_SENTINEL


class TestDCBlocker:
    def test_dc_removed(self):
        dc = DCBlocker()
        samples = [500 + int(1000 * math.sin(2 * math.pi * 440 * n / 8000))
                   for n in range(1000)]
        out = dc.process(samples)
        # 输出均值应显著小于输入均值（直流被抑制）
        assert abs(sum(out) / len(out)) < 500 * 0.3
        # 交流分量保留
        assert max(out) > 500

    def test_r_constant(self):
        assert DC_R_Q15 == 32735

    def test_restore_inverse(self):
        """filter → restore 往返还原（无量化时无损）"""
        import random
        random.seed(1)
        samples = [random.randint(-20000, 20000) + 3000 for _ in range(500)]
        f = DCBlocker()
        y = f.process(samples)
        g = DCBlocker()
        x = g.restore(y)
        # clamp 造成的误差极小
        assert max(abs(a - b) for a, b in zip(samples, x)) <= 2
