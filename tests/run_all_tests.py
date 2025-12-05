#!/usr/bin/env python3
"""
FMO Echo 服务测试运行器

运行所有测试并生成测试报告
"""

import subprocess
import sys
import os
from datetime import datetime

# 测试文件列表（按执行顺序）
TESTS = [
    {
        'name': '头部处理测试',
        'file': 'test_header.py',
        'description': '测试 FMO 头部解析、序列化和修改功能',
        'required': True
    },
    {
        'name': '配置管理测试',
        'file': 'test_config.py',
        'description': '测试配置加载、合并和验证功能',
        'required': True
    },
    {
        'name': 'UID 过滤测试',
        'file': 'test_uid_filter.py',
        'description': '测试 UID 过滤机制，防止重放循环',
        'required': True
    },
    {
        'name': '消息流程测试',
        'file': 'test_message_flow.py',
        'description': '测试消息缓存、超时检测和重放功能',
        'required': True
    },
    {
        'name': '集成测试',
        'file': 'test_integration.py',
        'description': '端到端集成测试（需要 MQTT 服务器）',
        'required': False
    }
]

def print_header(text):
    """打印标题"""
    print()
    print("=" * 70)
    print(f"  {text}")
    print("=" * 70)
    print()

def print_section(text):
    """打印小节标题"""
    print()
    print("-" * 70)
    print(f"  {text}")
    print("-" * 70)
    print()

def run_test(test_info):
    """
    运行单个测试

    Args:
        test_info: 测试信息字典

    Returns:
        tuple: (是否通过, 是否跳过, 执行时间)
    """
    test_file = test_info['file']
    test_path = os.path.join(os.path.dirname(__file__), test_file)

    if not os.path.exists(test_path):
        print(f"⚠️  测试文件不存在: {test_file}")
        return False, True, 0.0

    print(f"运行: {test_info['name']}")
    print(f"文件: {test_file}")
    print(f"说明: {test_info['description']}")
    print()

    start_time = datetime.now()

    try:
        # 运行测试
        result = subprocess.run(
            [sys.executable, test_path],
            cwd=os.path.dirname(os.path.dirname(test_path)),  # 项目根目录
            capture_output=True,
            text=True,
            timeout=60  # 超时时间 60 秒
        )

        end_time = datetime.now()
        elapsed = (end_time - start_time).total_seconds()

        # 打印测试输出
        if result.stdout:
            print(result.stdout)

        if result.stderr:
            print("错误输出:", file=sys.stderr)
            print(result.stderr, file=sys.stderr)

        # 检查退出码
        if result.returncode == 0:
            return True, False, elapsed
        elif result.returncode == 2:
            # 退出码 2 表示测试被跳过
            print(f"⚠️  {test_info['name']} 被跳过")
            return False, True, elapsed
        else:
            print(f"❌ {test_info['name']} 失败")
            return False, False, elapsed

    except subprocess.TimeoutExpired:
        print(f"❌ {test_info['name']} 超时")
        return False, False, 60.0

    except Exception as e:
        print(f"❌ 运行测试时出错: {e}")
        return False, False, 0.0

def main():
    """主函数"""
    print_header("FMO Echo 服务测试套件")

    print("开始时间:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print(f"Python 版本: {sys.version}")
    print(f"工作目录: {os.getcwd()}")

    # 测试结果统计
    results = []
    total_time = 0.0

    # 运行所有测试
    for test_info in TESTS:
        print_section(f"测试: {test_info['name']}")

        passed, skipped, elapsed = run_test(test_info)
        total_time += elapsed

        results.append({
            'info': test_info,
            'passed': passed,
            'skipped': skipped,
            'elapsed': elapsed
        })

    # 生成测试报告
    print_header("测试报告")

    passed_count = sum(1 for r in results if r['passed'])
    failed_count = sum(1 for r in results if not r['passed'] and not r['skipped'])
    skipped_count = sum(1 for r in results if r['skipped'])
    total_count = len(results)

    print("测试结果明细:")
    print()

    for i, result in enumerate(results, 1):
        test_info = result['info']
        status = "✅ 通过" if result['passed'] else ("⚠️  跳过" if result['skipped'] else "❌ 失败")
        required = "必需" if test_info['required'] else "可选"

        print(f"{i}. {test_info['name']}")
        print(f"   状态: {status}")
        print(f"   类型: {required}")
        print(f"   耗时: {result['elapsed']:.2f} 秒")
        print()

    print("-" * 70)
    print(f"总计: {total_count} 个测试")
    print(f"通过: {passed_count} 个 ✅")
    print(f"失败: {failed_count} 个 ❌")
    print(f"跳过: {skipped_count} 个 ⚠️")
    print(f"总耗时: {total_time:.2f} 秒")
    print("-" * 70)

    # 检查必需测试是否全部通过
    required_failed = [
        r for r in results
        if r['info']['required'] and not r['passed'] and not r['skipped']
    ]

    print()
    print("结束时间:", datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
    print()

    if required_failed:
        print("❌ 有必需测试未通过：")
        for r in required_failed:
            print(f"   - {r['info']['name']}")
        print()
        sys.exit(1)
    elif failed_count > 0:
        print("⚠️  所有必需测试通过，但有可选测试失败")
        print()
        sys.exit(0)
    else:
        print("✅ 所有测试通过！")
        print()
        sys.exit(0)

if __name__ == '__main__':
    main()
