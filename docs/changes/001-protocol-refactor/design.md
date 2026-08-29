# 详细设计：FMO 协议 v1 重构

> status: merged（已合并入 docs/design/{architecture,protocol,codecs,service,logging,testing}.md）
> 变更编号：001 ｜ 上游规范：https://bg5esn.com/docs/fmo-voice-codec-spec/

## 1. 总体架构

```
fmo_repeater/
├── __init__.py
├── protocol/            # 协议层（纯数据结构，无 I/O）
│   ├── __init__.py
│   ├── vendor.py        # vendor 区间常量与校验
│   ├── header.py        # MessageHeader（64B）
│   ├── frame.py         # EncodedVoiceFrame（8B）/ TransportFrame（8B）
│   └── packet.py        # PacketParser / PacketBuilder（组包、拆包、CRC、聚合）
├── codecs/              # 编解码层（PCM16 8kHz mono ↔ 编码载荷）
│   ├── __init__.py
│   ├── radpcm.py        # RADPCM（IMA ADPCM）完整实现
│   └── opus_codec.py    # OPUS 封装（opuslib，可选依赖）
└── service/             # 服务层（I/O 与生命周期）
    ├── __init__.py
    ├── config.py        # 默认配置/加载/验证
    ├── logging_setup.py # 运行日志（文本轮转）
    ├── event_log.py     # 结构化事件日志（JSONL）
    ├── echo.py          # Echo 服务
    └── daemon.py        # Unix 守护进程（迁移）
```

分层依赖：service → codecs → protocol（单向）。

## 2. 协议层（protocol/）

### 2.1 vendor.py

```python
VENDOR_RESERVED_MIN, VENDOR_RESERVED_MAX = 0x0000, 0x0FFF   # 保留区（FMO 项目方），禁用
VENDOR_EXPERIMENTAL_MIN, VENDOR_EXPERIMENTAL_MAX = 0x1000, 0x1FFF
VENDOR_SOFTWARE_MIN, VENDOR_SOFTWARE_MAX = 0x2000, 0x2FFF
VENDOR_FORMAL_MIN = 0x3000                                   # 正式区（登记制）
VENDOR_FMO = 0x0000

def vendor_zone(v: int) -> str          # 'reserved'/'experimental'/'software'/'formal'
def is_vendor_valid(v: int) -> bool     # 保留区 → False（严禁使用）
```

### 2.2 header.py — MessageHeader（64B）

struct 布局（全部小端）：

| 偏移 | 大小 | 字段 | 类型 | 说明 |
|---|---|---|---|---|
| 0 | 2 | version | H | 包版本，当前 1 |
| 2 | 4 | vendor | I | 厂家标识 |
| 6 | 4 | uid | I | 发送者用户 ID |
| 10 | 12 | callsign | 12s | 呼号，不足补 0 |
| 22 | 4 | stream_begin_utc | I | 语音流起始 UTC ms |
| 26 | 4 | timestamp | I | 本包生成 UTC ms |
| 30 | 4 | length | I | 整包长度（含头） |
| 34 | 2 | frame_num | H | 传输帧个数 |
| 36 | 4 | checksum | I | CRC32，仅覆盖帧区 |
| 40 | 1 | smeter | B | S 表强度 |
| 41 | 4 | srv_uid | I | 当前服务器 UID |
| 45 | 19 | reserved | 19s | 扩展区 |

- format：`<HIHI II I H I B I 19s`（去空格 `<HIIHIIHIIBI19s`）
- `MessageHeader.from_bytes(data)`：不足 64B → `ProtocolError`；version != 1 记录但不拒收（前向兼容）
- `to_bytes()`、`copy_with(**fields)`（重写字段后重新打包）
- callsign 编解码沿用旧行为：UTF-8、右侧空字节填充、`errors='replace'` 截断 12B

### 2.3 frame.py

**EncodedVoiceFrame（编码语音帧，8B 头）**

