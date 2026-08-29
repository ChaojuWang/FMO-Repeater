"""真实 MQTT 集成测试

固化 2026-08-30 手动启动验证的端到端流程（changes/003）：
- TestBrokerConnectivity：连接/认证/订阅回环
- TestEchoEndToEnd：真实服务进程 + 探针客户端的 Echo 重放全链路
- TestGracefulShutdown：SIGTERM 优雅停止

运行方式（默认排除，需显式启用）：
    ./run_tests.sh --integration
    python3 -m pytest tests/test_integration_mqtt.py -m integration -v

凭据来源：环境变量 FMO_TEST_BROKER（host:port:user:pass）> 仓库根 config.yaml。
无凭据时自动 skip。
"""

import json
import os
import random
import signal
import subprocess
import sys
import threading
import time

import pytest
from paho.mqtt import client as mqtt
import paho.mqtt.enums

from fmo_repeater.protocol import PacketParser

pytestmark = pytest.mark.integration

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TOPIC = "FMO/RAW"


def make_probe(credentials, name="probe"):
    """带凭据的探针客户端（已连接 + 已启动网络循环）"""
    connected = threading.Event()
    connack_rc = []

    def _on_connect(client, userdata, flags, rc, properties):
        connack_rc.append(rc)
        if rc == 0:
            connected.set()

    client = mqtt.Client(
        paho.mqtt.enums.CallbackAPIVersion.VERSION2,
        f"fmo_it_{name}_{random.randint(0, 99999)}",
    )
    if credentials['username']:
        client.username_pw_set(credentials['username'], credentials['password'])
    client.on_connect = _on_connect
    client.connect(credentials['broker'], credentials['port'], 60)
    client.loop_start()
    assert connected.wait(15), "探针连接 broker 超时"
    assert connack_rc[0] == 0, f"探针认证失败: {connack_rc[0]}"
    return client


@pytest.fixture
def probe(mqtt_credentials):
    client = make_probe(mqtt_credentials)
    yield client
    client.loop_stop()
    client.disconnect()


@pytest.fixture
def service_process(mqtt_credentials, tmp_path):
    """以子进程启动真实 Echo 服务（用临时配置，日志入 tmp_path）"""
    config = {
        'mqtt': {
            'broker': mqtt_credentials['broker'],
            'port': mqtt_credentials['port'],
            'username': mqtt_credentials['username'],
            'password': mqtt_credentials['password'],
            'client_id_prefix': 'fmo_it_svc',
            'keepalive': 60,
        },
        'topics': {'subscribe': TOPIC, 'publish': TOPIC},
        'echo': {
            'timeout': 2.0, 'vendor': 0x2000, 'uid': 65535,
            'callsign_prefix': 'RE>',
        },
        'event_log': {
            'enabled': True,
            'file': str(tmp_path / "events.jsonl"),
            'max_bytes': 1048576, 'backup_count': 2,
        },
        'logging': {
            'level': 'INFO', 'console': True,
            'file': str(tmp_path / "run.log"),
            'max_bytes': 1048576, 'backup_count': 1,
        },
    }
    import yaml
    cfg_file = tmp_path / "config.yaml"
    with open(cfg_file, 'w', encoding='utf-8') as f:
        yaml.dump(config, f, allow_unicode=True)

    proc = subprocess.Popen(
        [sys.executable, os.path.join(REPO_ROOT, 'main.py'),
         'start', '--config', str(cfg_file)],
        cwd=REPO_ROOT,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
    )
    yield proc, tmp_path
    if proc.poll() is None:
        proc.send_signal(signal.SIGTERM)
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()


def wait_service_ready(tmp_path, timeout=20):
    """等待服务完成 MQTT 连接与订阅（读 events.jsonl 出现 service_started）"""
    events_file = tmp_path / "events.jsonl"
    deadline = time.time() + timeout
    while time.time() < deadline:
        if events_file.exists():
            with open(events_file, encoding='utf-8') as f:
                for line in f:
                    try:
                        if json.loads(line).get('event') == 'service_started':
                            return True
                    except json.JSONDecodeError:
                        continue
        time.sleep(0.2)
    return False


