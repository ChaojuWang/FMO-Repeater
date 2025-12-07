# CLAUDE.md

本文件为 Claude Code (claude.ai/code) 在此代码库中工作时提供指导。

## 项目概述

**FMO Repeater** - 基于 MQTT 的 FM Over Internet (FMO) 系统管理和工具服务。

FMO（FM Over Internet） FM 信号的设备，用于业余无线电通过网络基础设施进行通信。本项目为 FMO 中继器提供完整的管理和工具服务，包括但不限于：
- **回音海螺（Echo）服务**：接收 FMO 消息，修改头部后重新发送，实现回声测试功能
- **消息管理和转发**：智能消息路由和转发功能
- **中继器状态监控**：实时监控和管理 FMO 中继器网络
- **配置和日志管理**：统一的服务配置管理和日志记录系统

**项目状态**：
- 完整的生产级 Repeater 服务，支持配置文件、日志、守护进程
- 模块化设计，易于扩展新的管理功能
- 完整的测试套件，确保服务稳定可靠

## 常用命令

### 安装依赖
```bash
pip install -r requirements.txt
```

### 运行正式服务

#### 前台模式（推荐用于测试）
```bash
python main.py start
```

#### 使用自定义配置文件
```bash
python main.py start --config /path/to/config.yaml
```

#### 后台守护进程模式
```bash
# 启动守护进程
python main.py start --daemon

# 停止守护进程
python main.py stop

# 重启守护进程
python main.py restart

# 查看守护进程状态
python main.py status
```

#### 生成默认配置文件
```bash
python main.py --generate-config config.yaml
```

### 运行概念验证演示
```bash
python demo.py
```

### 运行测试套件

#### 运行所有测试
```bash
python tests/run_all_tests.py
```

#### 运行单个测试
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

测试套件说明：
- 包含 5 个测试模块，覆盖核心功能
- 必需测试（头部、配置、UID 过滤、消息流程）不需要外部依赖
- 集成测试是可选的，需要实际的 MQTT 服务器
- 详细文档请参考 `tests/README.md`

## 代码架构

### 正式服务架构

项目采用模块化设计，分为以下核心模块：

#### 1. fmo_header.py - 头部处理模块
包含 FMO 数据包头部的解析和修改功能：
- `FMORawHeader` 类：表示 22 字节的头部结构
  - VERSION (4字节), PADDING1 (2字节), UID (2字节), PADDING2 (2字节), CALLSIGN (12字节)
  - `from_bytes()`: 反序列化字节流为头部对象
  - `to_bytes()`: 序列化头部对象为字节流
- `replace_header_in_stream()`: 修改数据流中的头部，保留载荷不变

#### 2. config.py - 配置管理模块
- `load_config()`: 从 YAML 文件加载配置，与默认配置合并
- `validate_config()`: 验证配置的完整性和合理性
- `save_default_config()`: 生成默认配置文件模板

#### 3. fmo_repeater_service.py - 核心服务模块
`FMORepeaterService` 类实现完整的中继器管理服务：
- **MQTT 连接管理**：
  - `connect()`: 连接 MQTT 代理并订阅主题
  - `_on_connect()`: 连接成功回调
  - `_on_message()`: 消息接收回调
- **消息处理和缓存**：
  - `message_buffer`: 线程安全的消息缓存列表
  - `_check_timeout()`: 周期性检查是否超时
  - 超时时间可配置（默认 2.0 秒）
- **Echo 服务功能**：
  - `_replay_messages()`: 修改头部并重新发布所有缓存消息
  - 自动添加呼号前缀（如 "RE>BD8BOJ"）
  - 支持可配置的回声延迟和重放策略
- **日志系统**：
  - 支持控制台和文件输出
  - 日志轮转（默认 10MB，保留 5 个备份）
  - 可配置日志级别和格式

#### 4. daemon.py - 守护进程模块
`Daemon` 类实现 Unix 守护进程功能：
- 双重 fork 守护进程化
- PID 文件管理
- 信号处理（SIGTERM, SIGINT）
- 支持 start/stop/restart/status 操作

#### 5. main.py - 主入口脚本
命令行接口，支持：
- 前台/后台运行模式
- 自定义配置文件
- 守护进程管理命令
- 生成配置文件模板

#### 6. config.yaml - 配置文件
使用 YAML 格式，包含：
- MQTT 连接配置（broker, port, 认证信息）
- 主题配置（订阅和发布主题）
- Repeater 服务配置
  - echo 子配置：Echo 功能的超时时间、UID、呼号前缀等
  - 其他 Repeater 功能配置（扩展预留）
- 日志配置（级别，输出方式，轮转策略）
- 守护进程配置（PID 文件路径）

### 服务工作流程

1. **启动阶段**：
   - 加载并验证配置文件
   - 初始化日志系统
   - 连接 MQTT 代理并订阅 `FMO/RAW` 主题

2. **运行阶段**：
   - 接收消息 → **检查 UID（关键！）** → 添加到缓存 → 重置超时计时器
   - **UID 过滤**：如果 UID = 65535（echo UID），则忽略消息（避免重放循环）
   - 主循环周期性检查超时（默认 0.1 秒间隔）
   - 检测到超时 → 触发重放逻辑

3. **重放阶段**：
   - 遍历缓存中的所有消息
   - 解析原始头部，获取呼号
   - 修改 UID 为 65535，呼号添加 "RE>" 前缀
   - 发布到同一主题（形成回声效果）
   - 清空缓存，重置计时器

