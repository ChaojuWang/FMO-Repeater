#!/usr/bin/env python3
"""
消息流程测试

测试消息缓存、超时检测和重放功能
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fmo_header import FMORawHeader, replace_header_in_stream
from fmo_echo_service import FMOEchoService
import base64
import time
import threading

print("=" * 60)
print("消息流程测试")
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

# ========== 测试 1: 服务初始化 ==========
print("--- 测试组 1: 服务初始化 ---")
print()

try:
    # 创建测试配置
    test_config = {
        'mqtt': {
            'broker': 'test.mosquitto.org',
            'port': 1883,
            'username': '',
            'password': '',
            'client_id_prefix': 'test_fmo_echo',
            'keepalive': 60
        },
        'topics': {
            'subscribe': 'TEST/FMO/RAW',
            'publish': 'TEST/FMO/RAW'
        },
        'echo': {
            'timeout': 2.0,
            'uid': 65535,
            'callsign_prefix': 'RE>'
        },
        'logging': {
            'level': 'WARNING',  # 测试时降低日志级别
            'console': False,
            'file': '',
            'max_bytes': 10485760,
            'backup_count': 5
        }
    }

    service = FMOEchoService(test_config)

    test(
        "服务初始化成功",
        service is not None,
        f"超时设置={service.config['echo']['timeout']}秒"
    )

    test(
        "消息缓存初始为空",
        len(service.message_buffer) == 0,
        f"缓存大小={len(service.message_buffer)}"
    )

    test(
        "初始无最后消息时间",
        service.last_message_time is None,
        "last_message_time=None"
    )

except Exception as e:
    test("服务初始化", False, f"异常: {e}")

# ========== 测试 2: 消息缓存机制 ==========
print("--- 测试组 2: 消息缓存机制 ---")
print()

try:
    # 模拟接收消息（不使用实际 MQTT）
    class MockMQTTMessage:
        def __init__(self, payload):
            self.payload = payload

    # 模拟接收第一个消息
    mock_msg = MockMQTTMessage(test_msg)
    service._on_message(None, None, mock_msg)

    test(
        "第一个消息成功缓存",
        len(service.message_buffer) == 1,
        f"缓存大小={len(service.message_buffer)}"
    )

    test(
        "设置了最后消息时间",
        service.last_message_time is not None,
        f"时间戳={service.last_message_time}"
    )

    first_time = service.last_message_time

    # 短暂延迟后模拟接收第二个消息
    time.sleep(0.1)
    service._on_message(None, None, mock_msg)

    test(
        "第二个消息成功缓存",
        len(service.message_buffer) == 2,
        f"缓存大小={len(service.message_buffer)}"
    )

    test(
        "最后消息时间已更新",
        service.last_message_time > first_time,
        f"新时间={service.last_message_time}"
    )

except Exception as e:
    test("消息缓存", False, f"异常: {e}")

# ========== 测试 3: UID 过滤 ==========
print("--- 测试组 3: UID 过滤（防止重放循环）---")
print()

try:
    # 记录当前缓存大小
    current_size = len(service.message_buffer)

    # 创建重放消息（UID = 65535）
    replay_msg = replace_header_in_stream(test_msg, uid=65535)
    mock_replay = MockMQTTMessage(replay_msg)

    # 尝试接收重放消息
    service._on_message(None, None, mock_replay)

    test(
        "重放消息被正确过滤",
        len(service.message_buffer) == current_size,
        f"缓存大小保持={len(service.message_buffer)}"
    )

    # 验证头部确实是 65535
    replay_header = FMORawHeader.from_bytes(replay_msg)
    test(
        "重放消息头部正确",
        replay_header.uid == 65535,
        f"UID={replay_header.uid}"
    )

except Exception as e:
    test("UID 过滤", False, f"异常: {e}")

# ========== 测试 4: 超时检测（未超时）==========
print("--- 测试组 4: 超时检测（未超时）---")
print()

try:
    # 重置缓存和时间
    with service.buffer_lock:
        service.message_buffer = [test_msg]
        service.last_message_time = time.time()

    # 立即检查超时（不应触发）
    service._check_timeout()

    test(
        "未超时时不触发重放",
        len(service.message_buffer) == 1,
        f"缓存大小={len(service.message_buffer)}"
    )

except Exception as e:
    test("超时检测（未超时）", False, f"异常: {e}")

# ========== 测试 5: 超时检测（已超时）==========
print("--- 测试组 5: 超时检测（已超时）---")
print()

try:
    # 创建模拟 MQTT 客户端以避免发布错误
    class MockMQTTClient:
        def __init__(self):
            self.published = []

        def publish(self, topic, payload):
            self.published.append((topic, payload))
            # 返回模拟结果对象
            class MockResult:
                rc = 0  # MQTT_ERR_SUCCESS
            return MockResult()

    # 替换为模拟客户端
    service.mqtt_client = MockMQTTClient()

    # 设置超时前的消息
    with service.buffer_lock:
        service.message_buffer = [test_msg, test_msg]
        service.last_message_time = time.time() - 5.0  # 5秒前

    original_buffer_size = len(service.message_buffer)

    # 检查超时（应触发重放）
    service._check_timeout()

    test(
        "超时后触发重放并清空缓存",
        len(service.message_buffer) == 0,
        f"原始缓存={original_buffer_size}, 清空后={len(service.message_buffer)}"
    )

    test(
        "超时后重置最后消息时间",
        service.last_message_time is None,
        "last_message_time=None"
    )

    test(
        "发布了正确数量的消息",
        len(service.mqtt_client.published) == original_buffer_size,
        f"发布数量={len(service.mqtt_client.published)}"
    )

except Exception as e:
    test("超时检测（已超时）", False, f"异常: {e}")

# ========== 测试 6: 头部修改正确性 ==========
print("--- 测试组 6: 重放时头部修改正确性 ---")
print()

try:
    # 验证发布的消息头部
    if len(service.mqtt_client.published) > 0:
        published_topic, published_payload = service.mqtt_client.published[0]

        # 解析原始和发布的头部
        original_header = FMORawHeader.from_bytes(test_msg)
        published_header = FMORawHeader.from_bytes(published_payload)

        test(
            "UID 修改为 65535",
            published_header.uid == 65535,
            f"原始UID={original_header.uid}, 修改后UID={published_header.uid}"
        )

        expected_callsign = f"RE>{original_header.callsign}"
        test(
            "呼号添加了前缀",
            published_header.callsign == expected_callsign,
            f"原始='{original_header.callsign}', 修改后='{published_header.callsign}'"
        )

        test(
            "发布到正确的主题",
            published_topic == test_config['topics']['publish'],
            f"主题='{published_topic}'"
        )

        # 验证载荷未改变
        original_payload = test_msg[FMORawHeader.HEADER_SIZE:]
        published_payload_data = published_payload[FMORawHeader.HEADER_SIZE:]

        test(
            "载荷数据完全保留",
            original_payload == published_payload_data,
            f"载荷大小={len(original_payload)}字节"
        )

except Exception as e:
    test("头部修改正确性", False, f"异常: {e}")

# ========== 测试 7: 空缓存不触发重放 ==========
print("--- 测试组 7: 空缓存不触发重放 ---")
print()

try:
    # 重置发布记录
    service.mqtt_client.published = []

    # 设置空缓存但超时
    with service.buffer_lock:
        service.message_buffer = []
        service.last_message_time = time.time() - 5.0

    service._check_timeout()

    test(
        "空缓存超时不发布消息",
        len(service.mqtt_client.published) == 0,
        "未发布任何消息"
    )

    test(
        "超时后仍重置状态",
        service.last_message_time is None,
        "last_message_time=None"
    )

except Exception as e:
    test("空缓存不触发重放", False, f"异常: {e}")

# ========== 测试 8: 线程安全 ==========
print("--- 测试组 8: 线程安全测试 ---")
print()

try:
    # 重置服务状态
    with service.buffer_lock:
        service.message_buffer = []
        service.last_message_time = None

    # 创建多个线程同时添加消息
    def add_messages():
        for _ in range(10):
            mock_msg = MockMQTTMessage(test_msg)
            service._on_message(None, None, mock_msg)

    threads = []
    for _ in range(5):
        t = threading.Thread(target=add_messages)
        threads.append(t)
        t.start()

    # 等待所有线程完成
    for t in threads:
        t.join()

    test(
        "多线程并发写入缓存",
        len(service.message_buffer) == 50,
        f"期望50个消息，实际={len(service.message_buffer)}"
    )

except Exception as e:
    test("线程安全", False, f"异常: {e}")

# ========== 测试总结 ==========
print("=" * 60)
print(f"测试总结: {pass_count}/{test_count} 通过")
print("=" * 60)

if pass_count == test_count:
    print("\n✅ 所有消息流程测试通过！\n")
    sys.exit(0)
else:
    print(f"\n❌ 有 {test_count - pass_count} 个测试失败\n")
    sys.exit(1)
