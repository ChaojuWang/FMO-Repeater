#!/usr/bin/env python3
"""
测试 UID 过滤功能

验证服务是否正确忽略自己重放的消息
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fmo_header import FMORawHeader, replace_header_in_stream
import base64

# 使用 demo.py 中的测试消息
test_msg = base64.b64decode("AQAAAAAAuQEAAEJEOEJPSgAAAAAAAIMzr++DM6/vmAEAAAEABxyQmgYAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEAWAEAAAAAAlABPRQA4D0AABYZAABAPX///3fZESEIN40OABFymqqKwBCgYSIIECG/AAIriBGXPbkJiQgYG7p2mAyEESmginniKRCJqprrFzCKoBqiIyEXWMsIiMqDeKmACYiIk3eairgESZiiDYIKULiJWLEnjKkRMorKQbIvgYAFOKuZmbirZIgouYNhuwLIITW9gAgQAakAdim6qpmIABP/A0kBEQAAiJiicviZMbqxK5k3Ijm6qZqf8DcpoBiamSiIvEihhWipqZBymwiJtxoQq7NREJmZmJAAEBEBAQEBAAAAAIiImImKoQbAEqo6qFwJSwNLMBIAIbIYGcEMk4qoGwWpQbOa+JslmLuhkOQLESkTEYg+4CHZGAG6KflSAhIbsgH6KNEckZF6mJADYasQkhrYAqMzCONckpolqBiLwwo/wALJQmChixOKMPiIAhUAmf0V")

print("=== 测试 UID 过滤功能 ===\n")

# 1. 解析原始消息
print("1. 原始消息:")
original_header = FMORawHeader.from_bytes(test_msg)
print(f"   {original_header}")
print(f"   UID: {original_header.uid}")
print(f"   呼号: '{original_header.callsign}'")
print()

# 2. 模拟重放（修改为 UID=65535）
print("2. 重放消息（修改后）:")
echo_msg = replace_header_in_stream(test_msg, uid=65535, callsign="RE>BD8BOJ")
echo_header = FMORawHeader.from_bytes(echo_msg)
print(f"   {echo_header}")
print(f"   UID: {echo_header.uid}")
print(f"   呼号: '{echo_header.callsign}'")
print()

# 3. 测试过滤逻辑
print("3. 过滤逻辑测试:")
ECHO_UID = 65535

# 测试原始消息
should_process_original = original_header.uid != ECHO_UID
print(f"   原始消息 (UID={original_header.uid}): {'✅ 应该处理' if should_process_original else '❌ 应该忽略'}")

# 测试重放消息
should_process_echo = echo_header.uid != ECHO_UID
print(f"   重放消息 (UID={echo_header.uid}): {'✅ 应该处理' if should_process_echo else '❌ 应该忽略'}")
print()

# 4. 结论
print("4. 结论:")
if should_process_original and not should_process_echo:
    print("   ✅ UID 过滤逻辑正确！")
    print("   - 原始消息会被处理并缓存")
    print("   - 重放消息会被忽略，不会导致循环")
else:
    print("   ❌ UID 过滤逻辑有问题！")

print("\n服务运行时的流程：")
print("   1. 接收原始消息（UID=441）→ 添加到缓存")
print("   2. 超时触发 → 修改头部（UID→65535）→ 重放")
print("   3. 接收重放消息（UID=65535）→ 检查UID → ❌ 忽略（避免循环）")
print("   4. 继续等待新的原始消息...")
