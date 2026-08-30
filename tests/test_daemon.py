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
