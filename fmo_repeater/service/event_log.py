"""结构化事件日志（JSONL）

每行一个 JSON 对象，固定首字段 ts（ISO8601 本地时间）与 event。
按字节数轮转（backup_count 保留旧文件）；enabled=false 时全部 no-op。

事件模式见 docs/changes/001-protocol-refactor/design.md §5.2。
"""

import json
import logging
import os
import threading
from datetime import datetime
from logging.handlers import RotatingFileHandler
from typing import Any, Dict, Optional

_LOGGER_NAME = 'FMORepeater.EventLog'


class _JsonlFormatter(logging.Formatter):
    """将 LogRecord.msg（dict）格式化为单行 JSON"""

    def format(self, record: logging.LogRecord) -> str:
        payload = record.msg
        if not isinstance(payload, dict):
            payload = {"event": str(payload)}
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"))


class EventLog:
    """JSONL 事件日志（线程安全）"""

    def __init__(self, config: Dict[str, Any]):
        cfg = config.get('event_log', {})
        self.enabled = bool(cfg.get('enabled', False))
        self._logger: Optional[logging.Logger] = None
        self._lock = threading.Lock()
        self._count = 0

        if not self.enabled:
            return

        log_file = cfg['file']
        log_dir = os.path.dirname(log_file)
        if log_dir and not os.path.exists(log_dir):
            os.makedirs(log_dir, exist_ok=True)

        logger = logging.getLogger(_LOGGER_NAME)
        logger.setLevel(logging.INFO)
        logger.handlers.clear()
        logger.propagate = False

        handler = RotatingFileHandler(
            log_file,
            maxBytes=cfg.get('max_bytes', 10 * 1024 * 1024),
            backupCount=cfg.get('backup_count', 5),
            encoding='utf-8',
        )
        handler.setFormatter(_JsonlFormatter())
        logger.addHandler(handler)
        self._logger = logger

    def log(self, event: str, **fields: Any) -> None:
        """写入一条事件（disabled 时 no-op）

        ts 与 event 固定在前；其余字段按 kwargs 顺序。
        """
        if not self.enabled or self._logger is None:
            return
        payload: Dict[str, Any] = {
            "ts": datetime.now().isoformat(timespec="milliseconds"),
            "event": event,
        }
        payload.update(fields)
        with self._lock:
            self._logger.info(payload)
            self._count += 1

    @property
    def written(self) -> int:
        """已写入事件数（测试/统计用）"""
        return self._count

    def close(self) -> None:
        if self._logger is not None:
            for h in self._logger.handlers:
                h.close()
            self._logger.handlers.clear()
