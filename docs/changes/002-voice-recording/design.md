# 详细设计：语音录制功能（Voice Recording）

> status: designed（设计完成，待实施）
> 变更编号：002 ｜ 上游提案：proposal.md ｜ 依赖：变更 001（已合并）

## 1. 概述

在 Echo 服务收包路径上新增 Recorder 组件：按发送者分流缓存编码语音帧，
流结束后解码为 WAV（8kHz/16bit/mono）落盘，录制事件写入 JSONL 事件日志。

**默认关闭**（`recording.enabled: false`），不影响现有部署；关闭时零开销 no-op。

依赖的变更 001 成果：

| 依赖 | 用途 |
|---|---|
| `ParsedPacket`（protocol/packet.py） | 已解析消息包：header 元数据 + frames（每帧含 encoded） |
| `RadpcmDecoder`（codecs/radpcm.py） | RADPCM 帧 → 1280B PCM（含丢包恢复） |
| `OpusDecoder` / `opus_is_available()`（codecs/opus_codec.py） | OPUS 帧 → 640B PCM / 依赖探测 |
| `EventLog`（service/event_log.py） | 结构化录制事件 |
| `EchoService` 流结束判定（service/echo.py） | 复用超时判定触发 finalize |

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
    def feed(self, packet: ParsedPacket) -> None       # 收包路径调用（MQTT 回调线程）
    def finalize(self) -> None                          # 流结束调用（主循环/停止路径）
```

- 构造：读取 `recording` 节；`enabled=false` → 全方法 no-op（与 `EventLog` 同模式）；
  `enabled=true` → 创建输出根目录（`os.makedirs(exist_ok=True)`）
- `feed`：防循环过滤**不在 Recorder 内做**（由 EchoService 在调用前完成，
  只喂有效包）；流切分判定见 §4
- `finalize`：将当前流落盘并清空状态；服务 `stop()` 时也调用（残留流尽量保存）
- 线程模型：`feed`（MQTT 回调线程）与 `finalize`（主循环线程）并发 → 内部
  `threading.Lock` 保护；落盘（解码+写文件）在锁外执行避免阻塞收包，锁内仅做
  队列交接（取出流数据 → 清空状态 → 锁外写盘）

### 3.1 内部状态

```python
_current: Optional[_StreamBuffer]   # 当前流缓冲，None 表示空闲
_seq_counter: Dict[str, int]        # (callsign, 秒) → 已用序号，防文件名冲突
```

`_StreamBuffer`：`uid / callsign / stream_begin_utc / first_local: datetime /
segments: List[_Segment]`；`_Segment`：`codec: int / frames: List[EncodedVoiceFrame]`。

## 4. 流切分与结束判定（决策 R1）

| 事件 | 行为 |
|---|---|
| `feed` 且无当前流 | 开新流（记录首包本地时间） |
| `stream_begin_utc` 与当前流不同 | 先 finalize 旧流，再开新流 |
| 流内 `compress_mode` 变化 | 当前 segment 收口，新 codec 追加新 segment（不整流切分） |
| EchoService 超时判定流结束 / 服务停止 | `finalize()` 整流落盘 |

**备选否决**：Recorder 独立线程独立超时判定——与 Echo 超时重复且时间轴可能不一致；
OPUS 40ms/RADPCM 80ms 混合流按帧切多文件——过度设计，段内切分足够。

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

## 7. EchoService 集成

`echo.py` 三处埋点（recording.enabled=false 时零开销）：

| 位置 | 调用 |
|---|---|
| `__init__` | `self.recorder = Recorder(config, event_log)` |
| `_on_message` 有效包缓存后 | `self.recorder.feed(packet)` |
| `_check_timeout` 触发重放前后 / `stop()` | `self.recorder.finalize()` |

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
- 流切分：stream_begin_utc 变化出两文件；同流 codec 变化出两 segment 两文件
- 命名：同 (callsign, 秒) 两流 seq 001/002；非法呼号（空格/`..`/空）清洗
- 事件：recording_saved/discarded 字段完整
- EchoService 集成：mock MQTT 收包→超时→feed/finalize 被调用且文件生成
- `test_config.py` 增补：recording 节默认值/校验（enabled bool、空 directory 拒绝）

## 11. 关键决策记录

| # | 决策 | 备选 | 理由 |
|---|---|---|---|
| R1 | 复用 EchoService 超时判定流结束 | Recorder 独立线程判定 | 同一时间轴；避免双超时状态机漂移 |
| R2 | `(callsign, 秒)` 内 3 位 seq 防文件名冲突 | 时间戳后缀/全局单调 seq | 同秒冲突确定性解决；命名可读、可测 |
| R3 | segment 按 codec 切分文件 | 混合流单文件/按帧多文件 | WAV 单一 codec；opusraw 降级粒度自然 |
| R4 | 流结束一次性写 WAV | 增量追加（自维护 RIFF 头） | 内存可控（≤~250MB 上限远低于语音流实际长度）；实现简单可靠 |
| R5 | `.opusraw` 保留 8B 编码帧头 | 裸载荷拼接 | 可事后离线逐帧解码与溯源 |
| R6 | 解码/写盘在锁外执行 | 全程持锁 | 不阻塞 MQTT 回调线程收包 |
