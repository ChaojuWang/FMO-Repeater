"""服务层：配置、日志、事件日志、Echo 服务、守护进程"""

from .config import (
    DEFAULT_CONFIG,
    deep_merge,
    load_config,
    validate_config,
    save_default_config,
)
from .logging_setup import setup_logging
from .event_log import EventLog
from .echo import EchoService
from .daemon import Daemon

__all__ = [
    "DEFAULT_CONFIG",
    "deep_merge",
    "load_config",
    "validate_config",
    "save_default_config",
    "setup_logging",
    "EventLog",
    "EchoService",
    "Daemon",
]
