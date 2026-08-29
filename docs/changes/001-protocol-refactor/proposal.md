# 提案：对齐 FMO 语音数据开放协议 v1 的全面重构

> status: merged（已合并入 docs/design/，2025 迭代一完成）
> 变更编号：001
> 上游规范：https://bg5esn.com/docs/fmo-voice-codec-spec/

## 1. 动机

FMO 项目方公布了语音数据开放协议 v1，定义了完整的线格式：消息包（64B 头）→ 传输帧（8B 头）→ 编码语音帧（8B 头）→ 编码载荷（OPUS/RADPCM）。当前实现基于过时的 22B 头部假设：

- **头部不符**：旧 22B 结构与新 64B 线格式完全不兼容，无法解析真实 FMO 消息
- **防循环失效**：旧机制依赖 UID=65535 识别自己重放的消息；新协议 UID 为 4 字节真实用户 ID，65535 不再具有 Echo 语义
- **无帧结构**：旧实现把整个载荷当黑盒，无法感知传输帧/编码帧边界、CRC 校验、聚合规则
- **无编解码能力**：无法验证载荷正确性，也无法支撑后续录音等功能

## 2. 目标

1. 按规范实现完整协议层：64B 消息头、传输帧、编码语音帧、CRC32 帧区校验、聚合规则（MTU 1400B / 250ms）、vendor 体系
2. 实现语音编解码层：RADPCM（IMA ADPCM）完整编解码；OPUS 基于系统 libopus（缺失时优雅降级）
3. Echo 服务基于新协议重写，建立新的防循环机制（vendor + 呼号前缀）
4. 日志重构：运行日志（文本，轮转）+ 结构化事件日志（JSONL）
5. 测试体系迁移到 pytest + `run_tests.sh` 一键脚本
6. 代码包化：`fmo_repeater` 包（protocol / codecs / service 分层）
7. 建立 SDD 文档流程（本目录）

## 3. 范围

### 做
- `fmo_repeater/protocol/`：header / frame / packet / vendor
- `fmo_repeater/codecs/`：radpcm / opus_codec
- `fmo_repeater/service/`：config / echo / logging_setup / event_log / daemon
- `main.py`、`config.yaml.example`、`requirements.txt`、`run_tests.sh`
- `tests/`：pytest 全套重写
- `docs/`：SDD 体系建立

### 不做（非目标）
- §8 PTT 仲裁（1500ms 路由窗口）——面向设备端，Echo 在发送者停止后才重放；协议字段已支持，留作后续 change
- 录音功能——拆分为迭代二 `docs/changes/002-voice-recording/`
- MQTT TLS、鉴权体系变更
- Windows 守护进程支持

## 4. 关键决策摘要

| 决策 | 选择 | 理由 |
|---|---|---|
| vendor ID | `0x2000`（软件区，可配） | 本项目为软件实现，软件区自由取用无需登记 |
| 防循环机制 | vendor==本机 && 呼号以 `RE>` 前缀开头 → 跳过 | UID=65535 语义已失效；双条件降低误判 |
| 包结构 | `fmo_repeater` 三层子包 | 协议/编解码/服务关注点分离 |
| 测试框架 | pytest + `run_tests.sh` | 用户明确要求；脚本作为一键入口 |
| CRC | zlib.crc32，仅覆盖帧区（64B 之后） | 规范 §2.2 |
| timestamp 语义 | UTC ms，uint32 回绕 | 规范原文 "UTC ms"，照抄协议字段宽度 |

## 5. 里程碑与验收

见 `tasks.md`。
