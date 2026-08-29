# 总体架构

> Merged from changes/001

## 1. 分层

```
┌────────────────────────────────────────────┐
│ service  (I/O 与生命周期)                   │
│   config / logging_setup / event_log       │
│   echo / daemon                            │
├────────────────────────────────────────────┤
│ codecs   (PCM16 8kHz mono ↔ 编码载荷)      │
│   radpcm (IMA ADPCM, 纯 Python)            │
│   opus_codec (opuslib, 可选)               │
├────────────────────────────────────────────┤
│ protocol (纯数据结构，无 I/O)              │
│   vendor / header / frame / packet         │
└────────────────────────────────────────────┘
```

依赖方向：service → codecs → protocol（单向）。protocol 不依赖任何外部库；
codecs 仅 opus_codec 依赖 opuslib（可选）；service 依赖 paho-mqtt、PyYAML。

## 2. 模块清单

| 模块 | 职责 |
|---|---|
| `fmo_repeater/protocol/vendor.py` | vendor 区间常量与校验 |
| `fmo_repeater/protocol/header.py` | 64B 消息头（解析/序列化/copy_with） |
| `fmo_repeater/protocol/frame.py` | 传输帧（8B）与编码语音帧（8B） |
| `fmo_repeater/protocol/packet.py` | PacketParser / PacketBuilder（CRC、聚合） |
| `fmo_repeater/codecs/radpcm.py` | RADPCM 编解码（含直流抑制、丢包恢复） |
| `fmo_repeater/codecs/opus_codec.py` | OPUS 封装（缺依赖优雅降级） |
| `fmo_repeater/service/config.py` | 默认配置/加载/验证/模板 |
| `fmo_repeater/service/logging_setup.py` | 运行日志（文本轮转） |
| `fmo_repeater/service/event_log.py` | JSONL 结构化事件日志 |
| `fmo_repeater/service/echo.py` | Echo 服务（防循环、头重写、时间轴重放） |
| `fmo_repeater/service/daemon.py` | Unix 守护进程（自旧实现迁移） |
| `main.py` | CLI 入口（start/stop/restart/status） |

## 3. 数据流

```
MQTT 订阅 → _on_message
  → PacketParser.parse（校验 len/CRC/帧数/index）
  → 防循环判定（vendor + callsign 前缀 [+ uid]）
  → 缓存 (ParsedPacket, monotonic)
  → 超时无新消息 → 流结束
     → _rewrite_packet（vendor/呼号/timestamp；帧区与 CRC 不动）
     → 按接收时间轴逐包 publish（绝对截止时间防漂移）
```

## 4. 设计决策

见 `docs/changes/001-protocol-refactor/design.md` §7（D1-D7）。
