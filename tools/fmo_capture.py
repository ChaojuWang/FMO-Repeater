#!/usr/bin/env python3
"""FMO raw 包抓取转存工具

订阅 MQTT 主题，把每个 FMO 消息包：
  1. 原始字节（base64）写入 JSONL（每行一个包）
  2. 解析头字段（uid / callsign / stream_begin_utc / timestamp / frame_num 等）
  3. 就地打印精简表格，便于实时观察 stream_begin_utc 变化

用途：分析设备在持续说话时 stream_begin_utc 的变化规律，从而校准 Echo
服务的流切分逻辑（不依赖猜测）。

用法：
    # 默认：订阅 config.yaml 的 topics.subscribe，抓 60 秒
    python tools/fmo_capture.py

    # 自定义时长 / 输出文件 / 主题
    python tools/fmo_capture.py --duration 120 --out /tmp/fmo_capture.jsonl

    # 凭据来源：环境变量 FMO_TEST_BROKER=host:port:user:pass，否则 config.yaml
    FMO_TEST_BROKER=host:port:user:pass python tools/fmo_capture.py --duration 90

输出结构（JSONL 每行）：
    {"recv_ts": <本地接收 monotonic>, "wall": "<HH:MM:SS.sss>", "uid": ...,
     "callsign": "...", "stream_begin_utc": ..., "timestamp": ...,
     "frame_num": ..., "bytes": ..., "raw": "<base64>"}
"""

import argparse
import base64
import json
import os
import sys
import time

# 允许从仓库根运行（conftest 已注入，但独立运行时也需要）
_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, _ROOT)

from paho.mqtt import client as mqtt  # noqa: E402
import paho.mqtt.enums  # noqa: E402
from fmo_repeater.protocol import PacketParser, ProtocolError  # noqa: E402

try:
    import yaml
except ImportError:
    yaml = None


def load_credentials(env_name="FMO_TEST_BROKER"):
    """解析凭据：env（host:port:user:pass）> 仓库根 config.yaml"""
    env = os.environ.get(env_name)
    if env:
        parts = env.split(":")
        if len(parts) == 4:
            return {"broker": parts[0], "port": int(parts[1]),
                    "username": parts[2], "password": parts[3]}
        if len(parts) == 3:
            return {"broker": parts[0], "port": 1883,
                    "username": parts[1], "password": parts[2]}
        print(f"[warn] FMO_TEST_BROKER 格式应为 host:port:user:pass，得到: {env!r}",
              file=sys.stderr)
        return None
    cfg_path = os.path.join(_ROOT, "config.yaml")
    if yaml is None or not os.path.exists(cfg_path):
        return None
    with open(cfg_path, encoding="utf-8") as f:
        cfg = (yaml.safe_load(f) or {}).get("mqtt") or {}
    if not cfg.get("broker"):
        return None
    return {"broker": cfg["broker"], "port": cfg.get("port", 1883),
            "username": cfg.get("username", ""),
            "password": cfg.get("password", "")}


def make_client(creds, on_message):
    client = mqtt.Client(
        paho.mqtt.enums.CallbackAPIVersion.VERSION2, "fmo_capture"
    )
    if creds["username"]:
        client.username_pw_set(creds["username"], creds["password"])
    client.on_message = on_message
    return client


def main():
    ap = argparse.ArgumentParser(description="FMO raw 包抓取转存工具")
    ap.add_argument("--duration", type=float, default=60.0,
                    help="抓取时长（秒），默认 60")
    ap.add_argument("--out", default="/tmp/fmo_capture.jsonl",
                    help="输出 JSONL 文件路径")
    ap.add_argument("--topic", default=None,
                    help="订阅主题（默认读 config.yaml 的 topics.subscribe）")
    ap.add_argument("--no-print", action="store_true", help="不打印到终端")
    args = ap.parse_args()

    creds = load_credentials()
    if creds is None:
        print("未找到凭据：设置 FMO_TEST_BROKER=host:port:user:pass 或提供 config.yaml",
              file=sys.stderr)
        sys.exit(1)

    topic = args.topic
    if topic is None:
        if yaml is None:
            topic = "FMO/RAW"
        else:
            cfg_path = os.path.join(_ROOT, "config.yaml")
            with open(cfg_path, encoding="utf-8") as f:
                topic = (yaml.safe_load(f) or {}).get("topics", {}).get(
                    "subscribe", "FMO/RAW")

    out_f = open(args.out, "a", encoding="utf-8")
    t0 = time.time()

    def on_message(client, userdata, msg):
        payload = msg.payload
        recv_ts = round(time.time() - t0, 3)
        wall = time.strftime("%H:%M:%S") + f".{int((time.time() % 1) * 1000):03d}"
        try:
            pkt = PacketParser.parse(payload)
            h = pkt.header
            row = {
                "recv_ts": recv_ts,
                "wall": wall,
                "uid": h.uid,
                "callsign": h.callsign,
                "vendor": h.vendor,
                "stream_begin_utc": h.stream_begin_utc,
                "timestamp": h.timestamp,
                "frame_num": h.frame_num,
                "bytes": len(payload),
                "raw": base64.b64encode(payload).decode("ascii"),
            }
            line = json.dumps(row, ensure_ascii=False)
            if not args.no_print:
                print(f"{wall} uid={h.uid:<6} {h.callsign:<10} "
                      f"sbu={h.stream_begin_utc:<12} ts={h.timestamp:<12} "
                      f"帧={h.frame_num:<2} {len(payload)}B")
        except ProtocolError as e:
            row = {"recv_ts": recv_ts, "wall": wall, "error": e.reason,
                   "bytes": len(payload),
                   "raw": base64.b64encode(payload).decode("ascii")}
            if not args.no_print:
                print(f"{wall} [INVALID {e.reason}] {len(payload)}B")
        out_f.write(json.dumps(row, ensure_ascii=False) + "\n")
        out_f.flush()

    client = make_client(creds, on_message)
    client.connect(creds["broker"], creds["port"], 60)
    client.subscribe(topic, qos=0)
    client.loop_start()

    print(f"抓取 {args.duration}s ... 主题={topic} broker={creds['broker']}", file=sys.stderr)
    try:
        end = time.time() + args.duration
        while time.time() < end:
            time.sleep(0.2)
    except KeyboardInterrupt:
        pass
    finally:
        client.loop_stop()
        client.disconnect()
        out_f.close()
        print(f"\n已保存到: {args.out}", file=sys.stderr)


if __name__ == "__main__":
    main()
