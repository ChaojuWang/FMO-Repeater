# 协议层设计

> Merged from changes/001
> 上游规范：FMO 语音数据开放协议 v1（https://bg5esn.com/docs/fmo-voice-codec-spec/）

## 1. 消息包总览

```
消息包（≤1400B）
├─ 消息头（固定 64B）
└─ 传输帧 × N
     ├─ 传输帧头（8B）
     └─ 编码语音帧
          ├─ 编码语音帧头（8B）
          └─ 编码载荷（OPUS 变长 / RADPCM 固定 328B）
```

公共音频参数：8000Hz / 16-bit 有符号 / 单声道。

## 2. 消息头（64B，全部小端）

| 偏移 | 大小 | 字段 | struct |
|---|---|---|---|
| 0 | 2 | version（当前 1） | H |
| 2 | 4 | vendor | I |
| 6 | 4 | uid | I |
| 10 | 12 | callsign（UTF-8 补 0） | 12s |
| 22 | 4 | stream_begin_utc（UTC ms） | I |
| 26 | 4 | timestamp（UTC ms） | I |
| 30 | 4 | length（整包含头） | I |
| 34 | 2 | frame_num | H |
| 36 | 4 | checksum（CRC32 帧区） | I |
| 40 | 1 | smeter | B |
| 41 | 4 | srv_uid | I |
| 45 | 19 | reserved | 19s |

实现：`MessageHeader`（`fmo_repeater/protocol/header.py`），format `<HII12sIIIHIBI19s`。
- version != 1 不拒收（前向兼容）
- callsign：UTF-8 截断 12B、右侧补 0、解码 errors='replace'
- `copy_with(**fields)`：Echo 头重写用

## 3. 帧结构

传输帧（8B 头）：`index(H, 从 1 递增) length(H, 含头) reserved(4s) data`
编码语音帧（8B 头）：`compress_mode(B) length(H, 含头) reserved(5s) raw`

compress_mode：0=PCM（预留不实现，决策 D3）、1=OPUS、2=RADPCM。
帧时长：OPUS 40ms / RADPCM 80ms（`frame_duration_ms`）。

## 4. 组包与拆包

**PacketParser.parse**（校验顺序）：
1. `len(data) >= 64` 且 `header.length == len(data)` → 否则 `bad_length`
2. `zlib.crc32(帧区) == header.checksum` → 否则 `bad_checksum`
3. 帧区恰好切分为 frame_num 个传输帧、index 连续递增 → 否则 `bad_index`/`bad_frame_num`

**PacketBuilder**（聚合规则，规范 §3.3）：
- 追加帧后 `64 + Σ(8+帧长) > 1400`（MTU）或聚合时长 `> 250ms` → 封包返回并重置缓冲
- 效果：每包 OPUS ≤6 帧、RADPCM ≤3 帧
- `stream_begin_utc` 取缓冲首帧时刻；封包时填 `timestamp`/`length`/`frame_num`/`checksum`
- 空缓冲时单帧仍超限 → 立即封包（防死锁）
- `flush()`：流结束冲刷剩余

## 5. vendor 体系

| 区间 | 语义 | 校验 |
|---|---|---|
| 0x0000-0x0FFF | 保留区（FMO 项目方） | `is_vendor_valid()==False`，配置校验拒绝 |
| 0x1000-0x1FFF | 实验区 | 自由取用 |
| 0x2000-0x2FFF | 软件区 | 自由取用（本项目默认 0x2000） |
| 0x3000+ | 正式区 | 登记制（代码仅判区间） |

## 6. 异常

单一 `ProtocolError(ValueError)`（决策 D5），`reason` ∈
{too_short, bad_length, bad_checksum, bad_frame_num, bad_index,
bad_length_frames, bad_compress_mode, frame_too_long}。

## 7. 未实现（预留）

- §8 PTT 仲裁（1500ms 路由窗口/抢占规则）：字段（uid/stream_begin_utc）已可解析，
  机制本身面向设备端，Echo 场景不需要；留作后续 change。
