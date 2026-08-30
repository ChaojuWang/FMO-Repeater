"""守护进程模块

提供 Unix 守护进程化功能：
- 将进程转为后台守护进程（双重 fork）
- PID 文件生命周期（acquire / release / stop / restart / status）

PID 管理原则（changes/008，009 修订登记时序）：
- PID 文件默认固定 /tmp/fmo_repeater.pid，前台与守护模式共用
- 守护模式 fork 前只读检查（check_pid_file）：活跃实例存在则拒绝，
  stale（进程已死）则清理；fork 前不写入任何 PID（009：避免孙进程
  重登记时读到尚未退出完的原进程 PID 而误判活跃实例）
- 孙进程在 daemonize 末尾无条件覆盖登记自己的 PID（write_pid_file）
- stop 只发送一次 SIGTERM，monotonic 有界等待；超时返回失败，不 SIGKILL
- restart 仅在确认旧进程退出后启动
- 守护分支 stderr 可落入运行日志（stderr_file），启动期失败可见（009）
"""

import os
import sys
import atexit
import signal
import time
from typing import Optional

# stop 等待旧进程退出的默认上限（秒）
STOP_TIMEOUT = 5.0
# 轮询进程存活的间隔（秒）
STOP_POLL_INTERVAL = 0.05


def pid_alive(pid: int) -> bool:
    """探测进程是否存活（signal 0 只做存在性检查，不真正发送信号）"""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        # 进程存在但属于其他用户：视为存活，避免误判
        return True
    except OSError:
        return False


def read_pid_file(pid_file: str) -> Optional[int]:
    """读取 PID 文件，返回 PID；文件不存在或内容无效返回 None"""
    try:
        with open(pid_file, 'r', encoding='utf-8') as f:
            return int(f.read().strip())
    except (IOError, OSError, ValueError):
        return None


def resolve_pid_file(cli_value: Optional[str], default: str) -> str:
    """PID 文件两级解析：--pid-file 显式指定 > 固定默认值"""
    if cli_value:
        return cli_value
    return default


