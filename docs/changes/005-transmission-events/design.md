# 详细设计：单信道 PTT 事件化与统一仲裁

> status: merged ｜ 变更编号：005

## 1. 数据流与职责

```text
MQTT callback → ingress queue → parse / own-replay filter
  → ChannelCoordinator.accept_network_packet
  → TransmissionProducer
  → TransmissionCompleted → TransmissionEventBus → EchoService
  → ChannelCoordinator.try_start_echo → ChannelLease → MQTT publish
```

- `RepeaterService`：MQTT 连接、线程和组件生命周期的组合根。
- `ChannelCoordinator`：一个主题所代表的半双工信道的唯一占用状态。
- `TransmissionProducer`：只组装仲裁后接受的网络包，并在边界处产生完成事件。
- `EchoService`：消费完成事件；获得本地路由租约后重写并回放。

## 2. 领域接口

```python
@dataclass(frozen=True)
class TimedPacket:
    payload: bytes
    offset_s: float

@dataclass(frozen=True)
class TransmissionCompleted:
    vendor: int
    uid: int
    callsign: str
    stream_begin_utc: int
    first_received_at: float
    last_received_at: float
    packets: tuple[TimedPacket, ...]
    reason: str
```

`reason` 为 `idle_timeout`、`route_replaced`、`preempted` 或
`duration_limit`。停机采用立即取消，不把未完成缓冲转成事件。

`TransmissionEventBus` 对每个订阅者使用独立 FIFO 队列与工作线程；发布不等待
消费者执行，消费者异常只记录日志。`EchoService` 实现内部
`TransmissionConsumer` 角色，但继续以业务服务名称对外。

## 3. 路由仲裁

协议常量：路由窗口 1500ms、较早流抢占最大差值 2000ms。输入统一使用
`time.monotonic()`；`streamBeginUTC` 比较将 uint32 差值解释为有符号数，以处理
回绕。

网络包裁决顺序：

1. 无有效路由：接受并占用。
2. 同 UID：续占并刷新窗口。
3. 当前路由距最后活动 `>=1500ms`：新包接管，旧 PTT 以
   `route_replaced` 封口。
4. 新 `streamBeginUTC` 更早且差值在 1..2000ms：抢占，旧 PTT 以
   `preempted` 封口。
5. 起始时间相同且新 UID 更小：抢占。
6. 其余拒绝，不进入 PTT 缓冲，也不刷新当前路由。

无竞争时最后一包后 `transmission.idle_timeout`（默认 2s）封口。连续上行在
达到 `max_uplink_duration` 时，触发上限的包不接受，已接受部分以
`duration_limit` 封口；超限 UID 在持续发包期间被阻断，静默 1500ms 后解除，
其他 UID 可立即竞争。

## 4. Echo 本地语音流

收到 `TransmissionCompleted` 后 Echo 不再等待 2s，而是立即以配置的 UID、
当前 uint32 UTC ms 申请 `ChannelLease`。若信道忙则整段丢弃，不排队。

- 一次回放的所有包使用同一个 `stream_begin_utc`；每包 `timestamp` 使用实际
  发布时间。
- 每次发布前刷新租约。网络包按相同 §8.4 规则可拒绝或抢占 Echo；租约失效后
  Echo 立即取消剩余等待和发布。
- `echo.max_duration` 只裁剪 Echo 使用的前段；领域事件保留完整 PTT。
- 最后一包后停止刷新，但路由保留至 1500ms 窗口自然过期。
- MQTT 回环在解析后、网络仲裁前按现有 vendor/UID/呼号规则过滤。Echo 已在
  发布前登记本地路由，回环不得重复参与仲裁。
- Echo 回放用 `replay_*` 记录，不再次产生供 Echo 消费的完成事件，避免递归。

## 5. 配置、日志与生命周期

```yaml
transmission:
  idle_timeout: 2.0
  max_uplink_duration: 60  # 0/30/60/90/120
echo:
  max_duration: 30.0
  vendor: 0x2000
  uid: 65535
  callsign_prefix: RE>
```

加载旧配置时，若用户只显式提供 `echo.timeout`，其值迁移到
`transmission.idle_timeout`。模板不再生成 `echo.timeout`。

保留 `stream_start`、`packet_received`、`stream_end` 与 `replay_*`；
`stream_end.duration_s` 改为接收跨度并增加 `reason`。新增路由获取、拒绝、
抢占、上行超限、Echo 路由拒绝和 Echo 被抢占事件。

SIGTERM/Ctrl+C：停止输入，撤销租约，取消可中断等待，清空生产者与消费者队列，
不冲刷未完成 PTT；已经交给 MQTT 客户端的包不撤回。

## 6. 关键决策

| # | 决策 | 理由 |
|---|---|---|
| E1 | 单一协调器管理网络与 Echo | 防止 Echo 绕过信道冲突检测 |
| E2 | 完成事件只表示网络 PTT | 避免 Echo 回放递归触发自身 |
| E3 | Echo 忙时不排队 | 避免迟到回音和信道长期积压 |
| E4 | 固定一次回放的 streamBeginUTC | 回放在协议上是一股连续新流 |
| E5 | 立即取消停机 | 用户明确要求停机不冲刷或续播 |

## 7. 实现检视修正

> 2026-08-30 review follow-up：实现前五项并发边界必须满足以下线性化约束。

1. 同 UID 续占只适用于 `network → network`；网络包不得续接 Echo 来源路由，
   跨来源仍按窗口和优先级仲裁。
2. 队列非空时只用下一包保存的 `received_at` 结算 PTT 边界；只有 `queue.Empty`
   才使用当前 monotonic 时间轮询，线程调度或日志停顿不得切分已排队的连续包。
3. `idle_timeout` 封口必须先清除匹配的网络路由，再向事件总线发布完成事件，
   包括 timeout 小于 1500ms 的合法配置。
4. 停机先置 Echo 为不可消费并停止事件总线，再等待生产者线程；事件总线停止后
   拒绝新发布，生产者 stopping 状态也不得暴露新的完成事件。
5. Echo 每包的租约验证、路由刷新和 `mqtt.publish` 提交必须在协调器同一临界区
   内完成；网络抢占与 Echo 发布以该临界区形成明确的全序。
