#!/usr/bin/env python3
"""
配置管理测试

测试配置加载、合并和验证功能
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import load_config, validate_config, deep_merge, DEFAULT_CONFIG
import tempfile
import yaml

print("=" * 60)
print("配置管理测试")
print("=" * 60)
print()

test_count = 0
pass_count = 0

def test(name, condition, details=""):
    """测试辅助函数"""
    global test_count, pass_count
    test_count += 1
    if condition:
        pass_count += 1
        print(f"✅ 测试 {test_count}: {name}")
        if details:
            print(f"   {details}")
    else:
        print(f"❌ 测试 {test_count}: {name} - 失败")
        if details:
            print(f"   {details}")
    print()

# ========== 测试 1: 默认配置 ==========
print("--- 测试组 1: 默认配置 ---")
print()

try:
    # 加载不存在的配置文件，应返回默认配置
    config = load_config("nonexistent_config.yaml")

    test(
        "默认配置加载成功",
        config is not None,
        f"配置节数量: {len(config)}"
    )

    test(
        "包含 mqtt 配置节",
        'mqtt' in config,
        f"broker={config['mqtt']['broker']}"
    )

    test(
        "包含 topics 配置节",
        'topics' in config,
        f"subscribe={config['topics']['subscribe']}"
    )

    test(
        "包含 echo 配置节",
        'echo' in config,
        f"timeout={config['echo']['timeout']}, uid={config['echo']['uid']}"
    )

    test(
        "包含 logging 配置节",
        'logging' in config,
        f"level={config['logging']['level']}"
    )

except Exception as e:
    test("默认配置加载", False, f"异常: {e}")

# ========== 测试 2: 配置合并 ==========
print("--- 测试组 2: 配置合并 ---")
print()

try:
    base = {
        'a': 1,
        'b': {'c': 2, 'd': 3},
        'e': 'base'
    }

    override = {
        'b': {'c': 99},  # 应该覆盖 c，保留 d
        'e': 'override',  # 应该覆盖
        'f': 'new'  # 应该添加
    }

    merged = deep_merge(base, override)

    test(
        "顶层字段正确覆盖",
        merged['e'] == 'override',
        f"期望: 'override', 实际: '{merged['e']}'"
    )

    test(
        "嵌套字段正确覆盖",
        merged['b']['c'] == 99,
        f"期望: 99, 实际: {merged['b']['c']}"
    )

    test(
        "嵌套字段正确保留",
        merged['b']['d'] == 3,
        f"期望: 3, 实际: {merged['b']['d']}"
    )

    test(
        "新字段正确添加",
        merged['f'] == 'new',
        f"期望: 'new', 实际: '{merged['f']}'"
    )

except Exception as e:
    test("配置合并", False, f"异常: {e}")

# ========== 测试 3: YAML 文件加载 ==========
print("--- 测试组 3: YAML 文件加载 ---")
print()

try:
    # 创建临时配置文件
    with tempfile.NamedTemporaryFile(mode='w', suffix='.yaml', delete=False) as f:
        temp_config = {
            'mqtt': {
                'broker': 'test.broker.com',
                'port': 1234
            },
            'echo': {
                'timeout': 10.0
            }
        }
        yaml.dump(temp_config, f)
        temp_file = f.name

    # 加载临时配置
    config = load_config(temp_file)

    test(
        "YAML 文件加载成功",
        config is not None,
        f"配置文件: {temp_file}"
    )

    test(
        "用户配置正确覆盖",
        config['mqtt']['broker'] == 'test.broker.com',
        f"broker={config['mqtt']['broker']}"
    )

    test(
        "用户配置正确覆盖（数值）",
        config['echo']['timeout'] == 10.0,
        f"timeout={config['echo']['timeout']}"
    )

    test(
        "默认配置正确保留",
        config['mqtt']['username'] == '',
        f"username='{config['mqtt']['username']}'"
    )

    # 清理临时文件
    os.unlink(temp_file)

except Exception as e:
    test("YAML 文件加载", False, f"异常: {e}")
    if 'temp_file' in locals():
        try:
            os.unlink(temp_file)
        except:
            pass

# ========== 测试 4: 配置验证 ==========
print("--- 测试组 4: 配置验证 ---")
print()

# 测试有效配置
try:
    valid_config = DEFAULT_CONFIG.copy()
    result = validate_config(valid_config)
    test(
        "有效配置验证通过",
        result == True,
        "默认配置应该是有效的"
    )
except Exception as e:
    test("有效配置验证", False, f"异常: {e}")

# 测试缺少配置节
try:
    invalid_config = {'mqtt': {}}  # 缺少其他必需节
    validate_config(invalid_config)
    test("缺少配置节应抛出异常", False, "没有抛出预期的异常")
except ValueError as e:
    test("缺少配置节正确抛出异常", True, f"异常: {str(e)[:50]}...")
except Exception as e:
    test("缺少配置节检测", False, f"意外异常: {e}")

# 测试 MQTT broker 为空
try:
    invalid_config = DEFAULT_CONFIG.copy()
    invalid_config['mqtt']['broker'] = ''
    validate_config(invalid_config)
    test("空 broker 应抛出异常", False, "没有抛出预期的异常")
except ValueError as e:
    test("空 broker 正确抛出异常", True, f"异常: {str(e)[:50]}...")
except Exception as e:
    test("空 broker 检测", False, f"意外异常: {e}")

# 测试无效的端口号
try:
    invalid_config = DEFAULT_CONFIG.copy()
    invalid_config['mqtt']['port'] = 99999  # 超出范围
    validate_config(invalid_config)
    test("无效端口应抛出异常", False, "没有抛出预期的异常")
except ValueError as e:
    test("无效端口正确抛出异常", True, f"异常: {str(e)[:50]}...")
except Exception as e:
    test("无效端口检测", False, f"意外异常: {e}")

# 测试无效的超时时间
try:
    invalid_config = DEFAULT_CONFIG.copy()
    invalid_config['echo']['timeout'] = -1  # 负数
    validate_config(invalid_config)
    test("负数超时应抛出异常", False, "没有抛出预期的异常")
except ValueError as e:
    test("负数超时正确抛出异常", True, f"异常: {str(e)[:50]}...")
except Exception as e:
    test("负数超时检测", False, f"意外异常: {e}")

# 测试无效的日志级别
try:
    invalid_config = DEFAULT_CONFIG.copy()
    invalid_config['logging']['level'] = 'INVALID'
    validate_config(invalid_config)
    test("无效日志级别应抛出异常", False, "没有抛出预期的异常")
except ValueError as e:
    test("无效日志级别正确抛出异常", True, f"异常: {str(e)[:50]}...")
except Exception as e:
    test("无效日志级别检测", False, f"意外异常: {e}")

# 测试无效的 UID
try:
    invalid_config = DEFAULT_CONFIG.copy()
    invalid_config['echo']['uid'] = 70000  # 超出 uint16 范围
    validate_config(invalid_config)
    test("无效 UID 应抛出异常", False, "没有抛出预期的异常")
except ValueError as e:
    test("无效 UID 正确抛出异常", True, f"异常: {str(e)[:50]}...")
except Exception as e:
    test("无效 UID 检测", False, f"意外异常: {e}")

# ========== 测试总结 ==========
print("=" * 60)
print(f"测试总结: {pass_count}/{test_count} 通过")
print("=" * 60)

if pass_count == test_count:
    print("\n✅ 所有配置管理测试通过！\n")
    sys.exit(0)
else:
    print(f"\n❌ 有 {test_count - pass_count} 个测试失败\n")
    sys.exit(1)
