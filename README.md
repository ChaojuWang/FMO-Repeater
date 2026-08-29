# FMO Repeater

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)

FMO Repeater 是一个基于 MQTT 的 FM Over Internet (FMO) 系统管理和工具服务，
对齐 [FMO 语音数据开放协议 v1](https://bg5esn.com/docs/fmo-voice-codec-spec/)。

## 📖 项目简介

FMO（FM Over Internet）是通过互联网中继 FM 信号的设备。本项目为 FMO 网络提供
中继器管理与工具服务，当前核心功能：

- **回音海螺（Echo）服务**：接收 FMO 语音消息包，重写头部（vendor/呼号前缀）后
  按原始时间轴重放，实现回声测试
- **完整协议栈**：64B 消息头 / 传输帧 / 编码语音帧解析与构造，CRC32 校验，
  MTU 1400B + 250ms 聚合规则
- **语音编解码**：RADPCM（IMA ADPCM，纯 Python 完整实现）与 OPUS（libopus）
- **结构化事件日志**：JSONL 格式，独立于运行日志
- **可扩展架构**：protocol / codecs / service 三层分包

## 🚀 快速开始

### 环境要求

- Python 3.8+
- MQTT 代理服务器（EMQX、Mosquitto 等）
- （可选）系统 libopus——启用 OPUS 编解码测试

### 安装

```bash
git clone https://github.com/ChaojuWang/FMO-Repeater.git
cd FMO-Repeater
pip install -r requirements.txt

# 配置
cp config.yaml.example config.yaml
vim config.yaml
```

### 运行

```bash
python main.py start                    # 前台模式
python main.py start --config my.yaml   # 自定义配置
python main.py start --daemon           # 后台守护进程
python main.py stop | restart | status  # 守护进程管理
python main.py --generate-config out.yaml  # 生成配置模板
```

## 🧪 测试

```bash
./run_tests.sh                          # 一键运行（pytest）
./run_tests.sh tests/test_codec_radpcm.py -q  # 指定文件/参数
```

覆盖：协议头/帧/组包拆包（CRC、聚合上限）、vendor 区间、RADPCM 编解码
（SNR、丢包恢复、旧格式兼容）、OPUS（缺依赖自动 skip）、Echo 防循环矩阵、
配置校验、JSONL 事件日志。117 个用例。

## ⚙️ 配置要点

```yaml
echo:
  timeout: 5.0         # 流结束超时（秒）
  vendor: 0x2000       # 重放 vendor：软件区 0x2000-0x2FFF 自由取用
                       # 严禁保留区 0x0000-0x0FFF；正式区 0x3000+ 需登记
  uid: 0               # 重放 UID（0 = 保持原值）
  callsign_prefix: 'RE>'

event_log:             # JSONL 结构化事件日志
  enabled: true
  file: logs/events.jsonl
```

**防循环机制**：重放包携带本服务 vendor + 呼号前缀；接收侧据此跳过自己的
回声（`vendor` 匹配且呼号以 `RE>` 开头，或 UID 匹配），不会无限转发。

## 🏗️ 架构

```
fmo_repeater/
├── protocol/          # 协议层：vendor / header(64B) / frame / packet(组包拆包/CRC/聚合)
├── codecs/            # 编解码：radpcm(IMA ADPCM) / opus_codec(可选)
└── service/           # 服务：config / echo / event_log(JSONL) / logging_setup / daemon
```

详细设计见 [docs/design/](docs/design/)（SDD 流程见
[docs/DESIGN_PROCESS.md](docs/DESIGN_PROCESS.md)，增量变更见
[docs/changes/](docs/changes/)）。

## 🔒 安全考虑

- 生产环境建议 MQTT TLS 与凭据管理（当前配置文件明文，勿提交 config.yaml）
- 事件日志含呼号/UID 等通联元数据，注意磁盘与隐私管理

## 📄 许可证

MIT License，见 [LICENSE](LICENSE)。

## 🙏 致谢

- [FMO 协议作者 BG5ESN](https://bg5esn.com/) 与 [协议规范](https://bg5esn.com/docs/fmo-voice-codec-spec/)
- [Paho MQTT Python](https://www.eclipse.org/paho/clients/python/)
- [PyYAML](https://pyyaml.org/) / [opuslib](https://github.com/Ensegrest/opuslib)
