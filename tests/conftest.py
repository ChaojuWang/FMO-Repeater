"""pytest 公共 fixtures 与路径注入"""

import math
import os
import struct
import sys

import pytest

# 项目根目录注入 sys.path
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fmo_repeater.protocol import (  # noqa: E402
    PacketParser,
    PacketBuilder,
    EncodedVoiceFrame,
    HEADER_SIZE,
    COMPRESS_OPUS,
    COMPRESS_RADPCM,
)
from fmo_repeater.codecs import RadpcmEncoder  # noqa: E402


def sine_pcm16(n_samples: int, amp: int, freq: float, phase0: float = 0, dc: int = 0) -> bytes:
    """生成 8kHz/16bit/mono 正弦 PCM"""
    return struct.pack(
        f"<{n_samples}h",
        *[
            int(amp * math.sin(2 * math.pi * freq * (phase0 + n) / 8000)) + dc
            for n in range(n_samples)
        ],
    )


@pytest.fixture
def pcm_sine_640() -> bytes:
    """单帧 640 样本（80ms）正弦 PCM"""
    return sine_pcm16(640, 30000, 440, dc=500)


@pytest.fixture
def radpcm_frames():
    """连续编码的 RADPCM 帧列表（3 帧）"""
    enc = RadpcmEncoder(frame_index_start=0)
    return [enc.encode(sine_pcm16(640, 20000, 440, i * 640)) for i in range(3)]


@pytest.fixture
def make_packet():
    """合成完整消息包的工厂

    用法: make_packet(uid=42, callsign="FMOTEST", vendor=0x1111, codec=COMPRESS_RADPCM, n_frames=2)
    返回 bytes（单个消息包，帧数 ≤ 聚合上限时为一包）。
    """
    def _make(
        uid: int = 42,
        callsign: str = "FMOTEST",
        vendor: int = 0x1111,
        codec: int = COMPRESS_RADPCM,
        n_frames: int = 1,
        stream_begin_utc: int = 1700000000000 & 0xFFFFFFFF,
        srv_uid: int = 7,
        smeter: int = 3,
    ) -> bytes:
        enc = RadpcmEncoder(frame_index_start=0)
        builder = PacketBuilder(
            vendor=vendor, uid=uid, callsign=callsign,
            srv_uid=srv_uid, smeter=smeter,
        )
        packets = []
        for i in range(n_frames):
            pcm = sine_pcm16(640, 20000, 440, i * 640)
            raw = enc.encode(pcm)
            p = builder.add_frame(EncodedVoiceFrame(codec, raw))
            if p is not None:
                packets.append(p)
        tail = builder.flush()
        if tail is not None:
            packets.append(tail)
        assert len(packets) == 1, f"测试预期单包，实际 {len(packets)} 包"
        packet = packets[0]
        if stream_begin_utc is not None:
            # builder 内部取当前时间；测试需要确定值时重写头部字段
            # （CRC 仅覆盖帧区，重写 stream_begin_utc 不影响校验）
            parsed = PacketParser.parse(packet)
            packet = parsed.header.copy_with(
                stream_begin_utc=stream_begin_utc
            ).to_bytes() + packet[HEADER_SIZE:]
        return packet

    return _make


@pytest.fixture
def service_config(tmp_path):
    """Echo 服务测试配置（事件日志写入临时目录）"""
    return {
        'mqtt': {
            'broker': 'test', 'port': 1883, 'username': '', 'password': '',
            'client_id_prefix': 'test_fmo', 'keepalive': 60,
        },
        'topics': {'subscribe': 'TEST/FMO/RAW', 'publish': 'TEST/FMO/RAW'},
        'echo': {
            'timeout': 2.0, 'max_duration': 30.0,
            'vendor': 0x2000, 'uid': 65535,
            'callsign_prefix': 'RE>',
        },
        'event_log': {
            'enabled': True,
            'file': str(tmp_path / "events.jsonl"),
            'max_bytes': 1048576,
            'backup_count': 2,
        },
        'logging': {
            'level': 'CRITICAL', 'console': False,
            'file': str(tmp_path / "run.log"),
            'max_bytes': 1048576, 'backup_count': 1,
        },
    }


# ----------------------------------------------------------------------
# 集成测试基础设施（marker=integration，默认排除）
# ----------------------------------------------------------------------

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _load_mqtt_credentials():
    """解析集成测试凭据：env FMO_TEST_BROKER > 仓库根 config.yaml

    FMO_TEST_BROKER 格式: host:port:user:pass（port 可省略，默认 1883）
    返回 dict 或 None。
    """
    env = os.environ.get('FMO_TEST_BROKER')
    if env:
        parts = env.split(':')
        if len(parts) == 4:
            host, port, user, passwd = parts
            return {'broker': host, 'port': int(port), 'username': user,
                    'password': passwd}
        if len(parts) == 3:
            host, user, passwd = parts
            return {'broker': host, 'port': 1883, 'username': user,
                    'password': passwd}
        return None
    cfg_path = os.path.join(REPO_ROOT, 'config.yaml')
    if not os.path.exists(cfg_path):
        return None
    import yaml
    with open(cfg_path, encoding='utf-8') as f:
        cfg = yaml.safe_load(f) or {}
    mqtt = cfg.get('mqtt') or {}
    if not mqtt.get('broker'):
        return None
    return {
        'broker': mqtt['broker'],
        'port': mqtt.get('port', 1883),
        'username': mqtt.get('username', ''),
        'password': mqtt.get('password', ''),
    }


@pytest.fixture
def mqtt_credentials():
    """真实 broker 凭据（无 env 且无 config.yaml 时 skip 集成测试）"""
    cred = _load_mqtt_credentials()
    if cred is None:
        pytest.skip("无集成测试凭据：设置 FMO_TEST_BROKER 或提供 config.yaml")
    return cred
