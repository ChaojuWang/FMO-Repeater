"""守护进程 PID 管理测试（changes/008）

全部通过 monkeypatch 替换 os.kill / time.monotonic / time.sleep，
不在测试中真实发送任何进程信号。
"""

import os
import signal
import time

import pytest

from fmo_repeater.service.daemon import (
    Daemon,
    STOP_POLL_INTERVAL,
    STOP_TIMEOUT,
    pid_alive,
    read_pid_file,
    resolve_pid_file,
)


class FakeClock:
    """可控单调时钟"""

    def __init__(self, start: float = 1000.0):
        self.now = start
        self.sleeps = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


class FakeProcesses:
    """受控进程表：alive 集合 + kill 调用记录"""

    def __init__(self, alive):
        self.alive = set(alive)
        self.kill_calls = []  # (pid, signum)

    def kill(self, pid: int, signum: int) -> None:
        self.kill_calls.append((pid, signum))
        if signum == 0:
            if pid not in self.alive:
                raise ProcessLookupError(pid)
            return
        if pid not in self.alive:
            raise ProcessLookupError(pid)
        if signum == signal.SIGTERM:
            # 模拟目标进程收到 SIGTERM 后正常退出
            self.alive.discard(pid)


@pytest.fixture
def fake_env(monkeypatch, tmp_path):
    """注入假时钟与假进程表；返回 (clock, procs) 便于断言"""
    clock = FakeClock()
    procs = FakeProcesses(alive=[os.getpid()])

    monkeypatch.setattr(time, 'monotonic', clock.monotonic)
    monkeypatch.setattr(time, 'sleep', clock.sleep)
    monkeypatch.setattr(os, 'kill', procs.kill)
    return clock, procs


@pytest.fixture
def pid_path(tmp_path) -> str:
    return str(tmp_path / "test.pid")


class TestResolvePidFile:
    def test_cli_value_wins(self):
        assert resolve_pid_file('/x/custom.pid', '/tmp/def.pid') == '/x/custom.pid'

    def test_falls_back_to_default(self):
        assert resolve_pid_file(None, '/tmp/def.pid') == '/tmp/def.pid'
        assert resolve_pid_file('', '/tmp/def.pid') == '/tmp/def.pid'


class TestPidAlive:
    def test_own_pid_alive(self, fake_env):
        _, procs = fake_env
        assert pid_alive(os.getpid()) is True

    def test_dead_pid(self, fake_env):
        _, procs = fake_env
        procs.alive.discard(os.getpid())
        assert pid_alive(os.getpid()) is False

    def test_nonpositive_pid(self):
        assert pid_alive(0) is False
        assert pid_alive(-1) is False


class TestAcquire:
    def test_acquire_fresh_file(self, fake_env, pid_path):
        daemon = Daemon(pid_path)
        assert daemon.acquire_pid_file() is True
        assert read_pid_file(pid_path) == os.getpid()

    def test_acquire_rejects_active_instance(self, fake_env, pid_path):
        procs = fake_env[1]
        with open(pid_path, 'w') as f:
            f.write("999999\n")  # 假装活跃的外部进程
        procs.alive.add(999999)

        daemon = Daemon(pid_path)
        assert daemon.acquire_pid_file() is False
        # 拒绝时不清除对方登记
        assert read_pid_file(pid_path) == 999999

    def test_acquire_cleans_stale(self, fake_env, pid_path):
        procs = fake_env[1]
        with open(pid_path, 'w') as f:
            f.write("888888\n")  # 已死亡进程
        assert 888888 not in procs.alive

        daemon = Daemon(pid_path)
        assert daemon.acquire_pid_file() is True
        assert read_pid_file(pid_path) == os.getpid()

    def test_acquire_invalid_file_content(self, fake_env, pid_path):
        with open(pid_path, 'w') as f:
            f.write("not-a-pid\n")
        daemon = Daemon(pid_path)
        assert daemon.acquire_pid_file() is True


class TestRelease:
    def test_release_removes_own_file(self, fake_env, pid_path):
        daemon = Daemon(pid_path)
        daemon.acquire_pid_file()
        daemon.release_pid_file()
        assert not os.path.exists(pid_path)

    def test_release_skips_foreign_file(self, fake_env, pid_path):
        _, procs = fake_env
        procs.alive.add(12345)  # 活跃的外部实例
        with open(pid_path, 'w') as f:
            f.write("12345\n")
        daemon = Daemon(pid_path)
        daemon.release_pid_file()
        # 不误删
        assert read_pid_file(pid_path) == 12345

    def test_release_removes_stale_foreign_file(self, fake_env, pid_path):
        with open(pid_path, 'w') as f:
            f.write("12345\n")  # 已死亡的外部实例
        daemon = Daemon(pid_path)
        daemon.release_pid_file()
        assert not os.path.exists(pid_path)


