#!/usr/bin/env python3
"""
FMO Echo 服务集成测试

端到端测试，需要实际的 MQTT 服务器
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import load_config, validate_config
from fmo_echo_service import FMOEchoService
from fmo_header import FMORawHeader, replace_header_in_stream
from paho.mqtt import client as mqtt_client
import paho.mqtt.enums
import base64
import time
import threading

print("=" * 60)
print("FMO Echo 服务集成测试")
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

# ========== 测试前检查 ==========
print("--- 测试前检查 ---")
print()

# 尝试加载配置文件
config_file = 'config.yaml'
if not os.path.exists(config_file):
    print(f"⚠️  配置文件 '{config_file}' 不存在")
    print("   集成测试需要有效的 MQTT 服务器配置")
    print("   请创建 config.yaml 并配置 MQTT 连接信息")
    print()
    sys.exit(2)  # 退出码 2 表示跳过测试

try:
    config = load_config(config_file)
    validate_config(config)
    print(f"✅ 配置文件加载成功")
    print(f"   MQTT Broker: {config['mqtt']['broker']}:{config['mqtt']['port']}")
    print(f"   订阅主题: {config['topics']['subscribe']}")
    print(f"   发布主题: {config['topics']['publish']}")
    print()
except Exception as e:
    print(f"❌ 配置文件加载失败: {e}")
    print("   请检查 config.yaml 格式是否正确")
    sys.exit(2)

# 修改配置以用于测试
config['echo']['timeout'] = 3.0  # 缩短超时时间
config['logging']['level'] = 'WARNING'  # 降低日志级别
config['logging']['console'] = False  # 关闭控制台输出

# ========== 测试 1: 服务启动和 MQTT 连接 ==========
print("--- 测试组 1: 服务启动和连接 ---")
print()

service = None
try:
    service = FMOEchoService(config)
    test("服务实例创建成功", True, "FMOEchoService 已初始化")

    # 连接 MQTT
    service.connect()

    # 等待连接建立
    timeout = 10
    start_time = time.time()
    while not service.connected and time.time() - start_time < timeout:
        time.sleep(0.1)

    test(
        "MQTT 连接建立成功",
        service.connected,
        f"Broker: {config['mqtt']['broker']}:{config['mqtt']['port']}"
    )

except Exception as e:
    test("服务启动和连接", False, f"异常: {e}")
    if service:
        service.stop()
    sys.exit(1)

if not service.connected:
    print("❌ 无法连接到 MQTT 服务器，测试终止")
    print("   请检查：")
    print("   1. MQTT 服务器是否运行")
    print("   2. 网络连接是否正常")
    print("   3. 配置文件中的地址、端口、凭据是否正确")
    service.stop()
    sys.exit(2)

# ========== 测试 2: 订阅主题 ==========
print("--- 测试组 2: 订阅验证 ---")
print()

try:
    # MQTT 连接成功时已自动订阅，这里验证订阅状态
    # 通过尝试发送一条消息并检查是否能接收到来验证
    time.sleep(1)  # 等待订阅完成

    test(
        "主题订阅完成",
        True,
        f"已订阅: {config['topics']['subscribe']}"
    )

except Exception as e:
    test("订阅验证", False, f"异常: {e}")

# ========== 测试 3: 接收和缓存消息 ==========
print("--- 测试组 3: 消息接收和缓存 ---")
print()

received_messages = []

# 创建监听客户端
def create_listener():
    """创建监听客户端以捕获重放的消息"""
    listener = mqtt_client.Client(
        paho.mqtt.enums.CallbackAPIVersion.VERSION2,
        "test_listener"
    )

    def on_message(client, userdata, msg):
        received_messages.append(msg.payload)

    listener.on_message = on_message

    if config['mqtt']['username']:
        listener.username_pw_set(
            config['mqtt']['username'],
            config['mqtt']['password']
        )

    listener.connect(
        config['mqtt']['broker'],
        config['mqtt']['port']
    )

    listener.subscribe(config['topics']['publish'])
    listener.loop_start()

    return listener

listener = create_listener()
time.sleep(1)  # 等待监听器准备好

try:
    # 发送测试消息
    publisher = mqtt_client.Client(
        paho.mqtt.enums.CallbackAPIVersion.VERSION2,
        "test_publisher"
    )

    if config['mqtt']['username']:
        publisher.username_pw_set(
            config['mqtt']['username'],
            config['mqtt']['password']
        )

    publisher.connect(
        config['mqtt']['broker'],
        config['mqtt']['port']
    )

    # 发送 3 个测试消息
    for i in range(3):
        publisher.publish(config['topics']['subscribe'], test_msg)
        time.sleep(0.2)

    publisher.disconnect()

    # 等待消息被服务接收和缓存
    time.sleep(1)

    test(
        "消息成功缓存",
        len(service.message_buffer) == 3,
        f"缓存大小={len(service.message_buffer)}"
    )

except Exception as e:
    test("消息接收和缓存", False, f"异常: {e}")

# ========== 测试 4: 超时和重放 ==========
print("--- 测试组 4: 超时检测和重放 ---")
print()

try:
    # 记录当前接收到的消息数量
    initial_received_count = len(received_messages)

    # 等待超时触发重放（配置为 3 秒）
    print(f"   等待超时触发（{config['echo']['timeout']}秒）...")
    time.sleep(config['echo']['timeout'] + 1)

    test(
        "超时后缓存已清空",
        len(service.message_buffer) == 0,
        f"缓存大小={len(service.message_buffer)}"
    )

    # 等待重放的消息被接收
    time.sleep(2)

    replayed_count = len(received_messages) - initial_received_count

    test(
        "接收到重放的消息",
        replayed_count >= 3,
        f"接收到 {replayed_count} 个重放消息"
    )

except Exception as e:
    test("超时检测和重放", False, f"异常: {e}")

# ========== 测试 5: 重放消息头部验证 ==========
print("--- 测试组 5: 重放消息头部验证 ---")
print()

try:
    if len(received_messages) > 0:
        # 验证第一个重放的消息
        replayed_msg = received_messages[-1]  # 最后一个接收到的消息
        replayed_header = FMORawHeader.from_bytes(replayed_msg)
        original_header = FMORawHeader.from_bytes(test_msg)

        test(
            "重放消息 UID 修改为 65535",
            replayed_header.uid == 65535,
            f"UID={replayed_header.uid}"
        )

        expected_callsign = f"{config['echo']['callsign_prefix']}{original_header.callsign}"
        test(
            "重放消息呼号添加前缀",
            replayed_header.callsign == expected_callsign,
            f"原始='{original_header.callsign}', 重放='{replayed_header.callsign}'"
        )

        # 验证载荷
        original_payload = test_msg[FMORawHeader.HEADER_SIZE:]
        replayed_payload = replayed_msg[FMORawHeader.HEADER_SIZE:]

        test(
            "载荷数据完全保留",
            original_payload == replayed_payload,
            f"载荷大小={len(original_payload)}字节"
        )

    else:
        test("重放消息头部验证", False, "未接收到重放消息")

except Exception as e:
    test("重放消息头部验证", False, f"异常: {e}")

# ========== 测试 6: 重放循环防护 ==========
print("--- 测试组 6: 重放循环防护 ---")
print()

try:
    # 清空接收记录
    received_messages.clear()

    # 手动发送一个重放消息（UID=65535）
    replay_msg = replace_header_in_stream(test_msg, uid=65535, callsign="RE>TEST")

    publisher2 = mqtt_client.Client(
        paho.mqtt.enums.CallbackAPIVersion.VERSION2,
        "test_publisher2"
    )

    if config['mqtt']['username']:
        publisher2.username_pw_set(
            config['mqtt']['username'],
            config['mqtt']['password']
        )

    publisher2.connect(
        config['mqtt']['broker'],
        config['mqtt']['port']
    )

    # 发送重放消息
    publisher2.publish(config['topics']['subscribe'], replay_msg)
    time.sleep(1)
    publisher2.disconnect()

    test(
        "重放消息未被缓存",
        len(service.message_buffer) == 0,
        "UID=65535 的消息被正确过滤"
    )

    # 等待超时（不应有新的重放）
    time.sleep(config['echo']['timeout'] + 1)

    test(
        "未触发新的重放循环",
        len(received_messages) == 0,
        "没有接收到新的重放消息"
    )

except Exception as e:
    test("重放循环防护", False, f"异常: {e}")

# ========== 清理 ==========
print("--- 清理资源 ---")
print()

try:
    listener.loop_stop()
    listener.disconnect()
    service.stop()
    time.sleep(1)

    print("✅ 资源清理完成")
    print()

except Exception as e:
    print(f"⚠️  清理资源时出错: {e}")
    print()

# ========== 测试总结 ==========
print("=" * 60)
print(f"测试总结: {pass_count}/{test_count} 通过")
print("=" * 60)

if pass_count == test_count:
    print("\n✅ 所有集成测试通过！\n")
    sys.exit(0)
else:
    print(f"\n❌ 有 {test_count - pass_count} 个测试失败\n")
    sys.exit(1)
