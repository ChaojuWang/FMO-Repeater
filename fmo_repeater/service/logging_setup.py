"""运行日志配置（可读文本 + 轮转）"""

import logging
import os
import sys
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional

LOGGER_NAME = 'FMORepeater'


def _same_file(fd: int, path: str) -> bool:
    """判断已打开的 fd 是否指向 path 同一文件（dev/ino 比较）

    用于守护模式：stderr 已被 dup2 到 logging.file 时，console handler
    再写 stderr 会对同一条记录产生重复写入（changes/009 §2.5）。
    """
    try:
        st_fd = os.fstat(fd)
        st_path = os.stat(path)
    except OSError:
        return False
    return (st_fd.st_dev, st_fd.st_ino) == (st_path.st_dev, st_path.st_ino)


def _stderr_is_log_file(log_file: str) -> bool:
    """检测当前进程 stderr 是否已指向 log_file 同一文件"""
    try:
        fd = sys.stderr.fileno()
    except (AttributeError, OSError, ValueError):
        # stderr 无真实 fd（如 pytest capture）：不视为同一文件
        return False
    if not log_file:
        return False
    return _same_file(fd, log_file)


def setup_logging(config: Dict[str, Any], log_file_override:
                  Optional[str] = None) -> logging.Logger:
    """按 logging 配置节构建 Logger（控制台 + 可选轮转文件）

    Args:
        config: 完整服务配置
        log_file_override: 覆盖 logging.file 的路径（默认 None）；
            供测试注入临时文件，不影响传入的 config 字典。
    """
    cfg = config['logging']
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, cfg['level']))
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    log_file = log_file_override or cfg['file']

    if cfg['console']:
        # 守护模式下 stderr 可能已被重定向到同一日志文件（changes/009
        # §2.5）：此时 console handler 会对每条记录重复写入，跳过之。
        # 前台模式 stderr 是终端，检测不命中，行为不变。
        if not _stderr_is_log_file(log_file):
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)

    if log_file:
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)
        file_handler = RotatingFileHandler(
            log_file,
            maxBytes=cfg['max_bytes'],
            backupCount=cfg['backup_count'],
            encoding='utf-8',
        )
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    return logger