| 偏移 | 大小 | 字段 | 类型 |
|---|---|---|---|
| 0 | 1 | compress_mode | B（0=PCM 预留, 1=OPUS, 2=RADPCM） |
| 1 | 2 | length | H（含头总长） |
| 3 | 5 | reserved | 5s |
| 8 | - | raw | 编码载荷 |

**TransportFrame（传输帧，8B 头）**

| 偁移 | 大小 | 字段 | 类型 |
|---|---|---|---|
| 0 | 2 | index | H（从 1 递增） |
| 2 | 2 | length | H（含头总长） |
| 4 | 4 | reserved | 4s |
| 8 | - | payload | 编码语音帧 |

两者均提供 `from_bytes` / `to_bytes`，长度不符 → `ProtocolError`。

### 2.4 packet.py

**PacketParser.parse(data: bytes) -> ParsedPacket**

校验顺序：
1. `len(data) >= 64` 且 `header.length == len(data)`（不匹配 → `ProtocolError`，不抛异常的容错需求由调用方捕获）
2. CRC32(帧区) == header.checksum
3. 按帧边界遍历：`frame_num` 个传输帧，index 递增（乱序容忍但要求连续递增），每帧内含一个编码语音帧

产物 `ParsedPacket`（dataclass）：`header` + `frames: list[TransportFrame]`（每个含 `encoded: EncodedVoiceFrame`）。

**PacketBuilder**（聚合规则，规范 §3.3）

- `PacketBuilder(vendor, uid, callsign, srv_uid=0, smeter=0)`
- `add_frame(encoded: EncodedVoiceFrame) -> bytes | None`：追加后若 `64 + Σ(8+len) > 1400` 或聚合时长 `≥ 250ms`（OPUS 40ms×6=240ms 后再补一帧即 280ms>250 触发；RADPCM 80ms×3=240ms 后再补即 320ms>250 触发——以编码帧自带时长计）则封包返回完整消息包 bytes 并清空缓冲；否则返回 None
- 时长计算：`encoded.duration_ms`（OPUS=40, RADPCM=80；PCM 预留=0）
- 封包时填 `stream_begin_utc`（首帧入缓冲的 UTC ms）、`timestamp`（封包时刻）、`length`、`frame_num`、`checksum`（zlib.crc32 帧区）

## 3. 编解码层（codecs/）

### 3.1 radpcm.py（RADPCM / IMA ADPCM）

- 常量：`IMA_STEP_TABLE`（89 级，规范原文照录）、`IMA_INDEX_TABLE`（16 项）
- `SAMPLE_RATE=8000, SAMPLES_PER_FRAME=640, FRAME_MS=80, FRAME_BYTES=328, DATA_BYTES=320`
- **帧结构**（328B）：`frame_index(H) recover_pcm(h) step_index(B) reserved(B) adpcm_bytes(H) data(320s)`，format `<HhBBH320s`
- **nibble 序**：每字节高 4 位为第 1 个样本，低 4 位为第 2 个
- **编码**：输入 PCM16 → 先做直流抑制 `y[n] = x[n] - x[n-1] + (R·y[n-1])`，R=0.999（Q15=32735，定点 `(x - x_prev) + ((y_prev * 32735) >> 15)`）→ IMA 编码；帧恢复点记录本帧编码前的 predictor/step_index
- **解码**：先丢包检测（last_frame==0xFFFF 首帧 / frame_index==0 新序列 / frame_index != last+1 丢包 → 用帧恢复点重置），再 IMA 解码，最后做直流抑制的逆（还原 `x[n] = y[n] + x[n-1] - R·y[n-1]` 量级还原）——注意：规范直流抑制标注"编码器侧"，解码输出即编码器输入域，解码器需还原直流。设计选择：**编码器抑制、解码器还原**（见 §3.1.1 决策）。

#### 3.1.1 决策：直流抑制的位置

规范 §6.2 将直流抑制标注为"编码器侧"。两种解读：
- A. 编码抑制 + 解码还原（往返无损于原始语音的直流）
- B. 编码抑制 + 解码不还原（线格式仍互通，但往返语音缺失直流分量）

