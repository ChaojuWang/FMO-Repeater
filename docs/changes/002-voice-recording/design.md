# 详细设计：语音录制功能（Voice Recording）

> status: designed（经 change 005 修订，待实施）
> 变更编号：002 ｜ 依赖：变更 001、005

## 1. 概述

新增 Recorder 事件消费者：每收到一个 `TransmissionCompleted`，解析其中的合法
原始包并解码为 WAV（8kHz/16bit/mono）落盘，录制事件写入 JSONL 事件日志。

**默认关闭**（`recording.enabled: false`），不影响现有部署；关闭时零开销 no-op。

依赖的变更 001 成果：

| 依赖 | 用途 |
|---|---|
| `ParsedPacket`（protocol/packet.py） | 已解析消息包：header 元数据 + frames（每帧含 encoded） |
| `RadpcmDecoder`（codecs/radpcm.py） | RADPCM 帧 → 1280B PCM（含丢包恢复） |
| `OpusDecoder` / `opus_is_available()`（codecs/opus_codec.py） | OPUS 帧 → 640B PCM / 依赖探测 |
| `EventLog`（service/event_log.py） | 结构化录制事件 |
| `TransmissionCompleted`（service/transmission.py） | 已经统一仲裁并封口的一次 PTT |

## 2. 配置

`config.py` 的 `DEFAULT_CONFIG` 新增节（加粗为相对现状新增）：

```yaml
**recording**:
  **enabled: false**        # 默认关闭
  **directory: recordings** # 输出根目录（相对工作目录）
```

`validate_config` 增加：`enabled` 必须 bool；`enabled=true` 时 `directory` 非空字符串；
目录名不得包含路径穿越（`..`）或以 `/` 开头的绝对路径以外的约束不设（允许绝对路径部署）。

## 3. Recorder 类

`fmo_repeater/service/recorder.py`：

```python
class Recorder:
    def __init__(self, config: Dict[str, Any], event_log: Optional[EventLog] = None)
    def handle(self, transmission: TransmissionCompleted) -> None
```

- 构造：读取 `recording` 节；`enabled=false` → 全方法 no-op（与 `EventLog` 同模式）；
  `enabled=true` → 创建输出根目录（`os.makedirs(exist_ok=True)`）
- `handle`：事件已经完成防循环、冲突仲裁与流边界判断；逐包解析后按 codec
  segment 解码并落盘。
- 线程模型：由 `TransmissionEventBus` 提供 Recorder 专属 FIFO 工作线程，
  Recorder 无需与 MQTT/Echo 共享锁，也不阻塞其他消费者。

### 3.1 内部状态

```python
_seq_counter: Dict[str, int]        # (callsign, 秒) → 已用序号，防文件名冲突
```

每次 `handle` 的局部状态为 `uid / callsign / stream_begin_utc / first_local /
segments`；`_Segment`：`codec / frames`。跨事件只保留文件名序号计数器。

## 4. 事件与编码段边界（决策 R1）

| 事件 | 行为 |
|---|---|
| 收到 `TransmissionCompleted` | 作为一个独立录音流 |
| 事件内 `compress_mode` 变化 | 当前 segment 收口，新 codec 追加新 segment |
| 停机时队列中未处理事件 | 遵循 Repeater 的立即取消语义，不额外冲刷 |

**备选否决**：Recorder 独立分流和超时——会与统一 ChannelCoordinator/Producer
状态漂移；OPUS/RADPCM 按帧多文件——过度设计，codec segment 足够。

## 5. 文件命名与目录布局（决策 R2）

```
{directory}/{callsign}/{YYYYMMDD-HHmmss}-{uid}-{seq:03d}-{codec}.{wav|opusraw}
```

示例：`recordings/BD8BOJ/20250618-143025-1234-001-RADPCM.wav`

- `YYYYMMDD-HHmmss`：流首包到达的**本地时间**
- `uid`：发送者 UID（十进制）
- `seq`：同一 `(callsign, 秒)` 下递增 3 位序号（跨 codec），启动后进程内累计
- `codec`：`RADPCM` / `OPUS`；**每个 segment 一个文件**（§4），故 seq 递增天然区分
- 降级（§6）扩展名 `.opusraw`，codec 段仍为 `OPUS`
- callsign 清洗：`re.sub(r'[^\w.-]', '_', callsign)`，空呼号用 `UNKNOWN`，
  结果 `.`/`..` 或空 → `UNKNOWN`（防路径穿越）

