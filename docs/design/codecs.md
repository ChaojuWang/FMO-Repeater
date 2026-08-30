# 编解码层设计

> Merged from changes/001, 006

## 1. 公共参数

采样率 8000Hz / 16-bit 有符号 / 单声道（规范 §1）。

## 2. RADPCM（radpcm.py，纯 Python）

### 2.1 帧结构（固定 328B）

```
frame_index(H) recover_pcm(h) step_index(B) reserved(B) adpcm_bytes(H) data(320B)
```

- 640 样本（80ms）→ 320B 数据区（每字节 2 个 4-bit 样本，高 nibble 在前）
- `adpcm_bytes`：新实现 uint16=320；兼容旧 8-bit 包（低字节 64 且高字节 0 → legacy）
- 帧恢复点：`recover_pcm`/`step_index` = 本帧编码开始时的 IMA 状态
- `parse_frame` 与 `build_frame` 均校验 `step_index` 为 0..88；非法恢复点统一抛
  `ProtocolError(reason="bad_step_index")`

### 2.2 IMA ADPCM 核心

- 89 级步长表 `IMA_STEP_TABLE`、16 项索引表 `IMA_INDEX_TABLE`（规范原文）
- 编码：diff 逐级比较生成 4-bit 码（bit3 符号）；解码：step>>3 基线 + 各 bit 增量重建
- predictor 限幅 [-32768, 32767]、step_index 限幅 [0, 88]

### 2.3 直流抑制（决策 D2）

编码器侧：`y[n] = x[n] - x[n-1] + R·y[n-1]`，R=0.999（Q15 32735，截止约 1.3Hz）；
解码器侧逆滤波还原：`x[n] = y[n] + x[n-1] - R·y[n-1]`。
往返语音直流可还原，线格式不受影响。

### 2.4 丢包恢复（规范 §6.2 四情形）

| 情形 | 动作 |
|---|---|
| last_frame == 0xFFFF（首帧） | 帧恢复点重置 |
| frame_index == 0 | 新序列，重置 |
| frame_index != last+1 | 丢包（dropped_frames++），帧恢复点重置 |
| 其余 | 连续解码 |

frame_index 为 uint16 循环（0xFFFF 回绕）。

### 2.5 有状态 API

- `RadpcmEncoder.encode(pcm1280B) -> frame328B`（predictor/step_index 跨帧延续）
- `RadpcmDecoder.decode(frame) -> pcm1280B`（含丢包检测）
- 无状态便捷：`encode_frame`/`decode_frame`

### 2.6 质量（决策 D7）

4-bit IMA ADPCM 理论 19-27dB；实测满幅正弦 26.8dB / 多音+噪 22.9dB。
验收阈值 SNR ≥ 20dB（去直流、连续流）；单帧无状态首帧收敛期放宽至 15dB。

## 3. OPUS（opus_codec.py，可选依赖）

参数（规范 §6.1）：8kHz/mono/VOIP/auto bitrate/complexity 4/voice/VBR/40ms（640B PCM → 变长帧）。

- 依赖 opuslib（3.x 属性式 API）+ 系统 libopus
- `is_available()` 实际探测（构造 Encoder 验证可调用）并缓存
- 缺失时 `OpusEncoder()`/`OpusDecoder()` 抛 `OpusUnavailable`；其余功能不受影响（决策 D4）
- `decode(b"")`：PLC 舒适噪声帧
- 注：opuslib 3.0.1 `inband_fec` setter 有漏参 bug，经底层 `encoder_ctl` 绕过
