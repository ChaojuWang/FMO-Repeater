#!/usr/bin/env python3
"""
FMO 头部处理测试

测试 FMORawHeader 类的解析、序列化和修改功能
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fmo_header import FMORawHeader, replace_header_in_stream
import base64

print("=" * 60)
print("FMO 头部处理测试")
print("=" * 60)
print()

# 使用 demo.py 中的测试消息
test_msg = base64.b64decode("AQAAAAAAuQEAAEJEOEJPSgAAAAAAAIMzr++DM6/vmAEAAAEABxyQmgYAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAEAWAEAAAAAAlABPRQA4D0AABYZAABAPX///3fZESEIN40OABFymqqKwBCgYSIIECG/AAIriBGXPbkJiQgYG7p2mAyEESmginniKRCJqprrFzCKoBqiIyEXWMsIiMqDeKmACYiIk3eairgESZiiDYIKULiJWLEnjKkRMorKQbIvgYAFOKuZmbirZIgouYNhuwLIITW9gAgQAakAdim6qpmIABP/A0kBEQAAiJiicviZMbqxK5k3Ijm6qZqf8DcpoBiamSiIvEihhWipqZBymwiJtxoQq7NREJmZmJAAEBEBAQEBAAAAAIiImImKoQbAEqo6qFwJSwNLMBIAIbIYGcEMk4qoGwWpQbOa+JslmLuhkOQLESkTEYg+4CHZGAG6KflSAhIbsgH6KNEckZF6mJADYasQkhrYAqMzCONckpolqBiLwwo/wALJQmChixOKMPiIAhUAmf0V")

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

# ========== 测试 1: 头部解析 ==========
print("--- 测试组 1: 头部解析 ---")
print()

try:
    header = FMORawHeader.from_bytes(test_msg)
    test(
        "头部解析成功",
        True,
        f"版本={header.version}, UID={header.uid}, 呼号='{header.callsign}'"
    )

    test(
        "版本号正确",
        header.version == 1,
        f"期望: 1, 实际: {header.version}"
    )

    test(
        "UID 正确",
        header.uid == 441,
        f"期望: 441, 实际: {header.uid}"
    )

    test(
        "呼号正确",
        header.callsign == "BD8BOJ",
        f"期望: 'BD8BOJ', 实际: '{header.callsign}'"
    )

except Exception as e:
    test("头部解析", False, f"异常: {e}")

# ========== 测试 2: 头部序列化 ==========
print("--- 测试组 2: 头部序列化 ---")
print()

try:
    # 创建新的头部对象
    new_header = FMORawHeader(version=2, uid=999, callsign="TEST123")
    serialized = new_header.to_bytes()

    test(
        "序列化长度正确",
        len(serialized) == FMORawHeader.HEADER_SIZE,
        f"期望: {FMORawHeader.HEADER_SIZE}, 实际: {len(serialized)}"
    )

    test(
        "序列化结果为字节类型",
        isinstance(serialized, bytes),
        f"类型: {type(serialized)}"
    )

except Exception as e:
    test("头部序列化", False, f"异常: {e}")

# ========== 测试 3: 往返转换 ==========
print("--- 测试组 3: 往返转换（序列化 -> 反序列化）---")
print()

try:
    # 创建头部
    original = FMORawHeader(version=3, uid=12345, callsign="ECHO")

    # 序列化
    serialized = original.to_bytes()

    # 反序列化
    recovered = FMORawHeader.from_bytes(serialized)

    test(
        "版本号往返一致",
        original.version == recovered.version,
        f"原始: {original.version}, 恢复: {recovered.version}"
    )

    test(
        "UID 往返一致",
        original.uid == recovered.uid,
        f"原始: {original.uid}, 恢复: {recovered.uid}"
    )

    test(
        "呼号往返一致",
        original.callsign == recovered.callsign,
        f"原始: '{original.callsign}', 恢复: '{recovered.callsign}'"
    )

except Exception as e:
    test("往返转换", False, f"异常: {e}")

# ========== 测试 4: 头部修改 ==========
print("--- 测试组 4: 数据流头部修改 ---")
print()

try:
    # 修改 UID
    modified_uid = replace_header_in_stream(test_msg, uid=65535)
    modified_header = FMORawHeader.from_bytes(modified_uid)

    test(
        "UID 修改成功",
        modified_header.uid == 65535,
        f"期望: 65535, 实际: {modified_header.uid}"
    )

    test(
        "其他字段未改变（版本）",
        modified_header.version == 1,
        f"版本: {modified_header.version}"
    )

    test(
        "其他字段未改变（呼号）",
        modified_header.callsign == "BD8BOJ",
        f"呼号: '{modified_header.callsign}'"
    )

    # 修改呼号
    modified_callsign = replace_header_in_stream(test_msg, callsign="RE>BD8BOJ")
    modified_header2 = FMORawHeader.from_bytes(modified_callsign)

    test(
        "呼号修改成功",
        modified_header2.callsign == "RE>BD8BOJ",
        f"期望: 'RE>BD8BOJ', 实际: '{modified_header2.callsign}'"
    )

    # 同时修改多个字段
    modified_both = replace_header_in_stream(test_msg, uid=65535, callsign="RE>BD8BOJ")
    modified_header3 = FMORawHeader.from_bytes(modified_both)

    test(
        "同时修改 UID 和呼号",
        modified_header3.uid == 65535 and modified_header3.callsign == "RE>BD8BOJ",
        f"UID={modified_header3.uid}, 呼号='{modified_header3.callsign}'"
    )

except Exception as e:
    test("头部修改", False, f"异常: {e}")

# ========== 测试 5: 载荷保留 ==========
print("--- 测试组 5: 载荷保留测试 ---")
print()

try:
    original_len = len(test_msg)
    modified = replace_header_in_stream(test_msg, uid=9999)
    modified_len = len(modified)

    test(
        "修改后消息长度不变",
        original_len == modified_len,
        f"原始: {original_len}, 修改后: {modified_len}"
    )

    # 检查载荷部分是否完全相同
    original_payload = test_msg[FMORawHeader.HEADER_SIZE:]
    modified_payload = modified[FMORawHeader.HEADER_SIZE:]

    test(
        "载荷数据完全保留",
        original_payload == modified_payload,
        f"载荷长度: {len(original_payload)} 字节"
    )

except Exception as e:
    test("载荷保留", False, f"异常: {e}")

# ========== 测试 6: 边界条件 ==========
print("--- 测试组 6: 边界条件测试 ---")
print()

try:
    # 测试数据过短
    try:
        short_data = b"too short"
        FMORawHeader.from_bytes(short_data)
        test("数据过短应抛出异常", False, "没有抛出预期的异常")
    except ValueError as e:
        test("数据过短正确抛出异常", True, f"异常消息: {str(e)[:50]}...")

    # 测试超长呼号（应被截断）
    long_callsign = "A" * 20  # 20 字符，超过 12 字节
    header_long = FMORawHeader(version=1, uid=100, callsign=long_callsign)
    serialized = header_long.to_bytes()
    recovered = FMORawHeader.from_bytes(serialized)

    test(
        "超长呼号被正确截断",
        len(recovered.callsign.encode('utf-8')) <= 12,
        f"截断后长度: {len(recovered.callsign.encode('utf-8'))} 字节"
    )

    # 测试空呼号
    empty_header = FMORawHeader(version=1, uid=100, callsign="")
    serialized_empty = empty_header.to_bytes()
    recovered_empty = FMORawHeader.from_bytes(serialized_empty)

    test(
        "空呼号处理正确",
        recovered_empty.callsign == "",
        f"恢复的呼号: '{recovered_empty.callsign}'"
    )

except Exception as e:
    test("边界条件测试", False, f"意外异常: {e}")

# ========== 测试总结 ==========
print("=" * 60)
print(f"测试总结: {pass_count}/{test_count} 通过")
print("=" * 60)

if pass_count == test_count:
    print("\n✅ 所有头部处理测试通过！\n")
    sys.exit(0)
else:
    print(f"\n❌ 有 {test_count - pass_count} 个测试失败\n")
    sys.exit(1)