class TestStop:
    def test_stop_no_file_returns_true(self, fake_env, pid_path):
        daemon = Daemon(pid_path)
        assert daemon.stop() is True

    def test_stop_stale_cleans_without_signal(self, fake_env, pid_path):
        clock, procs = fake_env
        with open(pid_path, 'w') as f:
            f.write("777777\n")  # 已死亡

        daemon = Daemon(pid_path)
        assert daemon.stop() is True
        # 信号 0 是存在性探测，不算发送信号；不得出现真正的 SIGTERM/SIGKILL
        real_signals = [
            c for c in procs.kill_calls if c[1] not in (0,)
        ]
        assert real_signals == []
        assert not os.path.exists(pid_path)  # 残留已清理

    def test_stop_sends_single_sigterm_and_waits(self, fake_env, pid_path):
        clock, procs = fake_env
        procs.alive.add(5555)
        with open(pid_path, 'w') as f:
            f.write("5555\n")

        daemon = Daemon(pid_path)
        assert daemon.stop(timeout=STOP_TIMEOUT) is True
        sigterms = [c for c in procs.kill_calls if c[1] == signal.SIGTERM]
        assert sigterms == [(5555, signal.SIGTERM)]  # 恰好一次
        assert not os.path.exists(pid_path)

    def test_stop_timeout_returns_false_keeps_file(self, fake_env, pid_path):
        clock, procs = fake_env
        procs.alive.add(6666)

        class Immortal(FakeProcesses):
            def kill(self, pid, signum):
                # 收到 SIGTERM 也不退出
                self.kill_calls.append((pid, signum))

        imm = Immortal(alive=procs.alive)
        import fmo_repeater.service.daemon as daemon_mod
        original_kill = os.kill

        # 替换 stop 使用的 os.kill 为不退出版本
        daemon_mod.os.kill = imm.kill
        try:
            daemon = Daemon(pid_path)
            with open(pid_path, 'w') as f:
                f.write("6666\n")
            assert daemon.stop(timeout=0.2) is False
        finally:
            daemon_mod.os.kill = original_kill

        sigterms = [c for c in imm.kill_calls if c[1] == signal.SIGTERM]
        assert sigterms == [(6666, signal.SIGTERM)]  # 仅一次
        assert all(c[1] != signal.SIGKILL for c in imm.kill_calls)  # 无 SIGKILL
        assert os.path.exists(pid_path)  # 文件保留
        # 有界：轮询发生且时间推进超过 timeout
        assert clock.now > 1000.0 + 0.2

    def test_stop_permission_error_returns_false(self, monkeypatch, fake_env,
                                                  pid_path):
        _, procs = fake_env
        procs.alive.add(4444)

        def denied(pid, signum):
            if signum == 0:
                return
            raise PermissionError("denied")

        monkeypatch.setattr(os, 'kill', denied)

        with open(pid_path, 'w') as f:
            f.write("4444\n")
        daemon = Daemon(pid_path)
        assert daemon.stop() is False
        assert os.path.exists(pid_path)  # 失败时不清文件


class TestRestart:
    def test_restart_aborts_when_stop_times_out(self, monkeypatch, fake_env,
                                                 pid_path):
        _, procs = fake_env
        procs.alive.add(3333)

        class Immortal(FakeProcesses):
            def kill(self, pid, signum):
                self.kill_calls.append((pid, signum))

        monkeypatch.setattr(os, 'kill', Immortal(alive=procs.alive).kill)

        with open(pid_path, 'w') as f:
            f.write("3333\n")
        daemon = Daemon(pid_path)

        started = []
        with pytest.raises(SystemExit) as exc:
            daemon.restart(started.append)
        assert exc.value.code == 1
        assert started == []  # 未启动新进程

    def test_restart_starts_after_confirmed_exit(self, monkeypatch, fake_env,
                                                  pid_path):
        _, procs = fake_env
        procs.alive.add(2222)
        with open(pid_path, 'w') as f:
            f.write("2222\n")

        daemon = Daemon(pid_path)
        # daemon.start 会 fork；替换 fork 为直接运行 target 的伪实现
        calls = []

        def fake_start(target, *a, **kw):
            calls.append((target, a, kw))

        monkeypatch.setattr(daemon, 'start', fake_start)
        daemon.restart(lambda *a, **k: None, 1, 2)
        assert len(calls) == 1  # 确认旧进程退出后才启动


