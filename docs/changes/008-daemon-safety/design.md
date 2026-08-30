# 设计：守护进程安全与 CLI 单一 PID 文件

> 变更编号：008

## 1. 现状与问题

`daemon.py` 的现有实现存在以下问题：

- `stop()` 在 `while True` 中反复 `os.kill(pid, SIGTERM)` 并 `sleep(0.1)`，
  直到 `kill` 抛错才停止：目标进程不退出则永久阻塞；PID 被复用则反复向
  无关进程发送信号。
- PID 文件残留（进程被 kill -9 或异常退出）时，下次启动被永久拒绝。
- PID 文件默认 `/var/run/fmo_repeater.pid`，普通用户无写权限，守护模式
  实际不可用。
- `restart()` 调 `stop()` 后无条件 `sleep(1)`，未确认旧进程退出就 `start()`。
- `main.py` 的 `--pid-file` 默认值写死，`config.yaml` 的 `daemon.pid_file`
  被忽略，`daemon.enabled` 从未被读取，形成配置双重事实源。

## 2. 方案

### 2.1 固定 PID 文件，前台与守护模式共用

- PID 文件默认固定为 `/tmp/fmo_repeater.pid`；CLI 仅有两级解析：
  `--pid-file` 显式指定 > 固定默认值。
- 无论前台还是守护模式，`start` 前都执行同一 `acquire()`：
  PID 文件指向的进程仍存活 → 拒绝启动（防双实例）；指向的进程已死亡
  → 视为 stale，删除文件后继续。
- 前台模式退出（正常返回或异常）时在 `finally` 中清理自己写入的 PID；
  守护模式由 `atexit` 在进程退出时清理，且只删除仍指向自己 PID 的文件，
  不误删新实例的登记。

### 2.2 stop：单次 SIGTERM + monotonic 有界等待

```
stop(timeout=5.0) -> bool:
    stale 清理；无 PID 文件 → 返回 True（本就未运行）
    os.kill(pid, SIGTERM)          # 只发一次
    轮询 pid_alive()，间隔 0.05s，deadline = monotonic() + timeout
    已退出 → 清理 PID 文件，返回 True
    超时   → 返回 False，不发送 SIGKILL
```

超时由 `main.py` 映射为非零退出码。是否强制终止（SIGKILL）留给运维决定。

### 2.3 restart

`restart()` 先调用有界 `stop()`；仅当返回 True 才 `start()`。`stop()`
超时时取消启动，避免新旧实例叠加。

### 2.4 配置单一事实源

- 从 `DEFAULT_CONFIG` 删除 `daemon` 节；`config.yaml.example` 同步删除，
  并注明 PID 文件由 CLI `--pid-file` 控制。
- 旧配置文件中的 `daemon` 节经 `deep_merge` 合并后成为多余键，校验与
  运行均不读取，保持兼容。

## 3. 被否决的备选（决策留痕）

- **flock 独占锁**：收益仅是毫秒级"检查-写入"竞态窗口的消除，成本是
  文件描述符生命周期管理；单机单实例部署下 acquire 检查足够，否决。
- **starttime 身份指纹防 PID 复用**：`/tmp` 在主流发行版为 tmpfs，重启
  即清空，复用误杀窗口很小；将来出现多实例共机部署时再引入，否决。
- **ProcessOps 注入抽象层**：pytest `monkeypatch` 直接替换 `os.kill`
  即可覆盖全部测试分支，不需要专门抽象，否决。

## 4. 测试策略（monkeypatch，不真实发信号）

`tests/test_daemon.py` 全部通过 monkeypatch 替换 `os.kill`、
`time.monotonic`、`time.sleep`，覆盖：

- 无 PID 文件时 `stop` 返回 True 且不发信号；
- stale PID：探测到进程死亡 → 文件被清理且从未发信号；
- 单次 SIGTERM：进程由活转死 → 恰好一次 SIGTERM 调用，返回 True；
- 超时：进程始终存活 → 返回 False，仅一次 SIGTERM，无 SIGKILL，文件保留；
- 权限错误：SIGTERM 抛 `PermissionError` → 返回 False 不崩溃；
- `acquire`：活跃实例存在时拒绝；stale 时清理并写入当前 PID；
- `restart`：`stop` 超时后不启动新进程；
- `resolve_pid_file`：`--pid-file` 显式 > `/tmp` 固定默认（两级）。

## 5. 文档合并

实现完成、测试全绿后，将本节要点合并入 `docs/design/service.md` 第 7 节
「守护进程」，标注 `Merged from changes/008`；`proposal.md` 标注
`status: merged`。
