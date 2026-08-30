# 任务清单：单信道 PTT 事件化与统一仲裁

> 状态标记：`[ ]` 待办 ｜ `[x]` 完成

## T1 协议仲裁

- [x] T1.1 实现 ChannelCoordinator、RouteDecision、ChannelLease
- [x] T1.2 实现 1500ms/2000ms/UID 平局与 uint32 回绕规则
- [x] T1.3 实现 0/30/60/90/120 秒连续上行限制

## T2 事件生产

- [x] T2.1 实现 TimedPacket、TransmissionCompleted、TransmissionProducer
- [x] T2.2 实现独立消费者队列的 TransmissionEventBus
- [x] T2.3 迁移包解析、防回环和 PTT 结构化日志

## T3 Echo 与组合根

- [x] T3.1 保留 EchoService 名称，改为事件消费者和本地流生产者
- [x] T3.2 固定单次回放 streamBeginUTC、可中断时间轴和租约抢占
- [x] T3.3 新增 RepeaterService，迁移 MQTT 与进程生命周期

## T4 配置、测试与文档

- [x] T4.1 新增 transmission 配置并兼容迁移 echo.timeout
- [x] T4.2 新增仲裁、生产者、总线、Echo 与生命周期测试
- [x] T4.3 保持真实 MQTT Echo 端到端测试
- [x] T4.4 更新 change 002 和 docs/design，标记本变更 merged

## 验收

- [x] 常规 pytest：141 passed；协议/编解码行为无回归
- [x] Echo 回放与网络输入使用同一仲裁器
- [x] 双发送者和 Echo 被抢占场景符合协议 §8.4
- [x] 停机后无新的 MQTT 发布
- [x] 真实 MQTT 集成：144 passed（含 3 项集成）
- [x] Review follow-up：跨来源 UID、积压队列时间、路由释放顺序、停机竞态、
  Echo 原子发布五项回归均已覆盖