**备选否决**：seq 用时间戳后缀（同一秒内仍可能冲突）；全局单调 seq（跨呼号
重排后可读性差）。进程内 `(callsign, 秒)` 计数实现简单且确定性可测。

## 6. 解码与落盘（决策 R3/R4）

`finalize` 时对每个 segment：

- **RADPCM**：`RadpcmDecoder()` 逐帧 `decode(frame.to_bytes())`，PCM 顺序拼接 →
  `wave` 标准库写 WAV（8000Hz / sampwidth=2 / nchannels=1 / 非压缩）
- **OPUS + libopus 可用**：`OpusDecoder()` 逐帧解码（640B/帧）拼接 → 同上 WAV
- **OPUS + libopus 缺失**（`opus_is_available() is False`）：降级写 `.opusraw` ——
  逐帧拼接完整编码语音帧**含 8B 头**（事后可离线解码），事件记 `degraded: true`
- 空 segment（0 帧）跳过不落盘，不产生事件

**WAV 写入时机**：流结束一次性写盘（内存缓存编码帧）。60s 上限语音流
≈750 RADPCM 帧 × 336B ≈ 250KB，内存可控（决策 R4；备选"边收边增量写"需自行
维护 RIFF 头长度字段，复杂度不值）。

## 7. RepeaterService 集成

启用录音时由组合根注册独立消费者：

| 位置 | 调用 |
|---|---|
| `RepeaterService.__init__` | 构造 `Recorder(config, event_log)` |
| 事件总线注册 | `event_bus.subscribe("recorder", recorder.handle)` |
| `recording.enabled=false` | 不构造、不订阅，接收路径零额外处理 |

## 8. 事件模式扩展

复用 `EventLog`，新增事件（`docs/design/logging.md` 已预留扩展位）：

| event | 字段 |
|---|---|
| recording_stream_start | uid, callsign, stream_begin_utc |
| recording_saved | uid, callsign, codec, file, frames, duration_ms, bytes, degraded |
| recording_discarded | uid, callsign, reason（如 empty_segment） |

`degraded` 仅 OPUS 降级时出现（True）；`duration_ms` = Σ帧时长（40/80ms × 帧数）。

## 9. 错误处理

- 解码单帧异常：跳过该帧、计数，不中断整流落盘（`recording_saved.frames` 为
  实际成功帧数）
- 写盘 IO 异常：记 `recording_discarded`（reason=io_error）+ 运行日志 WARNING，
  服务继续运行
- `feed` 全程 try/except（与 EchoService `_on_message` 同级防御，不影响网络线程）

## 10. 测试设计

`tests/test_recorder.py`：

- 默认关闭：feed/finalize 全 no-op，无目录创建、无事件
- RADPCM 流：合成包 → WAV 落盘 → `wave` 读回验证（8000/16bit/mono、样本数
  = 640×帧数、内容与 RadpcmDecoder 直接解码一致）
- OPUS 流（有 libopus）：WAV 样本数 = 320×帧数；无 libopus → `.opusraw` 存在、
  字节流 = 编码帧序列（含 8B 头）、事件 `degraded: true`
- 事件边界：两个完成事件产出两组文件；同事件 codec 变化出两个 segment 文件
- 命名：同 (callsign, 秒) 两流 seq 001/002；非法呼号（空格/`..`/空）清洗
- 事件：recording_saved/discarded 字段完整
- RepeaterService 集成：mock MQTT 收包→完成事件→Recorder 消费并生成文件
- `test_config.py` 增补：recording 节默认值/校验（enabled bool、空 directory 拒绝）

## 11. 关键决策记录

| # | 决策 | 备选 | 理由 |
|---|---|---|---|
| R1 | 直接消费统一完成事件 | Recorder 独立超时判定 | 与信道仲裁共用唯一 PTT 边界 |
| R2 | `(callsign, 秒)` 内 3 位 seq 防文件名冲突 | 时间戳后缀/全局单调 seq | 同秒冲突确定性解决；命名可读、可测 |
| R3 | segment 按 codec 切分文件 | 混合流单文件/按帧多文件 | WAV 单一 codec；opusraw 降级粒度自然 |
| R4 | 流结束一次性写 WAV | 增量追加（自维护 RIFF 头） | 内存可控（≤~250MB 上限远低于语音流实际长度）；实现简单可靠 |
| R5 | `.opusraw` 保留 8B 编码帧头 | 裸载荷拼接 | 可事后离线逐帧解码与溯源 |
| R6 | 解码/写盘在锁外执行 | 全程持锁 | 不阻塞 MQTT 回调线程收包 |
