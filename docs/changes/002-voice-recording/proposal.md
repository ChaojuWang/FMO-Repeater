# 提案：语音录制功能（Voice Recording）

> status: designed（详细设计已完成，见 design.md / tasks.md；待实施）
> 变更编号：002

## 1. 动机

Echo 服务天然汇集全网语音流，具备录制归档价值：回放调试、通联记录留存、语音质量分析。协议重构（变更 001）落地后，完整的解析能力（消息头元数据 + 编码语音帧 + 编解码器）使录音成为可行的新增功能。

## 2. 目标

1. 可配置开关：`recording.enabled`，**默认 false**（不影响现有部署）
2. 开启后：按发送者分流录制语音流，流结束（超时 / 新 streamBeginUTC）时落盘
3. 输出 WAV（8kHz / 16bit / mono，协议公共音频参数），RADPCM 纯 Python 解码；OPUS 依赖 libopus，缺失时降级保存原始编码帧（`.opusraw`）
4. 录制事件写入 JSONL 事件日志（复用变更 001 的 event_log）

## 3. 范围

### 做
- `fmo_repeater/service/recorder.py`：Recorder（开关、分流、解码、落盘）
- 录制相关配置节与验证
- pytest 测试（WAV 落盘、开关、降级、事件）

### 不做
- 音频增强（滤波、增益、降噪）
- 录音管理与清理策略（磁盘配额、保留期）——后续 change
- 上传 / 远程存储

## 4. 初步设计要点（详细设计待迭代一完成后补写）

- 文件命名：`recordings/{callsign}/{YYYYMMDD-HHmmss}-{uid}-{seq:03d}-{codec}.wav`
  - `YYYYMMDD-HHmmss`：流开始本地时间
  - `uid`：发送者 UID
  - `seq`：同一（呼号, 秒）下防冲突的递增序号，3 位
  - `codec`：`RADPCM` / `OPUS`；降级时扩展名 `.opusraw`
  - 非法呼号字符（路径不安全）替换为 `_`
- 流切分：`stream_begin_utc` 变化即新流；与 Echo 超时共用流结束判定
- WAV 写入：`wave` 标准库（PCM 16bit mono 8kHz，非压缩 RIFF）
- 降级：OPUS 帧无 libopus → 逐帧拼接 raw（保留 8B 编码帧头便于事后离线解码），后缀 `.opusraw`，事件日志记 `degraded: true`

## 5. 依赖

- 变更 001：协议层（ParsedPacket）、codecs（RadpcmDecoder / OpusDecoder）、event_log