class TestStatus:
    def test_status_no_file(self, fake_env, pid_path):
        daemon = Daemon(pid_path)
        assert daemon.status() is False

    def test_status_running(self, fake_env, pid_path):
        procs = fake_env[1]
        procs.alive.add(1111)
        with open(pid_path, 'w') as f:
            f.write("1111\n")
        daemon = Daemon(pid_path)
        assert daemon.status() is True
        assert os.path.exists(pid_path)  # 活跃时不清文件

    def test_status_stale_cleans(self, fake_env, pid_path):
        with open(pid_path, 'w') as f:
            f.write("9999\n")  # 已死亡
        daemon = Daemon(pid_path)
        assert daemon.status() is False
        assert not os.path.exists(pid_path)


class TestTimeoutConstants:
    def test_bounds_defined(self):
        assert STOP_TIMEOUT == 5.0
        assert 0 < STOP_POLL_INTERVAL < 1.0


# ---------------------------------------------------------------------------
# changes/009：启动竞态修复与失败可见性
# ---------------------------------------------------------------------------

class TestCheckPidFile:
    """fork 前只读检查：不写入任何 PID"""

    def test_passes_when_no_file(self, fake_env, pid_path):
        daemon = Daemon(pid_path)
        daemon.check_pid_file()  # 不抛异常即通过
        assert not os.path.exists(pid_path)  # 关键：检查不落笔

    def test_passes_and_cleans_stale(self, fake_env, pid_path):
        with open(pid_path, 'w') as f:
            f.write("777777\n")  # 已死亡进程
        daemon = Daemon(pid_path)
        daemon.check_pid_file()
        assert not os.path.exists(pid_path)  # stale 清理，且未写入自己

    def test_rejects_active_instance(self, fake_env, pid_path):
        procs = fake_env[1]
        procs.alive.add(999999)
        with open(pid_path, 'w') as f:
            f.write("999999\n")
        daemon = Daemon(pid_path)
        with pytest.raises(SystemExit) as exc:
            daemon.check_pid_file()
        assert exc.value.code == 1
        # 拒绝时保留对方登记
        assert read_pid_file(pid_path) == 999999

    def test_cleans_invalid_content(self, fake_env, pid_path):
        with open(pid_path, 'w') as f:
            f.write("not-a-pid\n")
        daemon = Daemon(pid_path)
        daemon.check_pid_file()
        assert not os.path.exists(pid_path)


class TestWritePidFile:
    def test_overwrites_with_current_pid(self, fake_env, pid_path):
        # 文件里残留任何旧内容都被无条件覆盖
        with open(pid_path, 'w') as f:
            f.write("123456\n")
        daemon = Daemon(pid_path)
        daemon.write_pid_file()
        assert read_pid_file(pid_path) == os.getpid()


class TestStartRace:
    """守护 start 的 PID 登记时序：fork 前不写，daemonize 后覆盖登记"""

    def test_start_no_pid_written_before_daemonize(self, fake_env, pid_path,
                                                   monkeypatch):
        """竞态根源回归用例：daemonize 开始时文件必须不存在"""
        daemon = Daemon(pid_path)
        order = []

        def fake_daemonize(stderr_file=None):
            order.append(('daemonize_begin', os.path.exists(pid_path)))
            daemon.write_pid_file()
            order.append(
                ('daemonize_end_pid', read_pid_file(pid_path))
            )

        monkeypatch.setattr(daemon, 'daemonize', fake_daemonize)
        daemon.start(lambda *a, **k: None)

        assert order[0] == ('daemonize_begin', False)  # fork 前未写入
        # daemonize 末尾登记的是当前（孙）进程的 PID
        assert order[1] == ('daemonize_end_pid', os.getpid())

    def test_start_overwrites_stale_content_after_daemonize(
            self, fake_env, pid_path, monkeypatch):
        """daemonize 末尾登记为无条件覆盖（即便文件曾残留第三方内容）"""
        daemon = Daemon(pid_path)
        seen = {}

        def fake_daemonize(stderr_file=None):
            # daemonize 执行时 stale 已被 check_pid_file 清理，
            # 随后无条件覆盖登记当前（孙）进程 PID
            seen['at_daemonize'] = read_pid_file(pid_path)
            daemon.write_pid_file()
            seen['after'] = read_pid_file(pid_path)

        with open(pid_path, 'w') as f:
            f.write("777777\n")  # 已死亡进程的残留

        monkeypatch.setattr(daemon, 'daemonize', fake_daemonize)
        daemon.start(lambda *a, **k: None)
        # check_pid_file 已在 daemonize 前清理 stale；daemonize 覆盖登记自己
        assert seen == {'at_daemonize': None, 'after': os.getpid()}

    def test_start_cleans_pid_file_when_target_raises(
            self, fake_env, pid_path, monkeypatch):
        """target_func 异常 → 以非零码退出且 PID 文件被兜底清理"""

        def boom(*a, **k):
            raise RuntimeError("模拟启动失败")

        daemon = Daemon(pid_path)
        monkeypatch.setattr(
            daemon, 'daemonize',
            lambda stderr_file=None: daemon.write_pid_file(),
        )
        with pytest.raises(SystemExit) as exc:
            daemon.start(boom)
        assert exc.value.code == 1
        assert not os.path.exists(pid_path)  # finally 兜底清理

    def test_start_cleans_pid_file_on_normal_return(
            self, fake_env, pid_path, monkeypatch):
        daemon = Daemon(pid_path)
        monkeypatch.setattr(
            daemon, 'daemonize',
            lambda stderr_file=None: daemon.write_pid_file(),
        )
        daemon.start(lambda *a, **k: None)
        assert not os.path.exists(pid_path)

    def test_start_passes_stderr_file_to_daemonize_only(
            self, fake_env, pid_path, monkeypatch):
        seen = {}
        received = {}
        daemon = Daemon(pid_path)

        def fake_daemonize(stderr_file=None):
            seen['stderr_file'] = stderr_file
            daemon.write_pid_file()

        def target(*args, **kwargs):
            received.update(kwargs)

        monkeypatch.setattr(daemon, 'daemonize', fake_daemonize)
        daemon.start(target, 'cfg.yaml', 'p.pid', stderr_file='/x/err.log')
        assert seen['stderr_file'] == '/x/err.log'
        # 不污染 target 参数
        assert 'stderr_file' not in received


