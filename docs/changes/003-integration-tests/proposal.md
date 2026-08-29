# 提案：真实 MQTT 集成测试（Integration Tests）

> status: designed（设计完成，随本变更一并实现）
> 变更编号：003

## 1. 动机

变更 001 的 pytest 套件全部基于 mock MQTT，从未验证过与真实 broker 的连通与
端到端行为。2026-08-30 的手动启动验证中实际用过一批一次性探针脚本（发布合成
FMO 包 → 验证 Echo 重放），证明了该流程的有效性，也暴露了无凭据连接静默失败的
坑。应将这些用例沉淀为可重复执行的正式集成测试。

## 2. 目标

1. 将手动验证过的端到端流程固化为 `tests/test_integration_mqtt.py`
2. 默认**不参与常规测试**（无 broker 时自动 skip），`run_tests.sh` 加
   `--integration` 开关显式启用
3. 复用现有 fixtures（`make_packet` 工厂），探针自动读取 `config.yaml` 凭据
4. 覆盖用例：连接/订阅、回环发布、Echo 端到端重放（vendor/呼号/UID/帧区 CRC/
   防循环）、服务优雅停止

## 3. 范围

### 做
- `tests/test_integration_mqtt.py`（pytest marker `integration`）
- `run_tests.sh` 增加 `--integration` 选项
- `pytest.ini`/`conftest` marker 注册与凭据 fixture
- `docs/design/testing.md` 增补集成测试章节

### 不做
- 需要真实 FMO 设备/语音流的测试（无设备）
- broker 本身的部署与配置（假定外部提供，凭据来自 config.yaml）
- CI 中的自动化集成测试（后续 change）

## 4. 设计要点

- 凭据来源：优先环境变量 `FMO_TEST_BROKER`（`host:port:user:pass` 格式），
  否则读仓库根 `config.yaml` 的 `mqtt` 节；两者皆无 → skip
- 服务进程：`subprocess.Popen(['python3', 'main.py', 'start', '--config', cfg])`
  启动真实服务，测试后 SIGTERM 优雅停止并校验退出
- 探针客户端：独立 paho client（带凭据），等待 SUBACK 后再发布，
  `wait_for_publish` 确保真正发出（吸取静默失败教训）
- 超时：每用例显式上限（连接 15s / 重放等待 10s），防挂死