选 **A**：往返测试可验证编解码质量；线格式不受影响（raw 区字节相同）。逆滤波公式：`x[n] = y[n] + x[n-1] - R·y[n-1]`。

- **adpcmBytes 兼容**：新实现按 uint16 读写（=320）；解析旧包时若高字节非零且低字节==64 则视为旧 8-bit 字段（线值 64），按 320 数据区处理
- `RadpcmEncoder`：有状态（predictor/step_index 跨帧延续），`encode(pcm: bytes) -> bytes`（328B 帧）
- `RadpcmDecoder`：有状态（last_frame_index + IMA 状态），`decode(frame: bytes) -> bytes`（1280B PCM）
- 无状态便捷函数 `encode_frame/decode_frame`（单帧独立编解码，帧恢复点即初始状态）

### 3.2 opus_codec.py

- 依赖 `opuslib`（PyPI，绑定系统 libopus）；`opuslib` 导入失败 → 模块级 `OpusUnavailable` 异常类 + `is_available()` 探测函数，编解码器构造时抛出
- `OpusEncoder`：8000Hz/mono/`APPLICATION_VOIP`/bitrate AUTO/complexity 4/`SIGNAL_VOICE`/VBR/40ms(320 样本)
- `OpusDecoder`：8000Hz/mono/40ms
- `encode(pcm) -> bytes`（变长）、`decode(data) -> bytes`（定长 640B）；`decode` 容忍空 data（DTX/丢包 → 舒适噪声帧）

## 4. 服务层（service/）

### 4.1 config.py

默认配置（新增节加粗）：

```yaml
mqtt: {broker, port, username, password, client_id_prefix, keepalive}
topics: {subscribe, publish}
echo:
  timeout: 5.0          # 流结束判定超时（秒）
  vendor: 0x2000        # 重放时写入的 vendor（软件区）
  uid: 0                # 重放时写入的 UID（0 表示保持原值）
  callsign_prefix: 'RE>'
**event_log**:
  **enabled: true**
  **file: logs/events.jsonl**
  **max_bytes: 10485760**
  **backup_count: 5**
logging: {level, console, file, max_bytes, backup_count}
daemon: {enabled, pid_file}
```

- `load_config`/`validate_config`/`save_default_config` 迁移自旧实现；validate 增加：echo.vendor 不得位于保留区（0x0000-0x0FFF）、event_log 参数类型校验
- 保持 `deep_merge` 语义不变

### 4.2 echo.py — EchoService

职责：订阅 → 解析 → 缓存 → 流结束（超时）→ 重写头 → 按原始时间轴重放。

- **防循环**（新机制，替代 UID=65535）：
  - 跳过条件（任一）：`header.vendor == 本机重放 vendor 且 callsign.startswith(prefix)`；或 `uid != 0 且 header.uid == 本机重放 uid`
  - 默认 `echo.uid=0`（保持原值）时仅靠 vendor+prefix 双条件，避免误伤真实用户
- **头部重写**：`vendor → echo.vendor`、`callsign → prefix + 原呼号`、`uid → 原值或 echo.uid`、`timestamp → now`；`stream_begin_utc`/帧区/CRC 不变（帧区不动 CRC 无需重算）
- **重放时间轴**：沿用旧实现绝对截止时间算法（防漂移）
- **事件日志埋点**：stream_start / packet_received / replay_started / replay_finished / packet_dropped / loop_detected，字段见 §5.2
- **解析容错**：`ProtocolError` → 计数 + 事件日志 `packet_invalid`，不中断服务
- MQTT：paho v2 回调 API（CallbackAPIVersion.VERSION2），on_connect 订阅、自动重连

### 4.3 logging_setup.py / event_log.py

- `logging_setup`：迁移旧 `_setup_logging`，接受 config dict，返回配置好的 Logger（文本、RotatingFileHandler）
- `EventLog`：JSONL 追加写；`log(event: str, **fields)`；字段顺序 `ts/event/...`；按字节轮转（RotatingFileHandler 模式，行完整性由单行 write 保证）；`enabled=false` 时全 no-op

