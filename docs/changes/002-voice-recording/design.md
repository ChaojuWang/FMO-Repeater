# 详细设计：语音录制与保留策略（Voice Recording）

> status: merged
> 变更编号：002 ｜ 依赖：变更 001、005

## 1. 配置与规范化

```yaml
recording:
  enabled: false
  directory: "./recording"
  max_total_size: "512M"
  max_age: "1w"
```

- 容量接受非负整数（直接表示字节）或字符串 `整数 + B/K/M/G/T`（大小写不敏感），按 1024 进制；
  时长接受 `整数 + h/d/w`。数字或字符串 `0` 表示关闭对应限制。
- 不接受负数、bool、小数、未知单位或缺少单位的非零时长。
- `parse_size` 与 `parse_duration` 分别规范化为字节和秒；Recorder 只保存规范化整数。
- enabled=true 时 directory 必须是非空字符串，路径分段不得包含 `..`；允许绝对路径。

## 2. 事件时间（决策 R1）

MQTT 回调同时采集 `time.monotonic()` 和 `time.time()`。前者继续用于仲裁、PTT
超时和相对时序；后者作为 `TransmissionCompleted.first_received_wall_time`，用于
生成真实日历文件名。两种时间不互相推导，避免系统校时影响路由逻辑。

`MqttTransport.on_payload`、`TransmissionProducer.submit/process_packet` 增加可选墙钟
参数；测试直接调用未提供时以当前 `time.time()` 补齐。

## 3. Recorder 与单 PTT 文件（决策 R2）

```python
class Recorder:
    def __init__(self, config, event_log=None, logger=None): ...
    def handle(self, transmission: TransmissionCompleted) -> None: ...
    def cleanup(self) -> None: ...
    def stop(self) -> None: ...
```

- enabled=false 时全方法 no-op，不创建目录；RepeaterService 也不构造、不订阅。
- enabled=true 时创建根目录、立即 cleanup，并注册专属 FIFO 消费者。
- 每个完成事件按包/帧顺序解析；以首个有效帧 codec 为预期 codec。
- RADPCM/OPUS 分别用有状态 decoder；codec 变化时更换 decoder，但 PCM 仍按顺序
  拼接为一个 WAV。编码变化属于异常，记 WARNING 与 `recording_codec_changed`。
- 单帧解码异常记 WARNING 并跳过；全部帧失败则不写文件。

## 4. 文件名与原子落盘（决策 R3）

```text
{directory}/{YYYYMMDD-HHmmss-SSS}-{safe_callsign}-{uid}.{wav|opusraw|fmoraw}
```

- 时间为 PTT 首包 wall time 的本地时间，精确到毫秒。
- 呼号用 `re.sub(r'[^\w.-]', '_', callsign)` 清洗；空、`.`、`..` 变为 `UNKNOWN`。
- 不建立呼号子目录；同名目标允许覆盖。
- WAV 固定 8000Hz、16bit、mono。临时文件写完后 `os.replace` 原子替换；异常清理
  临时文件，记录 `recording_discarded(reason=io_error)`。

正常情况下一个 PTT 只有一种编码。异常混合编码在 OPUS 可用时解码并拼为一个 WAV；
纯 OPUS 且 OPUS 不可用时保存 `.opusraw`；混合编码且 OPUS 不可用时保存 `.fmoraw`。
两种 raw 文件都按顺序拼接完整 `EncodedVoiceFrame.to_bytes()`，保留 8B 自描述头。

## 5. Rotate（决策 R4）

- 仅管理根目录直属的 `.wav/.opusraw/.fmoraw` 普通文件，不递归、不碰其他文件。
- 启动时及每个 PTT 成功保存后执行；先清年龄，再清总容量。
- 年龄以 mtime 和 cleanup 时墙钟比较，`age > max_age` 才删除，边界保留。
- 总容量超限时按 `(mtime, filename)` 从旧到新删除，直至 `<= max_total_size`；
  单个新文件超额也可能立即删除。
- 删除成功记 `recording_deleted`；失败记 WARNING 和 `recording_cleanup_failed`，继续处理。

## 6. 事件

| event | 关键字段 |
|---|---|
| recording_stream_start | uid, callsign, stream_begin_utc, started_at |
| recording_codec_changed | uid, callsign, from_codec, to_codec, frame |
| recording_saved | uid, callsign, codec, file, frames, duration_ms, bytes, degraded? |
| recording_discarded | uid, callsign, reason |
| recording_deleted | file, bytes, reason(age_limit/size_limit) |
| recording_cleanup_failed | file, reason |

`bytes` 是最终文件大小；`frames/duration_ms` 是成功写入 WAV 的帧，raw 降级则是全部
原始帧。`codec` 为 RADPCM、OPUS 或 MIXED；degraded 仅在 raw 降级时为 true。

## 7. 生命周期与错误边界（决策 R5）

Recorder 在事件总线专属线程执行，不阻塞 MQTT 或 Echo 消费者。停机顺序为
transport quiesce → Echo stop → Recorder stop → event bus stop。`stop()` 设置取消事件；
Recorder 在逐包解析、codec 检查、逐帧解码、逐帧写入和 rotate 删除之间检查该事件，
取消时清理尚未替换的临时文件并记录 `recording_discarded(reason=shutdown)`。已经完成
原子替换的文件视为保存成功。同步文件系统调用本身不可抢占，但返回后不得继续后续工作。
待处理录音事件不冲刷。意外包解析失败、无帧、全帧解码失败和 IO 错误均产生
`recording_discarded`，不得抛出到消费者线程之外。

## 8. 测试

- 配置正整数字节、缩写、0、大小写、非法值和目录校验。
- RADPCM/OPUS/混合编码单 PTT 单文件；坏帧隔离和异常事件。
- OPUS 不可用时 `.opusraw/.fmoraw` 的逐帧完整字节。
- 毫秒命名、清洗、扁平目录和覆盖。
- 启动/保存后 rotate、边界、删除失败与非录音文件保护。
- RepeaterService 开关注册与完整事件到文件流程；全量回归。
- 在途录音收到 stop 后退出、清理临时文件，且 RepeaterService 在等待总线前先取消 Recorder。

## 9. 决策记录

| # | 决策 | 理由 |
|---|---|---|
| R1 | 单调时钟与墙钟同时采集 | 路由稳定性和真实文件日期各自准确 |
| R2 | 一个完成 PTT 一个文件 | 与用户对“一段对话”的边界一致 |
| R3 | 扁平毫秒时间戳命名、允许覆盖 | 简洁可读，毫秒降低碰撞概率 |
| R4 | 启动及保存后同步 rotate | 无额外维护线程且可限制活跃系统磁盘 |
| R5 | 专属事件消费者、立即取消停机 | 不阻塞网络且保持现有生命周期语义 |
