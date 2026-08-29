"""运行日志配置（可读文本 + 轮转）"""

import logging
import os
from logging.handlers import RotatingFileHandler
from typing import Any, Dict

LOGGER_NAME = 'FMORepeater'


def setup_logging(config: Dict[str, Any]) -> logging.Logger:
    """按 logging 配置节构建 Logger（控制台 + 可选轮转文件）"""
    cfg = config['logging']
    logger = logging.getLogger(LOGGER_NAME)
    logger.setLevel(getattr(logging, cfg['level']))
    logger.handlers.clear()
    logger.propagate = False

    formatter = logging.Formatter(
        '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S',
    )

    if cfg['console']:
        console_handler = logging.StreamHandler()
        console_handler.setFormatter(formatter)
        logger.addHandler(console_handler)

    log_file = cfg['file']
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