### 4.4 daemon.py

从旧 `daemon.py` 原样迁移（双 fork、PID 文件、信号处理），仅更新导入路径。

## 5. 数据结构与事件模式

### 5.1 ParsedPacket

```python
@dataclass
class ParsedPacket:
    header: MessageHeader
    frames: list[TransportFrame]   # 每个 .encoded: EncodedVoiceFrame
```

### 5.2 事件模式（events.jsonl 每行一个 JSON 对象）

| event | 字段 |
|---|---|
| service_started | version, vendor, subscribe_topic, echo_timeout |
| stream_start | uid, callsign, vendor, stream_begin_utc, frames |
| packet_received | uid, callsign, vendor, frames, bytes, checksum_ok |
| packet_invalid | reason, bytes |
| loop_detected | uid, callsign, vendor |
| replay_started | uid, callsign, packets |
| replay_finished | uid, callsign, packets, ok, failed |
| stream_end | uid, callsign, packets, duration_s |
| service_stopped | reason |

固定字段 `ts`（ISO8601 本地时间）在首位。

## 6. 测试设计（tests/）

- `conftest.py`：sys.path 注入；fixtures：`pcm_sine`（8kHz 16bit 正弦）、`radpcm_frame`、`make_packet`（合成完整消息包工厂，支持指定 uid/callsign/vendor/codec/帧数）
- `test_protocol_header.py`：roundtrip、每字段编解码、短数据/坏 CRC/坏 length 拒收、version 容错、callsign 边界（12B 截断/填充/非法 UTF-8）
- `test_protocol_packet.py`：builder 聚合上限（1400B、250ms：OPUS 6 帧/RADPCM 3 帧）、stream_begin_utc/timestamp/checksum/frame_num 正确性、parser+builder roundtrip、乱序 index、CRC 校验、多包序列
- `test_codec_radpcm.py`：单帧 roundtrip SNR（正弦/噪声，阈值 ≥20dB，见 tasks）、多帧状态延续、丢包恢复（跳帧后仍可解）、frame_index 回绕、旧 8-bit adpcmBytes 兼容、直流抑制往返
- `test_codec_opus.py`：roundtrip（缺 opuslib 自动 `pytest.skip`）、变长帧、DTX 空帧
- `test_echo_service.py`：mock MQTT，防循环双条件、头重写字段、CRC/帧区不变、时间轴重放顺序、解析容错计数
- `test_config.py`：默认值、深合并、vendor 保留区拒绝、event_log 校验
- `test_event_log.py`：JSONL 写入/解析、轮转、禁用 no-op

## 7. 关键决策记录

| # | 决策 | 备选 | 理由 |
|---|---|---|---|
| D1 | 防循环 vendor+prefix 双条件 | 仅 vendor；仅 prefix | 单条件误判率高（他人软件区同 vendor；他人恰好用 RE> 前缀）|
| D2 | 直流抑制编码抑制+解码还原 | 解码不还原 | 往返可验证；线格式不变 |
| D3 | PCM compressMode=0 仅枚举不实现 | 实现 | 规范明确"预留，不用于网络传输" |
| D4 | opuslib 可选依赖 | 强制 | libopus 缺失不应阻塞 RADPCM/协议/Echo 功能 |
| D5 | ProtocolError 单一异常类 | 分层异常 | 调用方只需 catch 一处；reason 属性携带细节 |
| D6 | timestamp/streamBeginUTC uint32 秒级处理不足，按 ms 原样透传 | 缩放 | 协议字段宽度固定，透传语义最安全 |
| D7 | RADPCM 验收 SNR 阈值 ≥20dB（去直流） | ≥30dB | 4-bit IMA ADPCM 理论水平 19-27dB；实测满幅正弦 26.8dB / 多音+噪 22.9dB，30dB 不可达。阈值取理论下界留裕量 |