def read_events(tmp_path):
    events_file = tmp_path / "events.jsonl"
    if not events_file.exists():
        return []
    with open(events_file, encoding='utf-8') as f:
        return [json.loads(line) for line in f if line.strip()]


# ----------------------------------------------------------------------
# 1. broker 连通性
# ----------------------------------------------------------------------

class TestBrokerConnectivity:
    def test_subscribe_loopback(self, probe):
        """发布 qos1 消息，自己订阅能收到（通路 + 认证 + ACL）

        共享 broker 上有其他发布者（真实设备/其他 Echo），用唯一负载
        标记过滤，只验证自己的消息回环。
        """
        marker = f"IT_LOOPBACK_{random.randint(0, 999999)}".encode()
        got = []
        probe.on_message = lambda c, u, m: got.append(m.payload)
        result = probe.subscribe(TOPIC, qos=1)
        assert result[0] == mqtt.MQTT_ERR_SUCCESS
        time.sleep(0.5)  # 等 SUBACK 生效
        info = probe.publish(TOPIC, marker, qos=1)
        info.wait_for_publish(timeout=5)
        deadline = time.time() + 5
        while marker not in got and time.time() < deadline:
            time.sleep(0.1)
        assert marker in got, f"未收到自己的回环消息（收到 {len(got)} 条他方消息）"


# ----------------------------------------------------------------------
# 2. Echo 端到端
# ----------------------------------------------------------------------

class TestEchoEndToEnd:
    def test_echo_replay(self, service_process, probe, make_packet):
        """发布合成 FMO 包 → 服务重放 → 校验头重写/帧区不变/防循环"""
        proc, tmp_path = service_process
        assert wait_service_ready(tmp_path), "服务未在时限内就绪"

        received = []
        probe.on_message = lambda c, u, m: received.append(m.payload)
        probe.subscribe(TOPIC, qos=1)
        time.sleep(0.5)

        packet = make_packet(uid=4321, callsign="FMOTEST", vendor=0x1111,
                             n_frames=2)
        orig = PacketParser.parse(packet)
        info = probe.publish(TOPIC, packet, qos=1)
        info.wait_for_publish(timeout=5)

        # 等待：2s 流超时 + 重放 + 回环传播
        deadline = time.time() + 10
        echoes = []
        while time.time() < deadline:
            echoes = [p for p in received if p != packet]
            if echoes:
                break
            time.sleep(0.1)
        assert echoes, "未在时限内收到 Echo 重放包"

        new = PacketParser.parse(echoes[0])
        assert new.header.vendor == 0x2000
        assert new.header.callsign == "RE>FMOTEST"
        assert new.header.uid == 65535                       # 重放 UID（D8）
        assert new.header.stream_begin_utc != orig.header.stream_begin_utc  # 回放更新 sbu
        assert new.frames == orig.frames                  # 帧区逐字节一致（CRC 仍有效）

        # 事件链路完整
        time.sleep(1)
        names = [e['event'] for e in read_events(tmp_path)]
        for expected in ('service_started', 'stream_start', 'packet_received',
                         'replay_started', 'replay_finished', 'stream_end',
                         'loop_detected'):
            assert expected in names, f"事件 {expected} 缺失: {names}"


# ----------------------------------------------------------------------
# 3. 优雅停止
# ----------------------------------------------------------------------

class TestGracefulShutdown:
    def test_sigterm_shutdown(self, service_process):
        """SIGTERM → 进程退出 → events.jsonl 记录 service_stopped"""
        proc, tmp_path = service_process
        assert wait_service_ready(tmp_path), "服务未在时限内就绪"

        proc.send_signal(signal.SIGTERM)
        rc = proc.wait(timeout=10)
        assert rc == 0, f"退出码应为 0，实际 {rc}"

        names = [e['event'] for e in read_events(tmp_path)]
        assert 'service_stopped' in names
