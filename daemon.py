"""
守护进程模块

提供 Unix 守护进程化功能，支持：
- 将进程转为后台守护进程
- PID 文件管理
- 信号处理
"""

import os
import sys
import atexit
import signal


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

    def daemonize(self):
        """
        守护进程化

        执行双重 fork 和其他守护进程初始化步骤。
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

        # 将标准输出和标准错误重定向到 /dev/null
        with open('/dev/null', 'a+') as devnull_w:
            os.dup2(devnull_w.fileno(), sys.stdout.fileno())
            os.dup2(devnull_w.fileno(), sys.stderr.fileno())

        # 注册退出时删除 PID 文件的函数
        atexit.register(self.delete_pid_file)

        # 写入 PID 文件
        self.write_pid_file()

    def write_pid_file(self):
        """
        写入 PID 文件
        """
        pid = str(os.getpid())
        try:
            # 确保 PID 文件目录存在
            pid_dir = os.path.dirname(self.pid_file)
            if pid_dir and not os.path.exists(pid_dir):
                os.makedirs(pid_dir, exist_ok=True)

            with open(self.pid_file, 'w+') as f:
                f.write(f"{pid}\n")
        except Exception as e:
            sys.stderr.write(f"无法写入 PID 文件 {self.pid_file}: {e}\n")
            sys.exit(1)

    def delete_pid_file(self):
        """
        删除 PID 文件
        """
        if os.path.exists(self.pid_file):
            os.remove(self.pid_file)

    def get_pid_from_file(self):
        """
        从 PID 文件读取进程 ID

        Returns:
            int or None: 进程 ID，如果文件不存在或无效则返回 None
        """
        try:
            with open(self.pid_file, 'r') as f:
                return int(f.read().strip())
        except (IOError, ValueError):
            return None

    def start(self, target_func, *args, **kwargs):
        """
        启动守护进程

        Args:
            target_func: 要作为守护进程运行的函数
            *args: 传递给目标函数的位置参数
            **kwargs: 传递给目标函数的关键字参数
        """
        # 检查 PID 文件是否存在
        if self.get_pid_from_file():
            sys.stderr.write(f"PID 文件 {self.pid_file} 已存在，守护进程可能已在运行\n")
            sys.exit(1)

        # 守护进程化
        self.daemonize()

        # 运行目标函数
        target_func(*args, **kwargs)

    def stop(self):
        """
        停止守护进程

        向守护进程发送 SIGTERM 信号。
        """
        # 从 PID 文件获取进程 ID
        pid = self.get_pid_from_file()

        if not pid:
            sys.stderr.write(f"PID 文件 {self.pid_file} 不存在，守护进程未运行？\n")
            return

        # 尝试杀死进程
        try:
            while True:
                os.kill(pid, signal.SIGTERM)
                import time
                time.sleep(0.1)
        except OSError as e:
            error_str = str(e)
            if 'No such process' in error_str:
                # 进程已不存在，删除 PID 文件
                if os.path.exists(self.pid_file):
                    os.remove(self.pid_file)
                print(f"守护进程已停止")
            else:
                print(f"停止守护进程失败: {error_str}")
                sys.exit(1)

    def restart(self, target_func, *args, **kwargs):
        """
        重启守护进程

        Args:
            target_func: 要作为守护进程运行的函数
            *args: 传递给目标函数的位置参数
            **kwargs: 传递给目标函数的关键字参数
        """
        self.stop()
        import time
        time.sleep(1)  # 等待进程完全停止
        self.start(target_func, *args, **kwargs)

    def status(self):
        """
        检查守护进程状态
        """
        pid = self.get_pid_from_file()

        if not pid:
            print(f"守护进程未运行（PID 文件 {self.pid_file} 不存在）")
            return False

        # 检查进程是否存在
        try:
            # 发送信号 0 不会真正发送信号，只是检查进程是否存在
            os.kill(pid, 0)
            print(f"守护进程正在运行（PID: {pid}）")
            return True
        except OSError:
            print(f"守护进程未运行（但 PID 文件存在，可能是异常退出）")
            return False


if __name__ == '__main__':
    # 测试代码
    import time

    def test_daemon():
        """测试守护进程函数"""
        with open('/tmp/fmo_repeater_test.log', 'a') as f:
            for i in range(30):
                f.write(f"守护进程运行中... {i}\n")
                f.flush()
                time.sleep(1)

    daemon = Daemon('/tmp/fmo_repeater_test.pid')

    if len(sys.argv) == 2:
        if sys.argv[1] == 'start':
            print("启动测试守护进程...")
            daemon.start(test_daemon)
        elif sys.argv[1] == 'stop':
            print("停止测试守护进程...")
            daemon.stop()
        elif sys.argv[1] == 'restart':
            print("重启测试守护进程...")
            daemon.restart(test_daemon)
        elif sys.argv[1] == 'status':
            daemon.status()
        else:
            print(f"未知命令: {sys.argv[1]}")
            sys.exit(1)
    else:
        print("用法: python daemon.py {start|stop|restart|status}")
        sys.exit(1)
