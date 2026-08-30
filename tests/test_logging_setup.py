"""运行日志配置测试（changes/009 §2.5）

覆盖：stderr 与 logging.file 同文件检测、console handler 跳过逻辑、
前台（stderr 非日志文件）行为不变。
"""

import logging
import os
import sys

import pytest

from fmo_repeater.service.logging_setup import (
    LOGGER_NAME,
    _same_file,
    _stderr_is_log_file,
    setup_logging,
)


@pytest.fixture
def config(tmp_path):
    return {
        'logging': {
            'level': 'INFO',
            'console': True,
            'file': str(tmp_path / "run.log"),
            'max_bytes': 1048576,
            'backup_count': 1,
        },
    }


@pytest.fixture(autouse=True)
def cleanup_logger():
    """测试后关闭并移除 FMORepeater 的全部 handler，避免串扰"""
    yield
    logger = logging.getLogger(LOGGER_NAME)
    for handler in list(logger.handlers):
        handler.close()
        logger.removeHandler(handler)


class TestSameFile:
    def test_fd_pointing_to_same_file(self, tmp_path):
        path = tmp_path / "run.log"
        path.write_text("")
        with open(path, 'r') as f:
            assert _same_file(f.fileno(), str(path)) is True

    def test_fd_pointing_to_other_file(self, tmp_path):
        a = tmp_path / "a.log"
        b = tmp_path / "b.log"
        a.write_text("")
        b.write_text("")
        with open(a, 'r') as f:
            assert _same_file(f.fileno(), str(b)) is False

    def test_missing_path_returns_false(self, tmp_path):
        with open(os.devnull, 'r') as f:
            assert _same_file(f.fileno(), str(tmp_path / "nope")) is False


class TestStderrIsLogFile:
    def test_stderr_without_real_fd(self, tmp_path, monkeypatch):
        # pytest capture 下 stderr 无真实 fileno：不得误判
        monkeypatch.setattr(
            sys, 'stderr',
            type('FakeStderr', (), {
                'fileno': lambda self: (_ for _ in ()).throw(ValueError()),
            })(),
        )
        assert _stderr_is_log_file(str(tmp_path / "run.log")) is False

    def test_stderr_redirected_to_log_file(self, tmp_path, monkeypatch):
        log = tmp_path / "run.log"
        log.write_text("")
        with open(log, 'a') as f:
            monkeypatch.setattr(
                sys, 'stderr',
                type('FakeStderr', (), {'fileno': lambda self: f.fileno()})(),
            )
            assert _stderr_is_log_file(str(log)) is True

    def test_stderr_points_elsewhere(self, tmp_path):
        # 真实 stderr（终端或管道）与日志文件不同：不命中
        assert _stderr_is_log_file(str(tmp_path / "run.log")) is False


class TestSetupLogging:
    def test_console_skipped_when_stderr_is_log_file(
            self, config, monkeypatch):
        monkeypatch.setattr(
            'fmo_repeater.service.logging_setup._stderr_is_log_file',
            lambda path: True,
        )
        logger = setup_logging(config)
        kinds = [type(h).__name__ for h in logger.handlers]
        # console 与文件同落点时跳过 console，杜绝重复写
        assert kinds == ['RotatingFileHandler']

    def test_console_present_when_stderr_is_not_log_file(self, config):
        logger = setup_logging(config)
        kinds = [type(h).__name__ for h in logger.handlers]
        # 前台模式行为不变：console + 文件双 handler
        assert kinds == ['StreamHandler', 'RotatingFileHandler']

    def test_console_false_unchanged(self, config):
        config['logging']['console'] = False
        logger = setup_logging(config)
        kinds = [type(h).__name__ for h in logger.handlers]
        assert kinds == ['RotatingFileHandler']

    def test_log_file_override_keeps_config_untouched(self, config, tmp_path):
        alt = tmp_path / "alt.log"
        setup_logging(config, log_file_override=str(alt))
        file_handlers = [
            h for h in logging.getLogger(LOGGER_NAME).handlers
            if isinstance(h, logging.FileHandler)
        ]
        assert file_handlers[0].baseFilename == str(alt)
        # 传入的 config 字典不被修改
        assert config['logging']['file'].endswith('run.log')
