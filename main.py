#!/usr/bin/env python3
"""
FMO Repeater 服务主入口

提供命令行接口，支持：
- 前台运行服务
- 后台守护进程模式
- 启动/停止/重启/状态查询
- 自定义配置文件
"""

import os
import sys
import argparse
import time

from config import load_config, validate_config, save_default_config
from fmo_repeater_service import FMORepeaterService
from daemon import Daemon


def run_service(config_file: str = 'config.yaml'):
    """
    运行 FMO Repeater 服务（前台模式）

    Args:
        config_file: 配置文件路径
    """
    # 加载和验证配置
    try:
        config = load_config(config_file)
        validate_config(config)
    except Exception as e:
        print(f"配置错误: {e}")
        sys.exit(1)

    # 创建并启动服务
    service = FMORepeaterService(config)

    try:
        # 连接 MQTT
        service.connect()

        # 等待连接建立
        timeout = 10
        start_time = time.time()
        while not service.connected and time.time() - start_time < timeout:
            time.sleep(0.1)

        if not service.connected:
            service.logger.error("连接 MQTT 超时")
            sys.exit(1)

        # 运行服务
        service.run()

    except Exception as e:
        print(f"启动服务失败: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)


def main():
    """
    主函数：解析命令行参数并执行相应操作
    """
    parser = argparse.ArgumentParser(
        description='FMO Repeater 服务 - 基于 MQTT 的 FMO 系统管理和工具服务',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例:
  # 前台运行服务
  python main.py start

  # 使用自定义配置文件
  python main.py start --config /path/to/config.yaml

  # 后台守护进程模式
  python main.py start --daemon

  # 停止守护进程
  python main.py stop

  # 重启守护进程
  python main.py restart

  # 查询守护进程状态
  python main.py status

  # 生成默认配置文件
  python main.py --generate-config config.yaml
"""
    )

    parser.add_argument(
        'action',
        nargs='?',
        choices=['start', 'stop', 'restart', 'status'],
        help='要执行的操作'
    )

    parser.add_argument(
        '--config', '-c',
        default='config.yaml',
        help='配置文件路径（默认: config.yaml）'
    )

    parser.add_argument(
        '--daemon', '-d',
        action='store_true',
        help='以守护进程模式运行'
    )

    parser.add_argument(
        '--generate-config',
        metavar='FILE',
        help='生成默认配置文件到指定路径'
    )

    parser.add_argument(
        '--pid-file',
        default='/var/run/fmo_repeater.pid',
        help='PID 文件路径（守护进程模式，默认: /var/run/fmo_repeater.pid）'
    )

    args = parser.parse_args()

    # 生成默认配置文件
    if args.generate_config:
        try:
            save_default_config(args.generate_config)
            print(f"默认配置已生成: {args.generate_config}")
            sys.exit(0)
        except Exception as e:
            print(f"生成配置文件失败: {e}")
            sys.exit(1)

    # 必须指定操作
    if not args.action:
        parser.print_help()
        sys.exit(1)

    # 创建守护进程对象，指定工作目录为项目目录
    # 获取当前脚本所在目录的绝对路径
    current_dir = os.path.dirname(os.path.abspath(__file__))
    daemon = Daemon(args.pid_file, working_dir=current_dir)

    # 执行相应操作
    if args.action == 'start':
        if args.daemon:
            # 守护进程模式
            print(f"以守护进程模式启动 FMO Repeater 服务...")
            print(f"PID 文件: {args.pid_file}")
            print(f"配置文件: {args.config}")
            print(f"日志位置: 请查看配置文件中的 logging.file 设置")
            daemon.start(run_service, args.config)
        else:
            # 前台模式
            print(f"启动 FMO Repeater 服务（前台模式）...")
            print(f"配置文件: {args.config}")
            print(f"按 Ctrl+C 停止服务")
            print()
            run_service(args.config)

    elif args.action == 'stop':
        print(f"停止 FMO Repeater 服务...")
        daemon.stop()

    elif args.action == 'restart':
        print(f"重启 FMO Repeater 服务...")
        daemon.restart(run_service, args.config)

    elif args.action == 'status':
        daemon.status()


if __name__ == '__main__':
    main()
