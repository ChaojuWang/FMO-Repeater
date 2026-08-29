# 修复：回放触发改为纯超时 2s + 回放时更新 stream_begin_utc

> status: implemented
> 背景：用户澄清设备行为与回放语义，此前多轮修复均偏离真实需求

## 1. 用户确认的事实（重要，纠正此前全部推断）

1. 设备**按键（PTT）期间连续发送语音包不间断**（不会因换气/停顿暂停）
2. 网络环境好：到服务器延迟 ~15ms、无丢包
3. 回放触发 = **停止收到包满 2 秒**（超时）即回放整段缓冲
4. **回放的包 stream_begin_utc 不应保留原值**，应更新为回放时刻

## 2. 根因（修正）

此前反复切流非网络抖动、非多实例、非 sbu 滚动——核心 bug 是两点：

1. `_rewrite_packet` 重写头时**漏改 `stream_begin_utc`**：回放包携带原始
   发送者的 sbu。回声被自身订阅收到后（loop_detected）尚可跳过，但语义
   上等价于"冒充原发送者的一段旧流"，违背协议（回放是新流，应有新起始）。
2. 此前把流边界复杂化（sbu 变化切流、pending 续播等），实际需求只是
   **简单的超时回放**——与重构前最初实现一致，仅 timeout 用 2s。

## 3. 修复方案

- **回放触发**：纯超时——`_check_timeout` 检测「距最后一包 ≥ timeout 秒且
  缓冲非空」→ 回放整段并清空。`timeout` 默认 **2.0s**。
- **回放头重写**：除 vendor/callsign/timestamp/uid 外，**同时更新
  `stream_begin_utc` 为回放时刻**（`int(time.time()*1000) & 0xFFFFFFFF`）。
  帧区与 CRC 不变（CRC 仅覆盖帧区）。
- 撤除 sbu 变化切流、`_current_stream_begin`、`_pending` 等逻辑，`_on_message`
  仅做「解析 → 防循环 → 追加缓冲 → 刷新 last_message_time」。

## 4. 变更清单

- `fmo_repeater/service/echo.py`：`_on_message` 简化、`_check_timeout` 恢复超时
  回放、`_rewrite_packet` 更新 stream_begin_utc、删除 sbu 边界相关状态
- `fmo_repeater/service/config.py`：`timeout` 默认 2.0
- `config.yaml` / `config.yaml.example`：timeout 2.0
- `tests/test_echo_service.py`：重写 `TestStreamBoundary` → 验证「超时回放 +
  回放包 stream_begin_utc 已更新」；`test_rewrite_fields` 增补 sbu 断言

## 5. 修正：重放异步化（2026-08-30 复测）

复测日志出现「5.84s 无新消息才重放 7 包」的异常：timeout=2.0 却拖到 5.8s，
且每段重放后下一包要隔 1~5s 才到。定位根因：

- `_replay_messages` 按原始时间轴 `time.sleep` 实时重放，且**在主循环线程内
  同步执行**。重放期间主循环被 sleep 阻塞，无法及时 `_check_timeout`；同时
  收包线程在重放期间持续收包，二者串扰，累积出数秒空洞与切流。

修复：**重放改到独立线程**——

- `_replay_async(batch)`：对缓冲取快照，`threading.Thread(target=..., daemon=True)`
  启动重放，立即返回
- 单飞保护：仅当无进行中的重放线程时才启动新重放（`_replay_thread` 引用 +
  `_replay_lock`），避免回声重叠
- `stop()` 时等待重放线程结束（`join(timeout)`）优雅收尾

## 6. 验收（最终）

- 收包与超时判定不被重放 sleep 阻塞
- 停止收包 2s 后整段回放；重放期间新流正常落入缓冲
- 回放包 stream_begin_utc 更新为回放时刻
- 单元/集成全绿；实测长发言不再切段

## 7. 修正（最终）：单消费者线程串行化

异步重放虽解决了主循环阻塞，但引入新的竞态：`_check_timeout` 与 MQTT 回调
并发操作 `_buffer`，回放期间新包进入新 buffer 又触发超时，导致流被切成
「3/12/11/21/9 包」多段、echo 包 flood 干扰超时计时。

最终方案：**单一消费者线程串行化**——

- MQTT 回调（`_on_message`）只做「解析 → 防循环 → 丢入 `queue.Queue`」，
  不碰 buffer / last_message_time / 重放
- 专用消费者线程 `_consume_loop()`：从队列取包 → 缓冲 → 用「距最后一包时间」
  判定超时（用带超时的 `queue.get(timeout=...)` 自然实现「无新包计时」）→
  超时即回放整段并清空
- 全程单线程操作 buffer，无锁竞态、无多实例并发

## 8. 变更清单（追加）

- `fmo_repeater/service/echo.py`：改为队列 + 消费者线程模型；`_replay_messages`
  恢复同步调用（在消费者线程内，不阻塞 MQTT 回调）
- `tests/test_echo_service.py`：适配队列模型（`_on_message` 入队、消费者线程
  消费）；仍用 `wait_replay_done` 同步断言

## 9. 补充规则（2026-08-30 用户澄清）

**回放时序**：
1. 停止收包满 `timeout` 秒 → 回放整段
2. **回放进行中到达的包一律丢弃**（不回放、不缓存、不进入下一段）
3. **回放完成那一刻起**，重新开始缓存新包

实现：触发回放时（队列必空）直接 `_replay_messages` 同步回放，回放结束后
清空 `_buffer`、重置 `last_message_time = None`，并 `_drain_queue()` 丢弃回放
期间新到达的包，从此刻重新计时缓存。

## 10. 收尾清理（冗余消除）

- 删除未使用导入 `sys`、`HEADER_SIZE`
- 删除 `_maybe_replay_if_idle` 里回放前的 `_drain_queue()`（触发时队列必空，
  属冗余）；保留回放后的 drain 以丢弃回放期间新到达的包
- 保留回放逐包 `time.sleep`（保持回声原始语速，用户确认）

## 5. 验收

- 回放包 `stream_begin_utc != 原发送者 sbu`（= 回放时刻）
- 停止收包 2s 后整段回放，收包期间（间隔 <2s）不回放
- 单元/集成全绿
