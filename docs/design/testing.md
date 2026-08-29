# 测试体系设计

> Merged from changes/001

## 1. 框架与入口

- 框架：pytest（替代旧自运行脚本体系，见 docs/design/legacy）
- 一键入口：`./run_tests.sh`（检查依赖 → `python3 -m pytest tests/ -v`，参数透传）
- 依赖：`requirements-dev.txt`（pytest）；OPUS 可选（缺失自动 skip 不算失败）

## 2. 测试文件

| 文件 | 覆盖 |
|---|---|
| tests/conftest.py | sys.path 注入、sine_pcm16、make_packet 工厂、service_config |
| tests/test_protocol_header.py | 64B 头 roundtrip、字段偏移（规范 §2.2）、callsign 截断/多字节、错误路径、copy_with |
| tests/test_protocol_packet.py | 帧结构 roundtrip、parser 校验链（length/CRC/index/残余）、聚合上限（OPUS≤6/RADPCM≤3、MTU）、多包 roundtrip |
| tests/test_protocol_vendor.py | 四区间判定、保留区拒绝、越界 |
| tests/test_codec_radpcm.py | 步长/索引表、帧结构（恢复点/回绕/旧格式兼容）、roundtrip SNR（连续≥20dB/单帧≥15dB）、状态延续、丢包恢复、直流抑制可逆 |
| tests/test_codec_opus.py | 可用性探测与降级、roundtrip、VBR 变长、PLC 空帧、reset |
| tests/test_echo_service.py | 防循环双条件矩阵、头重写（帧区/CRC 不变）、非法包容错、超时重放（mock MQTT）、事件日志集成 |
| tests/test_config.py | deep_merge、默认值、加载合并、校验矩阵（vendor 保留区等）、示例配置有效性 |
| tests/test_event_log.py | JSONL 写入/解析、no-op、目录创建、轮转、Unicode |
| tests/test_integration_mqtt.py | **集成**（marker=integration，默认排除）：broker 连接回环、Echo 端到端重放、SIGTERM 优雅停止 |

## 3. 集成测试（changes/003）

- **默认排除**：`pytest.ini` 的 `addopts = -m "not integration"`；常规 `./run_tests.sh`
  不触碰网络
- **显式启用**：`./run_tests.sh --integration`（等价
  `pytest -m "not integration or integration"`）
- **凭据来源**（优先级）：环境变量 `FMO_TEST_BROKER=host:port:user:pass`
  > 仓库根 `config.yaml` 的 `mqtt` 节；皆无 → 用例自动 skip（不 fail）
- **真实服务进程**：集成用例以 `subprocess.Popen(main.py start --config tmp)`
  启动完整服务（独立临时配置与日志），探针客户端带凭据连接、等待 SUBACK 与
  `wait_for_publish` 后才发布（避免静默失败）
- 事件断言：读取子服务 `events.jsonl`，校验完整事件链
  （service_started → stream_start → packet_received → replay_* → stream_end →
  loop_detected / service_stopped）

## 4. 关键做法

- **mock MQTT**：`MockMQTTClient.publish` 捕获发布，不起网络
- **make_packet 工厂**：合成完整合法消息包（控制 uid/callsign/vendor/codec/帧数/
  stream_begin_utc/srv_uid/smeter）
- **SNR 度量**：去直流后计算（ADPCM 直流抑制特性），阈值依据决策 D7
- **OPUS skipif**：`pytest.mark.skipif(not opus_is_available())`

## 5. 验收

`./run_tests.sh` 退出码 0；OPUS 用例缺依赖时 skip 而非 fail；
`./run_tests.sh --integration` 在 broker 可达且凭据有效时集成用例全绿，
无凭据时 skip 而非 fail。