4. **停止阶段**：
   - 接收 SIGTERM/SIGINT 信号
   - 优雅关闭 MQTT 连接
   - 记录最终状态
   - 清理 PID 文件

**重要：防止重放循环**
服务通过检查 UID 来避免无限循环：
- 原始消息（UID ≠ 65535）→ 处理并缓存 ✅
- 重放消息（UID = 65535）→ 忽略，不缓存 ❌
- 这样确保只处理原始消息，不会重复处理自己重放的消息

### demo.py 演示脚本

保留的概念验证脚本，演示基本功能：
- 简单的 MQTT 连接和发布
- 静态消息列表的头部修改
- 适合快速测试和理解核心概念

### 消息结构

每个 FMO 消息由以下部分组成：
- **头部**（22 字节）：包含元数据的 FMORawHeader
- **载荷**（可变长度）：实际的 FM 无线电数据，在头部转换期间保持不变
- 头部格式使用小端字节序（struct 格式: `<IHHH12s`）

## 实现细节

### 二进制格式
- 使用 Python `struct` 模块，格式字符串为 `<IHHH12s`（小端序）
- 头部结构：uint32 (版本) + uint16 (填充1) + uint16 (UID) + uint16 (填充2) + 12字节字符串 (呼号)

### 呼号编码
- UTF-8 编码，截断至最多 12 字节
- 不足时右侧填充空字节
- 解码错误时使用替换字符

### 线程安全
- MQTT 回调在独立线程中运行
- 使用 `threading.Lock` 保护消息缓存
- 确保多线程访问的数据一致性

### MQTT 兼容性
- 使用 `paho.mqtt.enums.CallbackAPIVersion.VERSION2`
- 支持自动重连和心跳保持

### 日志系统
- 基于 Python `logging` 模块
- 使用 `RotatingFileHandler` 实现日志轮转
- 支持多个日志级别和输出目标

## 配置说明

### 关键配置项

**MQTT 连接** (`mqtt` 节):
- `broker`: MQTT 代理地址
- `port`: 端口号（默认 1883）
- `username` / `password`: 认证凭据
- `client_id_prefix`: 客户端 ID 前缀

**Echo 行为** (`echo` 节):
- `timeout`: 超时时间（秒），默认 5.0
- `uid`: 重放时使用的固定 UID，默认 65535
- `callsign_prefix`: 呼号前缀，默认 "RE>"

**日志设置** (`logging` 节):
- `level`: 日志级别（DEBUG/INFO/WARNING/ERROR）
- `console`: 是否输出到控制台
- `file`: 日志文件路径
- `max_bytes`: 单个日志文件最大大小
- `backup_count`: 保留的备份文件数量

## 文件结构

```
fmo_repeater/
├── fmo_header.py             # 头部处理模块
├── config.py                  # 配置管理模块
├── fmo_repeater_service.py   # Repeater 服务主模块（包含 Echo 功能）
├── daemon.py                  # 守护进程模块
├── main.py                    # 主入口脚本
├── config.yaml               # 配置文件
├── config.yaml.example       # 配置文件示例
├── requirements.txt          # 依赖清单
├── tests/                     # 测试套件
│   ├── README.md             # 测试说明文档
│   ├── run_all_tests.py      # 测试运行器
│   ├── test_header.py        # 头部处理测试
│   ├── test_config.py        # 配置管理测试
│   ├── test_uid_filter.py    # UID 过滤测试
│   ├── test_message_flow.py  # 消息流程测试
│   └── test_integration.py   # 集成测试
├── logs/                      # 日志目录（运行时创建）
├── CLAUDE.md                 # 项目文档
└── README.md                 # 项目说明文档
```

## 开发和调试

### 运行测试

建议在开发过程中定期运行测试以确保代码正确性：

```bash
# 运行所有测试（推荐）
python tests/run_all_tests.py

# 运行特定测试
python tests/test_header.py      # 测试头部处理
python tests/test_config.py      # 测试配置管理
python tests/test_message_flow.py  # 测试消息流程
```

测试覆盖范围：
- ✅ 头部解析、序列化、修改（19 个测试）
- ✅ 配置加载、合并、验证（20 个测试）
- ✅ UID 过滤机制（防止重放循环）
- ✅ 消息缓存、超时检测、重放（20 个测试）
- ✅ 线程安全测试
- ⚠️ 集成测试（需要 MQTT 服务器，可选）

### 测试头部处理
```python
from fmo_header import FMORawHeader, replace_header_in_stream

# 创建测试数据包
test_packet = b'...'  # 22字节头部 + 载荷

# 解析头部
header = FMORawHeader.from_bytes(test_packet)
print(header)

# 修改头部
modified = replace_header_in_stream(test_packet, uid=65535, callsign="TEST")
```

### 生成配置文件
```bash
python config.py --generate config_test.yaml
```

### 查看日志
```bash
# 实时查看日志
tail -f logs/fmo_repeater.log

# 查看最近的错误
grep ERROR logs/fmo_repeater.log
```

## 重要说明

- **安全性**：当前使用明文密码存储在配置文件中，生产环境应使用环境变量或密钥管理系统
- **网络**：使用未加密的 MQTT（端口 1883），如需加密请配置 TLS
- **兼容性**：守护进程功能仅支持 Unix/Linux 系统，Windows 用户请使用前台模式
- **资源**：日志文件会自动轮转，但仍需定期监控磁盘空间

## 语言约定

本项目使用中文作为主要交流和代码注释语言。所有模块都包含详细的中文文档字符串和注释。
