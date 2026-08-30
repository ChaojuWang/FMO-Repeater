# 任务清单：协议输入与 Echo 标识加固

> 状态标记：`[ ]` 待办 ｜ `[x]` 完成

## T1 编解码边界

- [x] T1.1 RADPCM parse/build 校验 step_index
- [x] T1.2 保持单帧超 MTU 的现有兼容行为

## T2 配置与依赖

- [x] T2.1 Echo UID 改为非零约束
- [x] T2.2 callsign_prefix 增加非空和 UTF-8 12B 上限
- [x] T2.3 Paho 依赖范围改为 >=2.0,<3
- [x] T2.4 更新配置模板与 README

## T3 测试与合并

- [x] T3.1 增加协议、编解码和配置回归测试
- [x] T3.2 运行全部非集成测试
- [x] T3.3 合并设计到 docs/design 并标记 proposal merged

## 验收

- [x] 不可信 RADPCM 帧不会泄漏 IndexError
- [x] 所有合法 Echo 配置的回放标识均可在线格式中可靠识别
- [x] Paho 最低支持版本与代码 API 一致
- [x] 超过 1400B 的历史兼容行为不变
