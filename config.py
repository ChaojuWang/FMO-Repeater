"""
配置管理模块

提供 FMO Echo 服务的配置加载、验证和管理功能。
支持从 YAML 文件读取配置，并与默认配置合并。
"""

import os
from typing import Dict, Any
import yaml


# 默认配置
DEFAULT_CONFIG = {
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
        'timeout': 5.0,  # 秒
        'uid': 65535,
        'callsign_prefix': 'RE>',
    },
    'logging': {
        'level': 'INFO',
        'console': True,
        'file': 'logs/fmo_repeater.log',
        'max_bytes': 10485760,  # 10MB
        'backup_count': 5,
    },
    'daemon': {
        'enabled': False,
        'pid_file': '/var/run/fmo_repeater.pid',
    }
}


def deep_merge(base: Dict, override: Dict) -> Dict:
    """
    深度合并两个字典，override 中的值会覆盖 base 中的对应值

    Args:
        base: 基础字典
        override: 覆盖字典

    Returns:
        Dict: 合并后的新字典
    """
    result = base.copy()

    for key, value in override.items():
        if key in result and isinstance(result[key], dict) and isinstance(value, dict):
            # 递归合并嵌套字典
            result[key] = deep_merge(result[key], value)
        else:
            # 直接覆盖
            result[key] = value

    return result


def load_config(config_file: str = 'config.yaml') -> Dict[str, Any]:
    """
    从 YAML 文件加载配置

    首先加载默认配置，然后从指定的 YAML 文件读取用户配置并合并。
    如果文件不存在，返回默认配置。

    Args:
        config_file: 配置文件路径（默认为 config.yaml）

    Returns:
        Dict: 合并后的配置字典

    Raises:
        yaml.YAMLError: YAML 文件格式错误
        IOError: 文件读取错误
    """
    # 从默认配置开始
    config = DEFAULT_CONFIG.copy()

    # 如果配置文件存在，读取并合并
    if os.path.exists(config_file):
        try:
            with open(config_file, 'r', encoding='utf-8') as f:
                user_config = yaml.safe_load(f)
                if user_config:  # 确保文件不为空
                    config = deep_merge(config, user_config)
                    print(f"已加载配置文件: {config_file}")
                else:
                    print(f"配置文件为空，使用默认配置: {config_file}")
        except yaml.YAMLError as e:
            raise yaml.YAMLError(f"配置文件格式错误: {e}")
        except IOError as e:
            raise IOError(f"无法读取配置文件: {e}")
    else:
        print(f"配置文件不存在，使用默认配置: {config_file}")

    return config


def validate_config(config: Dict[str, Any]) -> bool:
    """
    验证配置的完整性和合理性

    Args:
        config: 要验证的配置字典

    Returns:
        bool: 配置是否有效

    Raises:
        ValueError: 配置项缺失或无效
    """
    # 检查必需的顶级配置节
    required_sections = ['mqtt', 'topics', 'echo', 'logging']
    for section in required_sections:
        if section not in config:
            raise ValueError(f"缺少必需的配置节: {section}")

    # 检查 MQTT 配置
    mqtt = config['mqtt']
    if not mqtt.get('broker'):
        raise ValueError("MQTT broker 地址不能为空")
    if not isinstance(mqtt.get('port'), int) or not (1 <= mqtt['port'] <= 65535):
        raise ValueError("MQTT port 必须是 1-65535 之间的整数")

    # 检查主题配置
    topics = config['topics']
    if not topics.get('subscribe'):
        raise ValueError("订阅主题不能为空")
    if not topics.get('publish'):
        raise ValueError("发布主题不能为空")

    # 检查 Echo 配置
    echo = config['echo']
    if not isinstance(echo.get('timeout'), (int, float)) or echo['timeout'] <= 0:
        raise ValueError("Echo 超时时间必须是大于 0 的数值")
    if not isinstance(echo.get('uid'), int) or not (0 <= echo['uid'] <= 65535):
        raise ValueError("Echo UID 必须是 0-65535 之间的整数")
    if not isinstance(echo.get('callsign_prefix'), str):
        raise ValueError("呼号前缀必须是字符串")

    # 检查日志配置
    logging_config = config['logging']
    valid_levels = ['DEBUG', 'INFO', 'WARNING', 'ERROR', 'CRITICAL']
    if logging_config.get('level') not in valid_levels:
        raise ValueError(f"日志级别必须是以下之一: {', '.join(valid_levels)}")

    return True


def save_default_config(config_file: str = 'config.yaml'):
    """
    将默认配置保存到 YAML 文件

    用于生成配置文件模板。

    Args:
        config_file: 目标配置文件路径
    """
    try:
        with open(config_file, 'w', encoding='utf-8') as f:
            yaml.dump(DEFAULT_CONFIG, f, default_flow_style=False, allow_unicode=True)
        print(f"默认配置已保存到: {config_file}")
    except IOError as e:
        raise IOError(f"无法写入配置文件: {e}")


if __name__ == '__main__':
    # 测试：生成默认配置文件
    import sys

    if len(sys.argv) > 1 and sys.argv[1] == '--generate':
        output_file = sys.argv[2] if len(sys.argv) > 2 else 'config.yaml'
        save_default_config(output_file)
    else:
        # 测试：加载并验证配置
        config = load_config()
        try:
            validate_config(config)
            print("配置验证通过")
            print("\n当前配置:")
            print(yaml.dump(config, default_flow_style=False, allow_unicode=True))
        except ValueError as e:
            print(f"配置验证失败: {e}")
