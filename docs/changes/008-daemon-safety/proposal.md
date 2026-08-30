# 提案：守护进程安全与 CLI 单一 PID 文件

> status: merged
> 变更编号：008

## 动机

Issue #2 的 P1 项指出守护进程 PID 管理存在生产风险：`stop()` 在
`while True` 循环中反复发送 SIGTERM，目标进程不退出则永久阻塞，PID 被
复用时反复向无关进程发信号；stale PID 文件永久阻塞新启动；默认
`/var/run` 路径普通用户无写权限。P2 项同时指出 `daemon.enabled/pid_file`
在配置中存在但 CLI 从未读取，形成「配置双重事实源」。

## 目标

1. `stop()` 只发送一次 SIGTERM，使用 monotonic 有界等待；超时返回失败，
   不默认 SIGKILL。
2. 自动清理已死亡的 stale PID 文件；活跃实例存在时拒绝二次启动。
3. `restart()` 仅在确认旧进程退出后启动。
4. PID 文件固定默认 `/tmp/fmo_repeater.pid`，前台与守护模式共用；
   CLI 两级解析：`--pid-file` 显式指定 > 固定默认值。
5. 删除配置中的 `daemon` 节，消灭双重事实源。

## 范围

- 重写 `fmo_repeater/service/daemon.py` 的 PID 文件生命周期与 stop/restart。
- 从 `config.py` 与 `config.yaml.example` 删除 `daemon` 节。
- 修改 `main.py` 的 PID 文件解析与前台模式清理。
- 新增 `tests/test_daemon.py`（monkeypatch，不真实发送信号）。

## 非目标

- 不引入 systemd 单元管理、flock 独占锁或进程身份指纹（决策留痕见
  design.md 第 3 节）。
- 不改变双 fork 守护进程化整体流程。
- 不新增 TLS / broker 配置（已在 007 中明确为设计决策）。
