"""配置管理

默认配置、YAML 加载（deep_merge 合并）、校验、模板生成。
迁移自旧 config.py，按变更 001 扩展 vendor 与 event_log 节。
"""

import os
from typing import Any, Dict

import yaml

from ..protocol.vendor import (
    is_vendor_valid,
    vendor_zone,
    VENDOR_DEFAULT,
)

# 默认配置
DEFAULT_CONFIG: Dict[str, Any] = {
    'mqtt': {
        'broker': 'localhost',
        'port': 1883,
        'username': '',
        'password': '',
        'client_id_prefix': 'fmo_repeater',
        'keepalive': 60,
    },
    'topics': {
        'subscribe': 'FMO/RAW',
        'publish': 'FMO/RAW',
    },
    'echo': {
        'timeout': 2.0,            # 流结束判定超时（秒）：松键后此时间内无新包即重放
        'vendor': VENDOR_DEFAULT,  # 重放时写入的 vendor（软件区，勿用保留区）
        'uid': 65535,              # 重放时写入的 UID（勿用 0：保持原值会被
                                   # 客户端按"自己发的"自过滤，设备收不到回声）
        'callsign_prefix': 'RE>',
    },
    'event_log': {
        'enabled': True,
        'file': 'logs/events.jsonl',
        'max_bytes': 10485760,
        'backup_count': 5,
    },
    'logging': {
        'level': 'INFO',
        'console': True,
        'file': 'logs/fmo_repeater.log',
        'max_bytes': 10485760,
        'backup_count': 5,
    },
    'daemon': {
        'enabled': False,
        'pid_file': '/var/run/fmo_repeater.pid',
    },
}


def deep_merge(base: Dict, override: Dict) -> Dict:
    """深度合并两个字典，override 覆盖 base"""
    result = base.copy()
    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            result[key] = deep_merge(result[key], value)
        else:
            result[key] = value
    return result


def load_config(config_file: str = 'config.yaml') -> Dict[str, Any]:
    """从 YAML 文件加载配置并与默认配置合并（文件不存在时用默认值）"""
    config = DEFAULT_CONFIG.copy()
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                user_config = yaml.safe_load(f)
                if user_config:
                    config = deep_merge(config, user_config)
                    print(f"已加载配置文件: {config_file}")
                else:
                    print(f"配置文件为空，使用默认配置: {config_file}")
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"配置文件格式错误: {e}") from e
        except IOError as e:
            raise IOError(f"无法读取配置文件: {e}") from e
    else:
        print(f"配置文件不存在，使用默认配置: {config_file}")
    return config


def validate_config(config: Dict[str, Any]) -> bool:
    """校验配置完整性与合理性，失败抛 ValueError"""
    required_sections = ['mqtt', 'topics', 'echo', 'event_log', 'logging']
    for section in required_sections:
        if section not in config:
            raise ValueError(f"缺少必需的配置节: {section}")

    # MQTT
    mqtt = config['mqtt']
    if not mqtt.get('broker'):
        raise ValueError("MQTT broker 地址不能为空")
    if not isinstance(mqtt.get('port'), int) or not (1 <= mqtt['port'] <= 65535):
        raise ValueError("MQTT port 必须是 1-65535 之间的整数")

    # 主题
    topics = config['topics']
    if not topics.get('subscribe'):
        raise ValueError("订阅主题不能为空")
    if not topics.get('publish'):
        raise ValueError("发布主题不能为空")

    # Echo
    echo = config['echo']
    if not isinstance(echo.get('timeout'), (int, float)) or echo['timeout'] <= 0:
        raise ValueError("Echo 超时时间必须是大于 0 的数值")
    vendor = echo.get('vendor')
    if not isinstance(vendor, int) or not (0 <= vendor <= 0xFFFFFFFF):
        raise ValueError("Echo vendor 必须是 0-0xFFFFFFFF 之间的整数")
    if not is_vendor_valid(vendor):
        raise ValueError(
            f"Echo vendor {vendor:#x} 位于保留区（0x0000-0x0FFF，FMO 项目方专用），"
            f"当前区间: {vendor_zone(vendor)}，严禁使用"
        )
    if not isinstance(echo.get('uid'), int) or not (0 <= echo['uid'] <= 0xFFFFFFFF):
        raise ValueError("Echo UID 必须是 0-0xFFFFFFFF 之间的整数")
    if not isinstance(echo.get('callsign_prefix'), str):
        raise ValueError("呼号前缀必须是字符串")

    # 事件日志
    event_log = config['event_log']
    if not isinstance(event_log.get('enabled'), bool):
        raise ValueError("event_log.enabled 必须是布尔值")
    if event_log.get('enabled'):
        if not event_log.get('file'):
            raise ValueError("event_log.file 不能为空（enabled=true 时）")
        for key in ('max_bytes', 'backup_count'):
            v = event_log.get(key)
            if not isinstance(v, int) or v <= 0:
                raise ValueError(f"event_log.{key} 必须是正整数")

    # 运行日志
    logging_config = config['logging']
    valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    if logging_config.get('level') not in valid_levels:
        raise ValueError(f"日志级别必须是以下之一: {', '.join(valid_levels)}")

    return True


def save_default_config(config_file: str = 'config.yaml'):
    """将默认配置保存为 YAML 模板"""
    try:
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False, allow_unicode=True)
        print(f"默认配置已保存到: {config_file}")
    except IOError as e:
        raise IOError(f"无法写入配置文件: {e}") from e
