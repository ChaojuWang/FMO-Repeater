# 任务清单：回放缓存截断（变更 004）

> 状态标记：`[ ]` 待办 ｜ `[x]` 完成

## T1 配置
- [x] T1.1 `config.py` 新增 `echo.max_duration`（默认 30.0）+ validate 校验（>0）
- [x] T1.2 `config.yaml.example` / `config.yaml` 增补 max_duration 项

## T2 截断实现
- [x] T2.1 `echo.py` `_replay_messages`：按首包→末包 monotonic 跨度，超 max_duration 只重放前 30s 包，尾部丢弃
- [x] T2.2 事件 `replay_finished`/`stream_end` 增 `truncated`、`dropped` 字段
- [x] T2.3 `__init__` 读取 `max_duration`（缺省兜底 30.0）

## T3 测试
- [x] T3.1 截断行为（超阈值只重放前段）、不截断（未超阈值）、默认值 30
- [x] T3.2 `test_config.py` 默认值 + 非法值（≤0）拒绝

## 验收
- [x] 单元 125 passed、集成 3 passed
- [x] 测试运行 < 3s（避免逐包 sleep 拖慢）：采用小 max_duration + 小跨度模拟
