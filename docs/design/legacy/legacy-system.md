# 存量系统设计归档（Legacy）

> status: retired（2025 迭代一 `docs/changes/001-protocol-refactor` 后退役）
>
> 本目录归档重构前的存量系统设计，基于原代码整理，仅供追溯历史，不再维护。

## 1. 系统概述

FMO Repeater（重构前）是一个基于 MQTT 的 Echo（回音）服务：

- 订阅 `FMO/RAW` 主题接收 FMO 原始语音消息
- 缓存连续消息，超时（默认 5s，可配）无新消息后触发重放
- 重放时重写头部：UID 改为 65535，呼号加 `RE>` 前缀，按原始接收时间轴回放
- 通过 UID=65535 过滤自己重放的消息，防止无限循环

## 2. 旧协议假设（22 字节头部）

旧实现假设 FMO 数据包为 22 字节固定头部 + 载荷：

```
struct: <IHHH12s>（小端）
偏移  大小  字段
0     4    VERSION   (uint32)
4     2    PADDING1  (uint16)
6     2    UID       (uint16)
8     2    PADDING2  (uint16)
10    12   CALLSIGN  (UTF-8, 空字节填充)
```

## 3. 模块设计（原样保留记录）

### 3.1 fmo_header.py
- `FMORawHeader`：22B 头部解析/序列化（`from_bytes`/`to_bytes`）
- `replace_header_in_stream(stream, **updates)`：解析头部→修改字段→重序列化，载荷不变

### 3.2 config.py
- `DEFAULT_CONFIG`：mqtt/topics/echo/logging/daemon 五节
- `load_config()`：YAML 加载 + `deep_merge` 合并默认值
- `validate_config()`：必需节、端口范围、echo timeout/uid（0-65535）/prefix 校验
- `save_default_config()`：生成配置模板

### 3.3 fmo_repeater_service.py
- `FMORepeaterService`：
  - `_on_message()`：解析头部→UID=65535 过滤→入缓存（threading.Lock 保护）
  - `_check_timeout()`：主循环 0.1s 周期检查，超时后取出整段缓存
  - `_replay_messages()`：头部重写（UID=65535、`RE>` 前缀）→按接收时间轴（绝对截止时间防漂移）逐条发布
  - MQTT：paho v2 回调 API，自动重连，loop_start 网络线程
  - 信号：SIGINT/SIGTERM 优雅关闭

### 3.4 daemon.py
- Unix 双 fork 守护进程、PID 文件、start/stop/restart/status

### 3.5 main.py
- CLI：start/stop/restart/status、--config、--daemon、--generate-config

## 4. 旧测试体系

- 自运行脚本风格（`print` + 手工计数 pass/fail），`tests/run_all_tests.py` 顺序调度
- 5 个模块：test_header（19）/test_config（20）/test_uid_filter/test_message_flow（20）/test_integration（可选，需真实 MQTT）

## 5. 退役原因

FMO 官方公布语音数据开放协议 v1（64B 消息头 + 传输帧 + 编码语音帧 + PTT 仲裁），旧 22B 头假设与真实线格式不符，UID=65535 防循环机制失效（新协议 UID 为 4 字节真实用户 ID）。详见 `docs/changes/001-protocol-refactor/proposal.md`。
