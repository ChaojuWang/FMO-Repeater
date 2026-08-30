# 详细设计：回放缓存截断（max_duration）

> status: merged ｜ 变更编号：004 ｜ 依赖：003

## 1. 配置

`config.py` 的 `echo` 节新增：

```yaml
echo:
  timeout: 2.0       # 流结束判定超时（松键后此时间内无新包即回放）
  max_duration: 30.0 # 回放时长上限（秒），缓存跨度超过则只重放前 max_duration 秒
  ...
```

`validate_config` 增加：`max_duration` 必须是 >0 的数值。

## 2. 截断实现（echo.py）

`_replay_messages(batch)` 中，回放前按 `batch` 内 `(packet, monotonic)` 的
接收时间计算跨度：

```python
span = batch[-1][1] - batch[0][1]   # 首包→末包接收时间跨度
if span > self.max_duration:
    cutoff = batch[0][1] + self.max_duration
    kept = [x for x in batch if x[1] <= cutoff]
    dropped = len(batch) - len(kept)
    batch = kept
    # 事件里记录 truncated=True, dropped=dropped
```

- 保留 `monotonic <= 首包 + max_duration` 的包（含边界），丢弃其后
- `dropped == 0` 时截断不生效，走原路径
- 事件 `replay_finished` / `stream_end` 增加 `truncated` 与 `dropped` 字段

## 3. 边界

- `span <= max_duration`：不截断，原样回放
- `span > max_duration`：只重放前 max_duration 内的包；尾部包丢弃后，
  `buffer` 清空、`last_message_time=None`，从此刻重新缓存（与现有回放收尾一致）
- 被丢弃的尾部包不进下一段（符合 003 的「回放期间到达的包丢弃」精神，
  只是此处是「超时截断」）

## 4. 测试（tests/test_echo_service.py）

- `test_replay_truncates_beyond_max_duration`：构造跨度 >30s 的流（手动设
  buffer 内 monotonic 时间），断言只重放前 30s 内的包、dropped == 期望值
- `test_replay_no_truncate_within_duration`：跨度 ≤30s 完整重放
- `test_max_duration_configurable`：config 设 max_duration=1.0，短跨度也截断
- `test_config.py`：max_duration 默认 30、非法值（≤0）拒绝