class Daemon:
    """
    守护进程基类

    实现 Unix 守护进程的标准化流程：
    1. Fork 第一次，父进程退出
    2. 创建新会话，成为会话组长
    3. Fork 第二次，避免获取控制终端
    4. 设置工作目录和文件权限掩码
    5. 关闭文件描述符
    6. 重定向标准输入输出
    """

    def __init__(self, pid_file: str, working_dir: str = '/'):
        """
        初始化守护进程

        Args:
            pid_file: PID 文件路径
            working_dir: 工作目录，默认为根目录
        """
        self.pid_file = pid_file
        self.working_dir = working_dir

    # ------------------------------------------------------------------
    # PID 文件生命周期
    # ------------------------------------------------------------------

    def acquire_pid_file(self) -> bool:
        """登记 PID：活跃实例存在则拒绝，stale 清理后写入当前 PID

        供前台模式（单进程，无 fork）使用；守护模式的启动检查见
        check_pid_file / write_pid_file（changes/009）。

        Returns:
            bool: 登记成功返回 True；已有活跃实例返回 False
        """
        pid = read_pid_file(self.pid_file)
        if pid is not None:
            if pid == os.getpid():
                # 自己已登记（重复 acquire），视为成功
                return True
            if pid_alive(pid):
                return False
            # stale：进程已死亡，清理后继续
            self.release_pid_file()

        self.write_pid_file()
        return True

    def write_pid_file(self) -> None:
        """无条件以当前进程 PID 覆盖写入 PID 文件（changes/009）

        供孙进程在 daemonize 末尾调用：fork 前文件中不存在任何祖先进程
        的 PID（见 check_pid_file），此处覆盖写消灭「孙进程读到尚未退出
        完的原进程 PID → 误判活跃实例」的竞态窗口。
        """
        pid_dir = os.path.dirname(self.pid_file)
        if pid_dir and not os.path.exists(pid_dir):
            os.makedirs(pid_dir, exist_ok=True)
        with open(self.pid_file, 'w', encoding='utf-8') as f:
            f.write(f"{os.getpid()}\n")

    def check_pid_file(self) -> None:
        """启动前只读检查（changes/009）：不写入任何 PID

        - PID 文件指向活跃进程 → 报错并以非零码退出（防双实例，同 008）
        - 指向已死亡进程 / 内容无效 → 清理残留后通过
        - 文件不存在 → 直接通过

        与 acquire_pid_file 的区别：不在文件中留下当前进程的 PID。
        守护模式若在 fork 前写入，孙进程重登记时可能读到「尚未退出完的
        原进程 PID」而误判已有活跃实例（changes/009 修复的竞态根源）。

        Raises:
            SystemExit: 已有活跃实例时以非零码退出
        """
        pid = read_pid_file(self.pid_file)
        if pid is None:
            if os.path.exists(self.pid_file):
                # 内容无效（非整数）：清理残留
                self.release_pid_file()
            return
        if pid != os.getpid() and pid_alive(pid):
            sys.stderr.write(
                f"PID 文件 {self.pid_file} 指向活跃进程，"
                f"守护进程可能已在运行\n"
            )
            sys.exit(1)
        # stale（进程已死亡）：清理残留
        self.release_pid_file()

    def release_pid_file(self) -> None:
        """删除 PID 文件；仅当内容指向仍存活的其他进程时不误删"""
        pid = read_pid_file(self.pid_file)
        if pid is not None and pid != os.getpid() and pid_alive(pid):
            # PID 文件指向活跃的其他实例，不误删
            return
        try:
            os.remove(self.pid_file)
        except OSError:
            pass

    # ------------------------------------------------------------------
    # 守护进程化
    # ------------------------------------------------------------------

    def daemonize(self, stderr_file: Optional[str] = None):
        """
        守护进程化

        执行双重 fork 和其他守护进程初始化步骤。
        前置条件：调用方已通过 check_pid_file 确认无活跃实例。

        Args:
            stderr_file: stdout/stderr 落盘文件；None 时保持重定向到
                /dev/null（changes/009）。指定后启动期异常与提示写入
                该文件（通常为运行日志），死亡不再无声。
        """
        # 第一次 fork
        try:
            pid = os.fork()
            if pid > 0:
                # 父进程退出
                sys.exit(0)
        except OSError as e:
            sys.stderr.write(f"第一次 fork 失败: {e.errno} ({e.strerror})\n")
            sys.exit(1)

        # 从父进程环境中分离
        os.chdir(self.working_dir)  # 改变工作目录
        os.setsid()  # 创建新会话
        os.umask(0)  # 设置文件权限掩码

        # 第二次 fork
        try:
            pid = os.fork()
            if pid > 0:
                # 第一子进程退出
                sys.exit(0)
        except OSError as e:
            sys.stderr.write(f"第二次 fork 失败: {e.errno} ({e.strerror})\n")
            sys.exit(1)

        # 重定向标准输入输出
        sys.stdout.flush()
        sys.stderr.flush()

        # 将标准输入重定向到 /dev/null
        with open('/dev/null', 'r') as devnull_r:
            os.dup2(devnull_r.fileno(), sys.stdin.fileno())

        # 将标准输出和标准错误重定向到落盘文件或 /dev/null
        if stderr_file:
            stderr_dir = os.path.dirname(stderr_file)
            if stderr_dir and not os.path.exists(stderr_dir):
                os.makedirs(stderr_dir, exist_ok=True)
            with open(stderr_file, 'a') as stdio_w:
                os.dup2(stdio_w.fileno(), sys.stdout.fileno())
                os.dup2(stdio_w.fileno(), sys.stderr.fileno())
        else:
            with open('/dev/null', 'a+') as devnull_w:
                os.dup2(devnull_w.fileno(), sys.stdout.fileno())
                os.dup2(devnull_w.fileno(), sys.stderr.fileno())

        # fork 后 PID 已变化：无条件以孙进程 PID 覆盖 PID 文件（changes/009，
        # 不做活跃检查——fork 前已由 check_pid_file 确认无活跃实例），
        # 并注册退出清理
        self.write_pid_file()
        atexit.register(self.release_pid_file)

    # ------------------------------------------------------------------
    # start / stop / restart / status
    # ------------------------------------------------------------------

    def start(self, target_func, *args, **kwargs):
        """
        启动守护进程

        Args:
            target_func: 要作为守护进程运行的函数
            stderr_file: 关键字参数，透传给 daemonize（changes/009）；
                其余 *args/**kwargs 透传给 target_func
            *args: 传递给目标函数的位置参数
            **kwargs: 传递给目标函数的关键字参数

        Raises:
            SystemExit: 已有活跃实例时以非零码退出
        """
        # fork 前只读检查，不在 PID 文件留下原进程 PID（changes/009：
        # 避免孙进程重登记时读到尚未退出完的原进程 PID 而误判活跃实例）
        self.check_pid_file()

        stderr_file = kwargs.pop('stderr_file', None)

        # daemonize 与 target_func 一并兜底（changes/009）：孙进程侧任何
        # 启动失败（含 PID 文件写入失败）落盘可见并以非零码退出
        try:
            self.daemonize(stderr_file=stderr_file)
            target_func(*args, **kwargs)
        except Exception as e:
            sys.stderr.write(f"守护进程启动失败: {e}\n")
            import traceback
            traceback.print_exc()
            sys.exit(1)
        finally:
            self.release_pid_file()

    def stop(self, timeout: float = STOP_TIMEOUT) -> bool:
        """停止守护进程：只发送一次 SIGTERM，monotonic 有界等待

        stale PID（进程已死亡）直接清理并返回成功，不发送信号。

        Returns:
            bool: 已停止（或本就未运行）返回 True；等待超时返回 False
        """
        pid = read_pid_file(self.pid_file)
        if pid is None:
            print(f"PID 文件 {self.pid_file} 不存在，守护进程未运行")
            return True

        if not pid_alive(pid):
            # stale：进程已死亡，清理残留文件
            self.release_pid_file()
            print(f"清理残留 PID 文件（进程 {pid} 已退出）")
            return True

        # 只发送一次 SIGTERM
        try:
            os.kill(pid, signal.SIGTERM)
        except PermissionError as e:
            print(f"停止守护进程失败（权限不足）: {e}")
            return False
        except ProcessLookupError:
            # 信号发出前进程恰好退出
            self.release_pid_file()
            print("守护进程已停止")
            return True
        except OSError as e:
            print(f"停止守护进程失败: {e}")
            return False

        # monotonic 有界等待目标退出
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if not pid_alive(pid):
                self.release_pid_file()
                print("守护进程已停止")
                return True
            time.sleep(STOP_POLL_INTERVAL)

        # 超时返回失败；是否 SIGKILL 由运维决定
        print(
            f"等待守护进程（PID: {pid}）退出超时（{timeout}s），"
            f"未发送 SIGKILL"
        )
        return False

    def restart(self, target_func, *args, **kwargs):
        """重启守护进程：仅在确认旧进程退出后才启动新进程"""
        if not self.stop():
            print("旧进程未退出，取消重启")
            sys.exit(1)
        self.start(target_func, *args, **kwargs)

    def status(self) -> bool:
        """检查守护进程状态；stale PID 文件自动清理

        Returns:
            bool: 正在运行返回 True
        """
        pid = read_pid_file(self.pid_file)

        if pid is None:
            print(f"守护进程未运行（PID 文件 {self.pid_file} 不存在）")
            return False

        if not pid_alive(pid):
            self.release_pid_file()
            print(
                f"守护进程未运行（残留 PID 文件已清理，"
                f"原 PID: {pid}）"
            )
            return False

        print(f"守护进程正在运行（PID: {pid}）")
        return True


if __name__ == '__main__':
    # 简单自检（不守护进程化，只验证 PID 文件逻辑）
    daemon = Daemon('/tmp/fmo_repeater_test.pid')
    if len(sys.argv) == 2 and sys.argv[1] == 'status':
        daemon.status()
    else:
        print(f"用法: python -m fmo_repeater.service.daemon status")
        sys.exit(1)
