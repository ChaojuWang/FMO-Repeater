# 设计：守护进程启动竞态修复与失败可见性

> 变更编号：009

## 1. 现状与问题

`Daemon.start()` 的执行序列（daemon.py）：

```
acquire_pid_file()      # 把原进程 PID 写入 PID 文件 ← 竞态根源
daemonize()
    fork① → 原进程 sys.exit(0)（退出动作此刻才开始，需要时间）
    setsid / umask
    fork② → 中间进程 sys.exit(0)
    重定向 stdin/stdout/stderr → /dev/null
    acquire_pid_file()  # 孙进程重读文件：读到「尚未退出完」的原进程 PID
    atexit.register(release)
target_func()           # main.py 传入 run_service，无任何异常兜底
```

strace 实测时序（PID 文件内容 → 孙进程 exit_group(1)）证明：孙进程在第
5 步读到原进程 PID 且 `pid_alive()` 为 True，误判「已有活跃实例」打印
「拒绝重复启动」后自杀；该提示已被 dup2 到 `/dev/null`，死亡发生在
`setup_logging()` 之前，日志文件尚未建立——因此用户侧表现为「正常返回、
无日志、无进程」。成败取决于原进程退出与孙进程重登记的调度顺序，故偶发
成功。c0b93da 版本竞态相同：`daemonize()` 末尾 `write_pid_file()` 裸写
不检查，但 `run_service` 内的检查读到未死透的祖先进程 PID，同样自杀。

## 2. 方案

### 2.1 PID 登记时序重构（核心修复）

原则：**fork 前只检查不占坑，fork 后无条件覆盖登记**。

```
Daemon.start(target_func, *args):
    check_pid_file()    # 新增：只读检查
                        #   活跃实例 → 报错 sys.exit(1)（语义同 008 acquire 拒绝）
                        #   stale/无效 → 清理；文件不存在 → 直接通过
    daemonize()
        fork① / setsid / fork② / 标准流重定向（不变）
        atexit.register(release_pid_file)
        write_pid_file()    # 新增：无条件写 os.getpid()，不检查
    target_func(*args, **kwargs)   # 包 try/except（见 2.3）
```

- `check_pid_file()`：`read_pid_file` 为 None → 通过；PID 活跃 → 打印
  「已有活跃实例」并 `sys.exit(1)`；否则（stale/无效内容）→
  `release_pid_file()` 清理后通过。**不写入任何 PID**。
- 孙进程末尾 `write_pid_file()` 无条件覆盖：即使文件因任何原因残留了
  祖先进程 PID，也会被孙进程自己的 PID 覆盖，竞态窗口归零。
- 前台模式（`run_service`）沿用 `acquire_pid_file()`：单进程无 fork，
  「检查 + 写入」原子性无对手方，语义不变。
- `acquire_pid_file()` 保留供前台模式与既有测试使用。

### 2.2 失败可见性：守护 stderr 落入日志文件

`daemonize()` 增加可选参数 `stderr_file: Optional[str]`（None 时保持
`/dev/null`，行为向后兼容）：

- 非 None 时，将 stdout/stderr 的 dup2 目标从 `/dev/null` 换成
  `stderr_file`（`open(path, 'a')`），运行期 print/traceback 全部落盘；
  stdin 仍重定向 `/dev/null`。
- `main.py` 守护分支先在前台**预加载并校验配置**（`load_config` +
  `validate_config`，失败时前台直接报错退出，与 008 的单一配置源一致），
  然后把 `config['logging']['file']` 作为 `stderr_file` 传入
  `daemon.start(...)`。
- 日志文件目录不存在时由 `daemonize` 内 `os.makedirs` 兜底创建。

> 取舍：不恢复「stdout/stderr 全开」——守护进程无终端可看，落文件才有
> 意义；也不把 stdout 单独分流（本服务运行日志由 logging 模块管理，
> stderr 落点只是启动兜底，两条通道合流到日志文件可接受）。

### 2.3 孙进程异常兜底

`Daemon.start()` 中 `daemonize()` 与 `target_func` 一并包裹 try/except：

```
try:
    daemonize(stderr_file)
    target_func(*args, **kwargs)
except Exception as e:
    sys.stderr.write(f"守护进程启动失败: {e}\n") + traceback
    sys.exit(1)
finally:
    release_pid_file()   # 兜底清理，双保险（atexit 之外）
```