class TestDaemonizeStderrFile:
    """daemonize(stderr_file=...)：stdout/stderr 落盘而非 /dev/null

    monkeypatch os.fork 恒返回 0（走子进程分支），测试进程不真实 fork、
    不真实退出。stdin/stdout/stderr 的 fileno 由 pytest capture 接管
    （无真实 fd），统一 patch 为常量 0/1/2；os.dup2 换成记录器后，
    用 open 返回的真实文件对象 + /proc/self/fd 验证重定向源。
    """

    @pytest.fixture
    def child_env(self, monkeypatch, tmp_path):
        import builtins

        import fmo_repeater.service.daemon as dm

        monkeypatch.setattr(dm.os, 'fork', lambda: 0)  # 恒为子进程分支
        monkeypatch.setattr(dm.os, 'setsid', lambda: 0)
        monkeypatch.setattr(dm.os, 'umask', lambda m: 0)  # 不污染进程 umask
        # pytest capture 接管了标准流（无真实 fileno），patch 为常量
        monkeypatch.setattr(dm.sys.stdin, 'fileno', lambda: 0, raising=False)
        monkeypatch.setattr(dm.sys.stdout, 'fileno', lambda: 1, raising=False)
        monkeypatch.setattr(dm.sys.stderr, 'fileno', lambda: 2, raising=False)
        # 不真实注册 atexit，避免污染测试进程
        monkeypatch.setattr(dm.atexit, 'register', lambda f: None)

        real_open = builtins.open  # 先留原件，避免 tracking_open 递归
        opened_fds = {}   # fd -> 打开时的真实路径（仅 daemonize 打开的文件）
        dup2_record = []  # (源文件 basename, 重定向目标 fd)

        def tracking_open(file, mode='r', *args, **kwargs):
            fobj = real_open(file, mode, *args, **kwargs)
            try:
                opened_fds[fobj.fileno()] = os.path.realpath(str(file))
            except OSError:
                pass
            return fobj

        def recording_dup2(fd, target):
            # 只记录 daemonize 自己打开的 fd 的重定向；不真实执行，
            # 避免污染测试进程的标准流（pytest 自身的 dup2 调用因 fd
            # 不在 opened_fds 中被自动忽略）
            src = opened_fds.get(fd)
            if src is not None:
                dup2_record.append((os.path.basename(src), target))
            return target

        monkeypatch.setattr(dm.os, 'dup2', recording_dup2)
        monkeypatch.setattr(builtins, 'open', tracking_open)
        return dup2_record

    def test_stdout_stderr_go_to_stderr_file(self, child_env, tmp_path):
        dup2_record = child_env
        err_file = tmp_path / "logs" / "daemon_err.log"
        daemon = Daemon(str(tmp_path / "test.pid"), working_dir=str(tmp_path))

        daemon.daemonize(stderr_file=str(err_file))

        # 目录自动创建，文件以追加模式存在
        assert err_file.parent.is_dir()
        assert err_file.exists()
        # 三次重定向：stdin←/dev/null，stdout/stderr←stderr_file
        assert dup2_record == [
            ('null', 0),
            ('daemon_err.log', 1),
            ('daemon_err.log', 2),
        ]

    def test_default_keeps_devnull(self, child_env, tmp_path):
        dup2_record = child_env
        daemon = Daemon(str(tmp_path / "test.pid"), working_dir=str(tmp_path))

        daemon.daemonize()

        assert dup2_record == [
            ('null', 0),
            ('null', 1),
            ('null', 2),
        ]


