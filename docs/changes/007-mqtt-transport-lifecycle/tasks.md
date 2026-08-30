# 任务清单：MQTT 传输、发布确认与可证明停机

> 状态标记：`[ ]` 待办 ｜ `[x]` 完成

## T1 传输适配器

- [x] T1.1 实现 MqttTransport 连接、订阅、发布和断连
- [x] T1.2 实现可取消的发布完成等待与 PublishOutcome
- [x] T1.3 实现 QoS 和发布超时配置

## T2 生命周期

- [x] T2.1 Echo 改用 MqttTransport 并按确认结果计数
- [x] T2.2 event bus/producer 提供可验证的线程退出
- [x] T2.3 Repeater 按 quiesce→取消→join→disconnect 停机
- [x] T2.4 信号注册迁移到 main.py

## T3 测试与合并

- [x] T3.1 增加 transport、Echo、配置和生命周期测试
- [x] T3.2 运行全部非集成测试；真实 MQTT 留待最终总验收
- [x] T3.3 合并设计到 docs/design 并标记 proposal merged

## 验收

- [x] publish rc 成功但未完成时不计为成功
- [x] 停机取消发布等待，MQTT 断开前所有业务线程已结束
- [x] RepeaterService 可在非主线程构造
- [x] 保持当前明文 MQTT 行为，不新增 TLS 配置分支
