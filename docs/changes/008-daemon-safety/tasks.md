# 任务清单：守护进程安全与 CLI 单一 PID 文件

> 状态标记：`[ ]` 待办 ｜ `[x]` 完成

## T1 daemon.py

- [x] T1.1 acquire/release：启动前检查（活跃拒绝、stale 清理），退出时清理
- [x] T1.2 stop 只发一次 SIGTERM，monotonic 有界等待，超时返回 False 不 SIGKILL
- [x] T1.3 restart 仅在 stop 确认退出后启动

## T2 配置与 CLI

- [x] T2.1 删除 DEFAULT_CONFIG 与模板中的 daemon 节（单一事实源）
- [x] T2.2 main.py 两级解析：--pid-file 显式 > /tmp 固定默认；前台模式共用 acquire/release
- [x] T2.3 更新 config.yaml.example 与 README

## T3 测试与合并

- [x] T3.1 新增 tests/test_daemon.py（monkeypatch，不真实发信号）
- [x] T3.2 更新 test_config.py 的 daemon 断言；运行全部非集成测试
- [x] T3.3 合并设计到 docs/design 并标记 proposal merged

## 验收

- [x] 活跃实例存在时二次启动被拒绝；stale PID 被清理且不发信号
- [x] stop 只发一次 SIGTERM，超时返回失败且不 SIGKILL
- [x] restart 在旧进程未确认退出时不启动新进程
- [x] PID 文件默认 /tmp/fmo_repeater.pid，普通用户可用；--pid-file 可覆盖
- [x] 配置文件不再承载 daemon 节；旧配置中的 daemon 节被忽略且兼容
- [x] 无测试真实发送进程信号
