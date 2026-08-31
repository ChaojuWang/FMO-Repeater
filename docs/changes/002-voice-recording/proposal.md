# 提案：语音录制与保留策略（Voice Recording）

> status: merged
> 变更编号：002

## 1. 动机

Echo 服务汇集已经通过协议校验、信道仲裁并封口的完整 PTT，适合用于回放调试、
通联留存和语音质量分析。录音需要默认关闭，并通过容量和期限限制避免长期运行耗尽磁盘。

## 2. 目标

1. `recording.enabled` 默认 false，关闭时不订阅完成事件、不创建目录。
2. 每个 `TransmissionCompleted` 生成一个 8kHz/16bit/mono 录音文件。
3. 正常单 codec PTT 输出 WAV；OPUS 不可解码时保留完整原始编码。
4. 文件扁平保存于 `./recording`，名称为毫秒时间戳、呼号和 UID。
5. 默认最多 `512M`、保留 `1w`，启动和每次保存后清理。
6. 录制、编码异常、降级和清理行为写入运行日志及 JSONL 事件日志。

## 3. 范围

### 做

- `fmo_repeater/service/recorder.py`：解码、单 PTT 合并落盘、OPUS 降级和目录 rotate。
- recording 配置、容量/时长缩写解析与校验。
- 首包墙钟时间进入 `TransmissionCompleted`，用于文件命名。
- pytest 单元及组合测试、用户文档和存量设计合并。

### 不做

- 音频增强、上传或远程存储。
- 后台定时清理线程；无新录音时在下次启动再清理。
- 录音索引、查询或播放 API。

## 4. 依赖

- 变更 001：协议层、codecs、event_log。
- 变更 005：`TransmissionCompleted` 与独立消费者事件总线。
