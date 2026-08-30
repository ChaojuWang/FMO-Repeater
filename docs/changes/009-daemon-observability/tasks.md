# 任务清单：守护进程启动竞态修复与失败可见性

> 状态标记：`[ ]` 待办 ｜ `[x]` 完成

## T1 daemon.py

- [x] T1.1 新增 `check_pid_file()`：fork 前只读检查（活跃拒绝 / stale 清理 / 无文件通过），不写入
- [x] T1.2 新增 `write_pid_file()`：无条件覆盖当前 PID；`daemonize()` 末尾以它替换 `acquire_pid_file()`
- [x] T1.3 `start()` 改用 `check_pid_file()`；`daemonize` 与 `target_func` 一并 try/except + finally 释放 PID 文件
- [x] T1.4 `daemonize(stderr_file=None)`：stdout/stderr 可重定向到日志文件（目录自动创建），默认行为不变

## T2 CLI 与配置

- [x] T2.1 main.py 守护分支：前台预加载并校验配置，失败可见报错；将 `logging.file` 作为 stderr_file 传入
- [x] T2.2 清理本地 config.yaml 的 `daemon` 残留节（不入库，仅用户环境）

## T3 测试与验收

- [x] T3.1 tests/test_daemon.py 新增：check_pid_file 四分支、start 竞态回归（fork 前不写 / daemonize 后覆盖）、异常兜底、stderr_file 重定向
- [x] T3.2 全量测试通过（`./run_tests.sh`）
- [x] T3.3 真实启动回归：连续 5 次启动，2s 内进程存活、T+6s 日志出现「服务已启动」；拒绝路径可见性已验证（fork 前拒绝 → 启动器终端；fork 后失败 → logging.file）

## T4 文档合并

- [x] T4.1 合并 2.1–2.3 要点入 docs/design/service.md 第 7 节，标注 Merged from changes/009
- [x] T4.2 proposal.md 标注 status: merged

## T5 缺陷修订：console 与 stderr 落点同文件的重复写（2.5）

- [x] T5.1 setup_logging 检测 stderr 已指向 logging.file（dev/ino 比较）时跳过 console handler
- [x] T5.2 新增 tests：检测函数分支、console 跳过逻辑、前台行为不变
- [x] T5.3 真实守护启动验证：日志中每条记录仅出现一次（含停止链路）

## 验收

- [x] fork 前不在 PID 文件写入任何 PID；孙进程登记为无条件覆盖写
- [x] 连续真实启动回归无失败（原竞态 3/3 复现，修复后 5/5 成功）
- [x] 守护分支启动失败信息出现在 logging.file，不再依赖 /dev/null 后的黑盒
- [x] 008 语义保持：防双实例拒绝、stale 清理、单次 SIGTERM 有界等待、restart 确认后启动
- [x] 测试无真实 fork（monkeypatch os.fork）、无真实信号

## 排障笔记（实测补充）

- 远程 broker 下 MQTT 握手可能超过 2s：验收判据「日志出现服务已启动」
  需在启动后 ≥5s 检查，T+2s 仅判进程存活。
- ~~`logging.console: true` 时 stderr 被重定向进同一日志文件，console
  handler 输出会在文件中成对出现~~ → 已按 2.5 修复（同文件检测跳过
  console handler），真实启动回归确认每条日志仅出现一次。