- `SystemExit`/`BaseException` 不吞（保持 008 语义）；捕获 `Exception`
  覆盖本例全部死亡路径（拒绝启动的 SystemExit 由 sys.exit 直接传播，
  其提示在 2.2 之后已可见，无需改写）。fork①/fork② 的父进程分支以
  `sys.exit(0)` 退出，`SystemExit` 不被捕获，原进程行为不变。
- 配合 2.2，孙进程「拒绝重复启动」的遗言、PID 文件写入失败、以及未来
  任何启动期异常都会出现在 `logging.file` 中，死亡不再无声。

### 2.4 config.yaml 本地残留清理

008 已删除 `daemon` 节，本地 `config.yaml` 仍保留（deep_merge 后成多余
键，被忽略但误导排障）。更新本地文件删除该节；`config.yaml.example` 与
`DEFAULT_CONFIG` 008 起已无此节，无需改动。

### 2.5 console 与 stderr 落点同文件的重复写修复（实现期缺陷修订）

2.2 落地后暴露缺陷：`console: true` 时 `StreamHandler()` 默认流是
`sys.stderr`，而守护模式下 stderr 已被 dup2 到 `logging.file`，叠加
`RotatingFileHandler` 同文件写入 → **每条日志记录在文件中出现两遍**
（启动早期、setup_logging 之前的 print 仅单遍，与实测一致）。

修复：`setup_logging` 在安装 console handler 前检测 stderr 是否已指向
`logging.file` 同一文件（`fstat(fd)` 与 `stat(path)` 的 dev/ino 比较），
命中则跳过 console handler——语义为「console 输出已天然落在日志文件时
不再重复写」。前台模式 stderr 是终端，检测不命中，行为不变。

被否决的备选：stderr_file 改用独立的 `fmo_daemon_err.log` 与运行日志
分离（多一个文件、改变用户既有文件布局，且检测方案成本仅数行）；守护
分支隐式改写 config 的 `logging.console`（main.py 预检加载与
run_service 加载是两份配置，改了传不进去，且隐式改配置难排查）。

## 3. 被否决的备选（决策留痕）

- **管道握手 / 就绪文件启动确认**：原进程等待孙进程就绪后再退出，能把
  启动失败透传给前台。成本：跨 fork 的 fd 生命周期与超时处理复杂度显著
  上升；2.1 已消除本竞态，2.2/2.3 已让失败落盘可查，单机单实例场景收益
  不足以覆盖成本。留待真实需求（如 systemd Type=notify 集成）再评估。
- **fork 前不写、孙进程 acquire（带检查）**：仅去掉 fork 前写入仍不够——
  若文件中残留任何第三方活跃 PID（如误配置），孙进程 acquire 依旧可能
  自杀且不可见。无条件覆盖写 + fork 前集中检查更简单且语义完备。
- **flock 独占锁**：同 008 第 3 节结论，此处竞态根因是自我占坑而非
  「检查-写入」窗口，flock 不解决问题，否决。

## 4. 测试策略

延续 008 的离线风格（`FakeClock` / `FakeProcesses` / monkeypatch，无
fork、无真实信号）：

- `check_pid_file`：无文件通过；stale 清理后通过；活跃实例 → SystemExit(1)
  且文件保留；无效内容清理后通过。
- `start`（monkeypatch `os.fork` 与 `daemonize`）：fork 前文件不被写入；
  `daemonize` 后 PID 文件内容 == 孙进程（伪）PID；`target_func` 抛
  `Exception` → `SystemExit(1)` 且 PID 文件被清理。
- `daemonize(stderr_file=...)`：monkeypatch `os.dup2`，断言 stdout/stderr
  的 dup2 源 fd 打开的是指定文件而非 `/dev/null`；目录不存在时被创建。
- 真实启动回归（非 pytest，验收清单）：`start --daemon` 后 2s 内
  `status` 为运行中、`logging.file` 出现「服务已启动」，连续 N 次无失败。

## 5. 文档合并

实现完成、测试全绿后，将 2.1–2.3 要点合并入 `docs/design/service.md`
第 7 节「守护进程」，标注 `Merged from changes/009`；`proposal.md` 标注
`status: merged`。
