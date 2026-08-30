# 详细设计：MQTT 传输、发布确认与可证明停机

> status: merged ｜ 变更编号：007

## 1. MqttTransport

新增服务层 `MqttTransport`，唯一持有 Paho Client，负责：

- client id、凭据、连接、订阅和网络循环；
- 在 MQTT 回调入口捕获 `time.monotonic()` 并把 `(payload, received_at)` 交给生产者；
- 原子检查“仍接受发布”并调用 `client.publish()`；
- 等待 `MQTTMessageInfo.wait_for_publish()`，返回结构化 `PublishOutcome`；
- quiesce 后拒绝新的入站回调和出站提交；
- 仅在业务线程全部退出后 disconnect/loop_stop。

Echo 在 `ChannelLease.publish()` 临界区内只执行非阻塞的 publish 提交；等待完成发生在
租约锁外，避免 PUBACK 延迟阻塞网络路由仲裁。

## 2. 发布语义

配置新增：

```yaml
mqtt:
  qos: 0
  publish_timeout: 5.0
```

默认 QoS 0 保持兼容；此时 `wait_for_publish` 表示消息已交给网络层。QoS 1/2 时完成
分别表示 PUBACK/PUBCOMP。`qos` 允许 0/1/2，超时必须为正数。

等待以最长 100ms 小片段轮询，并同时观察 Echo stop Event，因此停机不必等待完整
发布超时。结果 reason 为 `published`、`publish_rc`、`timeout`、`cancelled` 或
`publish_error`。实时 Echo 不重试，避免过时语音和 QoS 重复叠加。

## 3. 传输安全兼容性决策

当前 FMO 网络没有 MQTT TLS 升级计划。本变更不增加 TLS 配置、证书加载或连接
分支，继续使用现有明文 MQTT。该选择作为部署约束记录；若网络安全方案变化，必须
另建增量设计定义证书分发、服务端口、迁移期和回退策略。

## 4. 生命周期

正常启动顺序：transport connect/loop → event bus → producer。停机顺序：

1. `transport.quiesce()`：拒绝新的回调入队和 Echo publish；
2. `echo.stop()`：撤销租约并取消节奏/确认等待；
3. event bus 停止接收、清空待播事件、等待所有消费者线程退出；
4. producer 丢弃输入和未完成 PTT、等待线程退出；
5. transport disconnect，再停止网络循环。

事件消费者和生产者改为非 daemon 线程。`stop(timeout=None)` 默认等待退出；测试可
传入有限 timeout 并检查布尔结果。生产路径中所有等待均可取消或有界，因此组合根
使用无超时 join 不会重现原 30 秒回放阻塞。

## 5. 信号边界

`RepeaterService` 不调用 `signal.signal`。它只提供 `request_shutdown(reason)` 设置
状态。`main.run_service()` 在主线程注册 SIGINT/SIGTERM，并把信号转换为关闭请求。
因此组件测试、嵌入式使用和非主线程构造不再受 Python 信号限制。

## 6. 测试

- MqttTransport：凭据、QoS、发布成功/rc/超时/取消、quiesce、断连顺序；
- Echo：确认成功才计成功，失败/超时计失败，停止可打断确认；
- Repeater：停机严格顺序，消费者退出后才 disconnect，非主线程可构造；
- Config：QoS 和发布超时校验；
- 真实 MQTT：保留端到端和 SIGTERM 用例，并以新配置运行。
