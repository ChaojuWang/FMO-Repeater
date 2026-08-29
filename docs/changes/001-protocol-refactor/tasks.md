# 任务清单：FMO 协议 v1 重构（变更 001）

> 状态标记：`[ ]` 待办 ｜ `[x]` 完成 ｜ `[~]` 部分完成

## T1 SDD 文档体系
- [x] T1.1 `docs/DESIGN_PROCESS.md` 流程规范
- [x] T1.2 `docs/design/legacy/legacy-system.md` 存量归档
- [x] T1.3 `docs/changes/001-protocol-refactor/` 提案+设计+任务
- [x] T1.4 `CLAUDE.md` 写入"文档先行"规则

## T2 环境准备
- [x] T2.1 依赖检查（paho-mqtt ✅ 2.1.0、PyYAML ✅ 6.0、pytest ✅ 9.0.2、libopus.so.0 ✅、opuslib 待装）
- [x] T2.2 `requirements.txt` 更新（+opuslib、+pytest）

## T3 协议层实现
- [x] T3.1 `fmo_repeater/protocol/vendor.py`（区间常量与校验）
  - 验收：`vendor_zone()` 四区间全覆盖；保留区 `is_vendor_valid()==False`
- [x] T3.2 `fmo_repeater/protocol/header.py`（MessageHeader 64B）
  - 验收：from_bytes/to_bytes roundtrip 无损；<64B、length 不符 → ProtocolError；callsign 12B 截断/填充正确
- [x] T3.3 `fmo_repeater/protocol/frame.py`（EncodedVoiceFrame / TransportFrame）
  - 验收：两帧型 roundtrip；compress_mode 枚举合法值；长度不符 → ProtocolError
- [x] T3.4 `fmo_repeater/protocol/packet.py`（PacketParser / PacketBuilder）
  - 验收：CRC 帧区校验通过/篡改拒绝；聚合规则：OPUS 第 7 帧、RADPCM 第 4 触发封包（1400B 或 250ms）；frame_num、index 从 1 递增

## T4 编解码层实现
- [x] T4.1 `fmo_repeater/codecs/radpcm.py`
  - 验收：正弦/带噪正弦 roundtrip SNR ≥ 20dB（去直流后比较；4-bit IMA ADPCM 理论水平 19-27dB，实测满幅正弦 26.8dB / 多音+噪 22.9dB）；丢帧后下一帧可恢复解码；旧 8-bit adpcmBytes 包可解析；frame_index 0xFFFF 起始合法
- [x] T4.2 `fmo_repeater/codecs/opus_codec.py`
  - 验收：roundtrip 帧长 640B；缺 opuslib 时 `is_available()==False` 且构造抛 `OpusUnavailable`

## T5 服务层实现
- [x] T5.1 `fmo_repeater/service/config.py`（默认配置/加载/验证）
  - 验收：vendor 保留区拒绝；event_log 节校验；deep_merge 兼容旧配置
- [x] T5.2 `fmo_repeater/service/logging_setup.py` + `event_log.py`
  - 验收：JSONL 每行合法 JSON；禁用时 no-op；轮转生效
- [x] T5.3 `fmo_repeater/service/echo.py`（EchoService 重写）
  - 验收：防循环双条件跳过；重写后帧区与 CRC 不变；timestamp 更新；按时间轴重放
- [x] T5.4 `fmo_repeater/service/daemon.py`（迁移）

## T6 入口与清理
- [x] T6.1 `main.py` 包导入改造（CLI 不变）
- [x] T6.2 `config.yaml.example` 更新（vendor/event_log 节）
- [x] T6.3 删除旧模块（fmo_header.py / config.py / fmo_repeater_service.py）与旧测试
- [x] T6.4 `requirements.txt` / `.gitignore`（recordings/、logs/）

## T7 测试套件
- [x] T7.1 `tests/conftest.py`（fixtures）
- [x] T7.2 test_protocol_header / test_protocol_packet
- [x] T7.3 test_codec_radpcm / test_codec_opus
- [x] T7.4 test_echo_service / test_config / test_event_log
- [x] T7.5 `run_tests.sh` + 全部跑绿
  - 验收：`./run_tests.sh` 退出码 0；OPUS 用例缺依赖时 skip 而非 fail

## T8 文档合并与收尾
- [x] T8.1 changes/001 合并入 docs/design/（architecture/protocol/codecs/service/logging/testing），标注 Merged
- [x] T8.2 README.md / CLAUDE.md 更新
- [x] T8.3 proposal.md 标记 status: merged
