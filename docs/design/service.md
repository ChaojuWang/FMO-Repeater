# 服务层设计

> Merged from changes/001, 003, 004, 005

## 1. 配置

```yaml
mqtt: {broker, port, username, password, client_id_prefix, keepalive}
topics: {subscribe, publish}
transmission:
  idle_timeout: 2.0
  max_uplink_duration: 60  # 0/30/60/90/120
echo:
  max_duration: 30.0
  vendor: 0x2000
  uid: 65535
  callsign_prefix: 'RE>'
event_log: {enabled, file, max_bytes, backup_count}
logging: {level, console, file, max_bytes, backup_count}
daemon: {enabled, pid_file}
```

旧配置的 `echo.timeout` 在加载时迁移为 `transmission.idle_timeout`。vendor 拒绝
保留区；PTT 超时和 Echo 最大时长必须为正数；上行限制只接受规范允许值。

## 2. PTT 完成事件

`TimedPacket` 保存不可变原始包字节和相对接收时间；`TransmissionCompleted` 保存
首包身份、首末单调时间、包元组与结束原因。原因包括：

- `idle_timeout`：最后一包后满配置超时
- `route_replaced`：路由窗口已过，新发送者接管
- `preempted`：更早流或相同起始时间的更小 UID 抢占
- `duration_limit`：连续上行达到限制

在处理每个新包前先结算旧 PTT 的空闲边界，避免生产者线程调度延迟把两次同 UID
按键合并；队列仍有包时不得用当前 wall/monotonic 时间插入额外超时检查，避免
调度停顿把实际连续的包切开。空闲封口先释放对应网络路由，再发布完成事件。
非法包、自身 Echo 回环和仲裁拒绝包均不进入事件。

事件总线为每个消费者建立独立 FIFO 线程；一个消费者慢或异常不会阻塞网络生产者
和其他消费者。事件仅在进程内传递，不持久化、不重试、不发布到 MQTT。

## 3. EchoService

### 3.1 信道申请与忙时规则

Echo 收到完成事件时，2 秒 PTT 封口已经发生，因此立即申请本地路由，不再额外
等待。信道仍忙时整段丢弃，不排队。获得 `ChannelLease` 后，每包发布前刷新；
网络路由抢占会使租约失效，Echo 立即停止剩余回放。

同 UID 续占只允许网络来源续接网络来源；网络包与 Echo 路由 UID 相同也不能并发
续占。每包租约验证、刷新与 MQTT publish 提交由协调器在同一临界区内原子执行，
网络抢占只能发生在两次提交之间。

### 3.2 头重写与时间轴

- `vendor → echo.vendor`
- `uid → echo.uid`（非 0 时）
- `callsign → prefix + 原呼号`
- 一次回放的所有包共享回放开始时生成的 `stream_begin_utc`
- 每包 `timestamp` 使用实际发布时间
- 帧区与 CRC 原样保留

回放使用绝对截止时间 `started_at + packet.offset_s`，并以可中断 Event 等待，避免
累积漂移且支持立即停机。`echo.max_duration` 保留边界内（含边界）的包，尾部只对
Echo 丢弃，不改变领域事件。

### 3.3 防循环

header.vendor 必须先等于 Echo vendor；其后 UID 等于配置的非零 Echo UID，或呼号
以前缀开头，即判定为本机回环。过滤发生在网络仲裁前，因为本地路由已在发布前
登记。Echo 回放使用 `replay_*` 事件记录，不产生新的完成事件，避免递归。

## 4. RepeaterService

组合并启动 ChannelCoordinator、TransmissionEventBus、TransmissionProducer 和
EchoService；处理 Paho MQTT VERSION2 回调及信号。停机是幂等的立即取消：不冲刷
未完成 PTT，不等待待播事件；顺序为禁用 Echo、关闭事件总线、停止生产者、断开
MQTT。已经交给 MQTT 客户端的包不撤回。

## 5. 结构化事件

| 类别 | 事件 |
|---|---|
| 生命周期 | service_started, service_stopped |
| PTT | stream_start, packet_received, stream_end, packet_invalid, loop_detected |
| 路由 | route_acquired, route_rejected, route_preempted, uplink_limited |
| Echo 路由 | echo_route_acquired, echo_route_rejected, echo_preempted |
| 回放 | replay_started, replay_finished |

`stream_end` 包含 `packets/duration_s/reason`，其中 duration 是网络接收跨度。
`replay_finished` 包含成功、失败、截断、丢弃数和完成原因。

## 6. 守护进程

`daemon.py` 保持 Unix 双 fork、PID 文件和 start/stop/restart/status 管理；CLI 入口
构造 `RepeaterService`。
