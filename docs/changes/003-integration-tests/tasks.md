# 任务清单：真实 MQTT 集成测试（变更 003）

> 状态标记：`[ ]` 待办 ｜ `[x]` 完成
> 设计：proposal.md §4（本变更规模小，设计要点直接写入提案）

## T1 测试基础设施
- [x] T1.1 `pytest.ini` 注册 `integration` marker；默认 addopts 排除 `-m "not integration"`
- [x] T1.2 `tests/conftest.py` 增加 `mqtt_credentials` fixture（env `FMO_TEST_BROKER` > config.yaml > skip）

## T2 集成测试用例
- [x] T2.1 `tests/test_integration_mqtt.py::TestBrokerConnectivity`：连接/认证/订阅回环（发布 qos1 → 自收）
- [x] T2.2 `TestEchoEndToEnd`：subprocess 启动服务 → 发布合成包（make_packet）→ 探针收到重放包 → 校验 vendor 0x2000/RE> 前缀/UID 保持/帧区与 CRC 一致/loop 防循环
- [x] T2.3 `TestGracefulShutdown`：SIGTERM → 进程退出码 0 → events.jsonl 含 service_stopped

## T3 入口与文档
- [x] T3.1 `run_tests.sh --integration` 开关（追加 `-m integration` 并允许 marker）
- [x] T3.2 `docs/design/testing.md` 增补集成测试章节；README 命令说明
- [x] T3.3 常规套件（默认排除）与 `--integration` 两种模式均验证通过

## 验收
- [x] `./run_tests.sh`：118 passed，集成用例被排除（不 fail 不 skip 计数不影响）
- [x] `./run_tests.sh --integration`：默认套件 + 集成用例全绿（需 broker 可达与凭据）
- [x] 无 broker/凭据时 `--integration` 模式下集成用例 skip 而非 fail
