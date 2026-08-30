"""配置管理测试"""

import os

import pytest
import yaml

from fmo_repeater.service.config import (
    DEFAULT_CONFIG,
    deep_merge,
    load_config,
    save_default_config,
    validate_config,
)


class TestDeepMerge:
    def test_nested_merge(self):
        base = {"a": {"b": 1, "c": 2}, "d": 3}
        override = {"a": {"b": 10}, "e": 4}
        merged = deep_merge(base, override)
        assert merged == {"a": {"b": 10, "c": 2}, "d": 3, "e": 4}
        # 原字典不被修改
        assert base == {"a": {"b": 1, "c": 2}, "d": 3}


class TestDefaultConfig:
    def test_defaults_complete(self):
        for section in (
            'mqtt', 'topics', 'transmission', 'echo',
            'event_log', 'logging', 'daemon',
        ):
            assert section in DEFAULT_CONFIG
        assert DEFAULT_CONFIG['transmission']['idle_timeout'] == 2.0
        assert DEFAULT_CONFIG['transmission']['max_uplink_duration'] == 60
        assert DEFAULT_CONFIG['echo']['vendor'] == 0x2000
        assert DEFAULT_CONFIG['echo']['uid'] == 65535
        assert DEFAULT_CONFIG['echo']['callsign_prefix'] == 'RE>'
        assert DEFAULT_CONFIG['echo']['max_duration'] == 30.0
        assert DEFAULT_CONFIG['event_log']['enabled'] is True

    def test_default_config_valid(self):
        assert validate_config(DEFAULT_CONFIG) is True


class TestLoadConfig:
    def test_missing_file_uses_defaults(self, tmp_path):
        config = load_config(str(tmp_path / "nonexistent.yaml"))
        assert config == DEFAULT_CONFIG

    def test_load_and_merge(self, tmp_path):
        user_cfg = {"mqtt": {"broker": "mqtt.example.com", "port": 8883}}
        cfg_file = tmp_path / "config.yaml"
        cfg_file.write_text(yaml.dump(user_cfg), encoding="utf-8")
        config = load_config(str(cfg_file))
        assert config['mqtt']['broker'] == 'mqtt.example.com'
        assert config['mqtt']['port'] == 8883
        # 未覆盖项保持默认
        assert config['mqtt']['keepalive'] == 60
        assert config['echo']['vendor'] == 0x2000

    def test_empty_file_uses_defaults(self, tmp_path):
        cfg_file = tmp_path / "empty.yaml"
        cfg_file.write_text("", encoding="utf-8")
        assert load_config(str(cfg_file)) == DEFAULT_CONFIG

    def test_invalid_yaml_raises(self, tmp_path):
        cfg_file = tmp_path / "bad.yaml"
        cfg_file.write_text("mqtt: [unclosed", encoding="utf-8")
        with pytest.raises(yaml.YAMLError):
            load_config(str(cfg_file))

    def test_legacy_echo_timeout_migrates(self, tmp_path):
        cfg_file = tmp_path / "legacy.yaml"
        cfg_file.write_text("echo:\n  timeout: 3.5\n", encoding="utf-8")
        config = load_config(str(cfg_file))
        assert config['transmission']['idle_timeout'] == 3.5


class TestValidateConfig:
    def _base(self):
        import copy
        return copy.deepcopy(DEFAULT_CONFIG)

    def test_missing_section(self):
        cfg = self._base()
        del cfg['echo']
        with pytest.raises(ValueError, match="echo"):
            validate_config(cfg)

    def test_vendor_reserved_zone_rejected(self):
        for vendor in (0x0000, 0x0FFF, 0x0500):
            cfg = self._base()
            cfg['echo']['vendor'] = vendor
            with pytest.raises(ValueError, match="保留区"):
                validate_config(cfg)

    def test_vendor_zones_accepted(self):
        for vendor in (0x1000, 0x1FFF, 0x2000, 0x2FFF, 0x3000, 0xFFFFFFFF):
            cfg = self._base()
            cfg['echo']['vendor'] = vendor
            assert validate_config(cfg) is True

    def test_bad_mqtt_port(self):
        cfg = self._base()
        cfg['mqtt']['port'] = 70000
        with pytest.raises(ValueError, match="port"):
            validate_config(cfg)

    def test_bad_timeout(self):
        cfg = self._base()
        cfg['transmission']['idle_timeout'] = -1
        with pytest.raises(ValueError, match="idle_timeout"):
            validate_config(cfg)

    @pytest.mark.parametrize("value", [-1, 1, 45, 121, "60"])
    def test_bad_max_uplink_duration(self, value):
        cfg = self._base()
        cfg['transmission']['max_uplink_duration'] = value
        with pytest.raises(ValueError, match="max_uplink_duration"):
            validate_config(cfg)

    def test_bad_max_duration(self):
        for v in (-1, 0):
            cfg = self._base()
            cfg['echo']['max_duration'] = v
            with pytest.raises(ValueError, match="max_duration"):
                validate_config(cfg)

    def test_empty_topics(self):
        cfg = self._base()
        cfg['topics']['subscribe'] = ''
        with pytest.raises(ValueError, match="订阅主题"):
            validate_config(cfg)

    def test_event_log_disabled_allows_empty_file(self):
        cfg = self._base()
        cfg['event_log']['enabled'] = False
        cfg['event_log']['file'] = ''
        assert validate_config(cfg) is True

    def test_event_log_enabled_requires_file(self):
        cfg = self._base()
        cfg['event_log']['file'] = ''
        with pytest.raises(ValueError, match="event_log.file"):
            validate_config(cfg)

    def test_event_log_bad_max_bytes(self):
        cfg = self._base()
        cfg['event_log']['max_bytes'] = 0
        with pytest.raises(ValueError, match="max_bytes"):
            validate_config(cfg)

    def test_bad_log_level(self):
        cfg = self._base()
        cfg['logging']['level'] = 'VERBOSE'
        with pytest.raises(ValueError, match="日志级别"):
            validate_config(cfg)


class TestSaveDefaultConfig:
    def test_save_and_reload(self, tmp_path):
        target = tmp_path / "generated.yaml"
        save_default_config(str(target))
        assert target.exists()
        loaded = yaml.safe_load(target.read_text(encoding="utf-8"))
        assert loaded == DEFAULT_CONFIG


class TestExampleConfig:
    def test_example_config_is_valid(self):
        """仓库内的 config.yaml.example 必须通过验证"""
        config = load_config("config.yaml.example")
        assert validate_config(config) is True
