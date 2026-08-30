# 总体架构

> Merged from changes/001, 003, 004, 005

## 1. 分层

```text
┌──────────────────────────────────────────────────┐
│ service（I/O、事件与生命周期）                    │
│ repeater / transmission / echo / config / logs  │
├──────────────────────────────────────────────────┤
│ codecs（PCM16 8kHz mono ↔ 编码载荷）              │
│ radpcm / opus_codec                              │
├──────────────────────────────────────────────────┤
│ protocol（纯数据结构与无 I/O 状态机）             │
│ vendor / header / frame / packet / ptt           │
└──────────────────────────────────────────────────┘
```

依赖方向保持 `service → codecs → protocol`。`ChannelCoordinator` 属于协议层，
因为它完整实现 FMO §8 的确定性路由规则且不执行 I/O；线程安全仅用于允许网络输入
生产者和 Echo 本地生产者共享同一信道状态。

## 2. 运行组件

| 组件 | 职责 |
|---|---|
| `RepeaterService` | MQTT 适配、组件装配、信号与立即停机 |
| `ChannelCoordinator` | 单信道当前路由、抢占、上行限制、Echo 租约 |
| `TransmissionProducer` | 串行解析/过滤，组装一次获准 PTT 并产生完成事件 |
| `TransmissionEventBus` | 进程内广播，每个消费者独立 FIFO 工作线程 |
| `EchoService` | 消费完成事件，申请信道并按原时间轴发布回音 |

`EchoService` 是业务服务名称；`TransmissionConsumer` 只是它在事件架构中的角色。

## 3. 数据流

```text
MQTT on_message
  → ingress queue
  → PacketParser + own-replay filter
  → ChannelCoordinator.accept_network_packet
  → TransmissionProducer
  → TransmissionCompleted
  → TransmissionEventBus
  → EchoService
  → ChannelCoordinator.try_start_echo / ChannelLease.refresh
  → MQTT publish
```

一个订阅主题代表一个半双工信道，只组装当前获胜者。Echo 并不绕过冲突检测：
它是同一协调器管理的本地语音流；网络流按协议拥有更高优先级时会使 Echo 租约
失效。MQTT 返回的本机 Echo 包只做回环过滤，不重复参与本地仲裁。

## 4. 生命周期

MQTT 回调只入队，所有网络包状态由单一生产者线程修改。收到 SIGTERM/Ctrl+C
后停止输入、丢弃未完成 PTT、取消 Echo 等待和消费者队列，再断开 MQTT；停机
不会冲刷或启动新的回放。

队列中的包以入队时保存的 monotonic 接收时间顺序结算边界；仅在队列为空时以
当前时间检查空闲。停机先禁用 Echo 和事件消费，再等待生产者退出，关闭后的事件
总线拒绝新事件。
