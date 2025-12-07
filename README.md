# FMO Repeater

[![Python Version](https://img.shields.io/badge/python-3.8%2B-blue.svg)](https://python.org)
[![License](https://img.shields.io/badge/license-MIT-green.svg)](LICENSE)
[![Build Status](https://img.shields.io/badge/build-passing-brightgreen.svg)]()

FMO Repeater 是一个基于 MQTT 的 FM Over Internet (FMO) 系统管理和工具服务，为业余无线电网络中继提供完整的管理解决方案。

## 📖 项目简介

FMO（FM Over Internet）是一种通过网络中继 FM 信号的设备，允许业余无线电爱好者通过互联网进行远程通信。FMO Repeater 为这些中继器提供了一套完整的管理和工具服务，包括：

### 🎯 核心功能

- **回音海螺（Echo）服务**：接收 FMO 消息，修改头部后重新发送，实现回声测试功能
- **消息管理和转发**：智能消息路由和转发功能
- **中继器状态监控**：实时监控和管理 FMO 中继器网络
- **配置和日志管理**：统一的服务配置管理和日志记录系统

### 🚀 特性

- 🐍 **Python 驱动**：纯 Python 实现，跨平台兼容
- 📡 **MQTT 支持**：基于标准 MQTT 协议的可靠消息传输
- 🔧 **配置灵活**：YAML 配置文件，支持环境变量覆盖
- 📝 **日志完善**：多级别日志系统，支持文件轮转
- 🛡️ **守护进程**：支持后台守护进程模式（Unix/Linux）
- 🧪 **测试覆盖**：完整的测试套件，确保服务稳定可靠

## 🛠️ 快速开始

### 环境要求

- Python 3.8 或更高版本
- MQTT 代理服务器（如 EMQX、Mosquitto 等）
- pip 包管理器

### 安装步骤

1. **克隆仓库**
   ```bash
   git clone https://github.com/your-username/fmo-repeater.git
   cd fmo-repeater
   ```

2. **安装依赖**
   ```bash
   pip install -r requirements.txt
   ```

3. **配置服务**
   ```bash
   # 复制配置文件模板
   cp config.yaml.example config.yaml

   # 编辑配置文件
   vim config.yaml
   ```

4. **启动服务**
   ```bash
   # 前台模式（推荐用于测试）
   python main.py start

   # 后台守护进程模式（Unix/Linux）
   python main.py start --daemon
   ```

### 配置说明

主要配置项位于 `config.yaml` 文件中：

```yaml
mqtt:
  broker: "your-mqtt-broker.com"  # MQTT 代理地址
  port: 1883                       # MQTT 端口
  username: "your_username"        # 用户名
  password: "your_password"        # 密码

topics:
  subscribe: "FMO/RAW"  # 订阅的主题
  publish: "FMO/RAW"    # 发布的主题

repeater:
  echo:
    timeout: 2.0              # Echo 超时时间（秒）
    uid: 65535                # Echo 时使用的固定 UID
    callsign_prefix: "RE>"    # 呼号前缀

logging:
  level: "INFO"               # 日志级别
  console: true               # 控制台输出
  file: "logs/fmo_repeater.log"  # 日志文件路径
```

## 📖 使用文档

### 命令行工具

```bash
# 启动服务
python main.py start

# 使用自定义配置文件
python main.py start --config /path/to/config.yaml

# 后台模式
python main.py start --daemon

# 停止服务
python main.py stop

# 重启服务
python main.py restart

# 查看服务状态
python main.py status

# 生成配置文件模板
python main.py --generate-config config.yaml
```

### Echo 服务工作流程

1. **接收消息**：订阅指定 MQTT 主题，接收 FMO 消息
2. **缓存管理**：将消息存入缓存，启动超时计时器
3. **超时处理**：在配置的超时时间内没有新消息时，触发 Echo 功能
4. **头部重写**：修改消息头部（UID 设为 65535，添加呼号前缀）
5. **重新发送**：将修改后的消息发布到相同主题
6. **循环防护**：通过 UID 检查避免无限循环重放

## 🧪 测试

### 运行所有测试

```bash
python tests/run_all_tests.py
```

### 运行特定测试

```bash
# 头部处理测试
python tests/test_header.py

# 配置管理测试
python tests/test_config.py

# UID 过滤测试
python tests/test_uid_filter.py

# 消息流程测试
python tests/test_message_flow.py

# 集成测试（需要 MQTT 服务器）
python tests/test_integration.py
```

测试覆盖范围：
- ✅ 头部解析、序列化、修改（19 个测试）
- ✅ 配置加载、合并、验证（20 个测试）
- ✅ UID 过滤机制（防止重放循环）
- ✅ 消息缓存、超时检测、重放（20 个测试）
- ✅ 线程安全测试

## 🏗️ 项目架构

```
fmo_repeater/
├── fmo_header.py             # FMO 数据包头部的解析和修改
├── config.py                  # 配置文件管理和验证
├── fmo_repeater_service.py   # Repeater 服务主模块（包含 Echo 功能）
├── daemon.py                  # Unix 守护进程支持
├── main.py                    # 主入口和命令行接口
├── config.yaml               # 配置文件（不提交到版本控制）
├── config.yaml.example       # 配置文件示例
├── requirements.txt          # Python 依赖列表
├── tests/                     # 测试套件
│   ├── README.md             # 测试文档
│   ├── run_all_tests.py      # 测试运行器
│   ├── test_header.py        # 头部处理测试
│   ├── test_config.py        # 配置管理测试
│   ├── test_uid_filter.py    # UID 过滤测试
│   ├── test_message_flow.py  # 消息流程测试
│   └── test_integration.py   # 集成测试
├── logs/                      # 日志目录（运行时创建）
├── CLAUDE.md                 # Claude Code 工作指导
└── README.md                 # 本文档
```

## 🔧 开发指南

### 开发环境设置

1. **创建虚拟环境**
   ```bash
   python -m venv venv
   source venv/bin/activate  # Linux/Mac
   # 或 venv\Scripts\activate  # Windows
   ```

2. **安装开发依赖**
   ```bash
   pip install -r requirements.txt
   pip install pytest pytest-cov  # 开发工具
   ```

3. **运行测试**
   ```bash
   python -m pytest tests/ -v
   ```

### 代码规范

- 使用 Black 进行代码格式化
- 使用 Flake8 进行代码检查
- 所有函数和类都有详细的中文文档字符串

### 添加新功能

1. 在 `fmo_repeater_service.py` 中添加新功能
2. 在 `config.py` 中添加相应的配置项
3. 编写单元测试
4. 更新文档

## 📊 监控和日志

### 日志查看

```bash
# 实时查看日志
tail -f logs/fmo_repeater.log

# 查看最近的错误
grep ERROR logs/fmo_repeater.log
```

### 日志级别

- **DEBUG**：详细的调试信息
- **INFO**：一般信息（默认）
- **WARNING**：警告信息
- **ERROR**：错误信息
- **CRITICAL**：严重错误

## 🔒 安全考虑

- MQTT 连接支持 TLS 加密
- 敏感信息通过环境变量传递
- UID 过滤机制防止重放攻击
- 线程安全的消息处理

## 🌐 部署

### Docker 部署

```dockerfile
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install -r requirements.txt

COPY . .
EXPOSE 1883

CMD ["python", "main.py", "start"]
```

### systemd 服务

创建 `/etc/systemd/system/fmo-repeater.service`：

```ini
[Unit]
Description=FMO Repeater Service
After=network.target

[Service]
Type=simple
User=fmo
WorkingDirectory=/opt/fmo-repeater
ExecStart=/opt/fmo-repeater/venv/bin/python main.py start
Restart=always
RestartSec=5

[Install]
WantedBy=multi-user.target
```

## 🤝 贡献指南

1. Fork 项目
2. 创建功能分支 (`git checkout -b feature/AmazingFeature`)
3. 提交更改 (`git commit -m 'Add some AmazingFeature'`)
4. 推送到分支 (`git push origin feature/AmazingFeature`)
5. 打开 Pull Request

## 📄 许可证

本项目采用 MIT 许可证 - 查看 [LICENSE](LICENSE) 文件了解详情。

## 🙏 致谢

- [FMO作者 BG5ESN](https://bg5esn.com/)
- [Paho MQTT Python](https://www.eclipse.org/paho/clients/python/) - MQTT 客户端库
- [PyYAML](https://pyyaml.org/) - YAML 解析库

## 📞 联系方式

- 项目主页：https://github.com/your-username/fmo-repeater
- 问题反馈：https://github.com/your-username/fmo-repeater/issues

---

**FMO Repeater** - 让 FM Over Internet 中继更智能、更可靠！