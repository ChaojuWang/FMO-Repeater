# 提案：守护进程启动竞态修复与失败可见性

> status: merged
> 变更编号：009

## 动机

实测（strace 全量系统调用追踪 + 多次复现）发现 `start --daemon` 高概率
失败：`Daemon.start()` 在 fork① 前通过 `acquire_pid_file()` 将**原进程
自己的 PID** 写入 PID 文件，fork① 后原进程 `sys.exit(0)` 但完全退出需要
时间；孙进程在 `daemonize()` 末尾重新 `acquire_pid_file()` 时读到该
PID，此刻原进程往往**尚未退出完毕**，`pid_alive()` 返回 True → 误判
「已有活跃实例」→ 孙进程 `sys.exit(1)` 自杀。

由于此时 stdout/stderr 已被重定向到 `/dev/null`，且死亡发生在
`setup_logging()` 之前，「拒绝重复启动」的提示与任何 traceback 都不可
见：终端看似正常返回、无日志、无进程、残留指向已死中间进程的 PID 文件。
同一竞态在 c0b93da（008 之前）同样存在，仅死亡点位不同（`run_service`
内的 `write_pid_file`/检查读到未死透的祖先进程 PID）。

## 目标

1. 消除启动竞态：fork 前仅做防双实例检查、不在 PID 文件中留下祖先进程
   的 PID；孙进程在 `daemonize()` 末尾**无条件**以自己的 PID 覆盖登记。
2. 启动期失败可见：守护分支的标准错误可重定向到运行日志文件，孙进程
   侧异常与拒绝信息落入日志，不再无声死亡。
3. 保持 008 的既有语义：防双实例、stale 清理、单次 SIGTERM 有界等待、
   `atexit` 清理、PID 文件两级解析均不变。

## 范围

- `fmo_repeater/service/daemon.py`：start/daemonize 的 PID 登记时序、
  Daemon 增加可选 stderr 重定向目标、孙进程异常兜底。
- `main.py`：守护分支预加载配置（失败在前台可见地报错），将
  `logging.file` 作为守护进程 stderr 落点传入。
- `tests/test_daemon.py`：新增竞态回归与异常兜底用例（离线、无 fork、
  无真实信号）。
- `config.yaml`（本地，不入库）：清理 008 起已失效的 `daemon` 节。

## 非目标

- 不引入管道握手 / 就绪文件等启动确认机制（fork 前检查 + 孙进程无条件
  覆盖写已消除本竞态；启动确认留待真实需求出现，决策留痕见 design.md）。
- 不改变双 fork 流程与 PID 文件路径解析（008 语义保持）。
- 不处理 WSL2 DNS 优先返回 IPv6 导致 broker 域名解析异常的环境问题
  （与本次缺陷无关，排查过程中记录在案）。
