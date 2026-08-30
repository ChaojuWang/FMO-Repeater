# 日志系统设计

> Merged from changes/001, 005

## 1. 双轨日志

| 轨道 | 格式 | 用途 | 配置节 |
|---|---|---|---|
| 运行日志 | 可读文本（轮转） | 人读排障 | `logging` |
| 事件日志 | JSONL（每行一个 JSON） | 结构化分析/统计 | `event_log` |

## 2. 运行日志（logging_setup.py）

- Logger 名 `FMORepeater`；格式 `时间 - 名称 - 级别 - 消息`
- 控制台（可关）+ RotatingFileHandler（max_bytes/backup_count）
- 级别 DEBUG/INFO/WARNING/ERROR/CRITICAL

## 3. 事件日志（event_log.py）

- 每行 `{"ts": "<ISO8601 本地毫秒>", "event": "...", ...fields}`，ts/event 固定前两字段
- RotatingFileHandler 按 max_bytes 轮转，backup_count 保留；单行单 write 保证行完整
- `enabled: false` → 全部 no-op（不建文件不建 handler）
- 写入线程安全（内部锁）；`written` 属性供测试/统计

## 4. 事件模式

定义于 `docs/design/service.md`：服务、PTT、路由仲裁、Echo 回放与停止事件。
路由事件包含 acquired/rejected/preempted/uplink_limited；Echo 路由事件区分获取、
忙时拒绝和回放中被抢占。后续录音事件仍由 changes/002 扩展。

## 5. 决策

- 选择 JSONL 而非结构化 logging（结构化 handler 生态杂、单行 JSON 简单可 grep）
  ——变更 001 用户决策
- 事件日志独立于运行日志文件，避免机器可读与人可读互扰
