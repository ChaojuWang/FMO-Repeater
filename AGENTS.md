# AGENTS.md

本文件为 AI 编码代理在此代码库中工作时提供指导。

## 设计流程（SDD，文档先行 — 强制）

本仓库采用"文档先行"流程，详见 `docs/DESIGN_PROCESS.md`。规则：

1. **任何需求先写设计、再实现**：新需求在 `docs/changes/NNN-<slug>/` 创建增量设计（`proposal.md` → `design.md` → `tasks.md`）
2. **实现中发现设计缺陷**：先更新 `design.md`，再改代码
3. **迭代完成后**：将变更设计合并入 `docs/design/` 存量文档（标注 `Merged from changes/NNN`），proposal 标记 `status: merged`
4. `docs/design/` 是系统现状的单一事实源；`docs/design/legacy/` 为只读归档

## 项目概述

**FMO Repeater** — 基于 MQTT 的 FM Over Internet (FMO) 系统管理和工具服务，
对齐 FMO 语音数据开放协议 v1（https://bg5esn.com/docs/fmo-voice-codec-spec/）。

- **回音海螺（Echo）服务**：接收 FMO 语音消息包，重写头部后按原始时间轴重放
- **完整协议栈**：64B 消息头 / 传输帧 / 编码语音帧 / CRC32 / 聚合规则（MTU 1400B、250ms）
- **语音编解码**：RADPCM（IMA ADPCM 纯 Python）/ OPUS（opuslib 可选）
- **结构化日志**：JSONL 事件日志 + 文本运行日志
- **语音录制**：每个完成 PTT 一个文件，支持 OPUS 降级和目录保留策略

## 常用命令

### 安装依赖
```bash
pip install -r requirements.txt              # 运行依赖
pip install -r requirements-dev.txt          # 开发/测试（pytest）
```

### 运行服务
```bash
python main.py start                         # 前台
python main.py start --config /path/to.yaml  # 自定义配置
python main.py start --daemon                # 守护进程
python main.py stop | restart | status
python main.py --generate-config config.yaml # 生成配置模板
```

### 测试（pytest）
```bash
./run_tests.sh                               # 一键全部
python3 -m pytest tests/ -v                  # 等价
./run_tests.sh tests/test_codec_radpcm.py -q # 指定文件
```
OPUS 用例在缺少 opuslib/libopus 时自动 skip（不算失败）。

## 代码架构

```
fmo_repeater/
├── protocol/              # 协议层（纯数据结构，无 I/O）
│   ├── vendor.py          #   vendor 区间常量与校验
│   ├── common.py          #   MTU/聚合时长常量、ProtocolError
│   ├── header.py          #   MessageHeader（64B）
│   ├── frame.py           #   TransportFrame(8B) / EncodedVoiceFrame(8B)
│   └── packet.py          #   PacketParser / PacketBuilder（CRC32、聚合）
├── codecs/                # 编解码层（8kHz/16bit/mono PCM ↔ 编码载荷）
│   ├── radpcm.py          #   IMA ADPCM 完整实现（直流抑制/丢包恢复/旧格式兼容）
│   └── opus_codec.py      #   opuslib 封装（缺依赖优雅降级）
└── service/               # 服务层（I/O 与生命周期）
    ├── config.py          #   默认配置/加载/验证
    ├── logging_setup.py   #   运行日志
    ├── event_log.py       #   JSONL 事件日志
    ├── echo.py            #   EchoService（防循环/头重写/时间轴重放）
    ├── recorder.py        #   PTT 录音、原始降级与 rotate
    └── daemon.py          #   Unix 守护进程
main.py                    # CLI 入口
run_tests.sh               # 一键测试
tests/                     # pytest 套件（conftest + 分模块）
docs/                      # SDD：DESIGN_PROCESS.md / design/ / changes/
```

依赖方向：service → codecs → protocol（单向）。

## 协议要点（v1）

- 消息包 = 64B 头 + N 传输帧（8B 头 + 编码语音帧[8B 头 + 载荷]）
- 头部字段（小端）：version(2) vendor(4) uid(4) callsign(12) streamBeginUTC(4)
  timestamp(4) len(4) frameNum(2) checkSum(4) smeter(1) srvUID(4) reserved(19)
- checkSum = zlib.crc32，仅覆盖帧区（64B 之后）
- 聚合：追加后 >1400B 或聚合 >250ms 封包 → OPUS ≤6 帧/包、RADPCM ≤3 帧/包
- compressMode：1=OPUS（40ms）、2=RADPCM（80ms、328B 固定）
- vendor：0x0000-0x0FFF 保留区禁用；本项目用软件区 0x2000（配置项）

## Echo 服务关键机制

- **防循环**（不是旧的 UID=65535！）：接收包 `vendor==echo.vendor` 且
  `callsign` 以 `callsign_prefix` 开头 → 跳过；`echo.uid≠0` 时 UID 匹配也跳过
- **头重写**：vendor/呼号前缀/timestamp 更新，stream_begin_utc 与帧区、CRC 不动
- **重放**：按接收时间轴（绝对截止时间防漂移）
- **容错**：ProtocolError → 计数 + `packet_invalid` 事件，不中断

## 配置说明

`config.yaml`（模板 `config.yaml.example`）包含 mqtt / topics / transmission / echo /
recording / event_log / logging。录音默认关闭，启用时默认写入 `./recording`，
总量 `512M`、保留 `1w`。

## 开发约定

- **文档先行**：改功能先改 `docs/changes/` 设计（见顶部规则）
- 中文注释与文档字符串
- 测试放 `tests/`，pytest 风格，公共工具进 `tests/conftest.py`
- 完成迭代后合并设计入 `docs/design/` 并标记 merged
