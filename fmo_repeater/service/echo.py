"""Echo（回音）服务

基于 FMO 协议 v1（变更 001）重写：
- 订阅 MQTT 主题接收消息包，解析 64B 消息头
- 缓存同一发送者的连续语音流，超时判定流结束
- 流结束后重写头部（vendor / callsign 前缀 / 可选 uid）并按原始接收时间轴重放
- 防循环：vendor==本机重放 vendor 且 callsign 以前缀开头 → 跳过（决策 D1）
- 帧区与 CRC 不动（CRC 仅覆盖帧区，帧区不变则校验值仍有效）
"""

from __future__ import annotations

import queue
import random
import signal
import threading
import time
from typing import Any, Dict, List, Optional, Tuple

from paho.mqtt import client as mqtt_client
import paho.mqtt.enums

from ..protocol import (
    PacketParser,
    ParsedPacket,
    ProtocolError,
)
from .event_log import EventLog
from .logging_setup import setup_logging


class EchoService:
    """FMO Echo 服务主类"""

    def __init__(self, config: Dict[str, Any], event_log: Optional[EventLog] = None):
        self.config = config
        self.logger = setup_logging(config)
        self.event_log = event_log or EventLog(config)

        # 重放头重写参数
        echo_cfg = config['echo']
        self.replay_vendor: int = echo_cfg['vendor']
        self.replay_uid: int = echo_cfg['uid']            # 0 = 保持原值
        self.callsign_prefix: str = echo_cfg['callsign_prefix']
        self.stream_timeout: float = echo_cfg['timeout']

        # 单消费者线程模型：MQTT 回调只入队，消费者线程串行化 缓存→超时→回放，
        # 消除并发操作 buffer 的竞态（此前导致流被切成多段）。
        self._queue: "queue.Queue[ParsedPacket]" = queue.Queue()
        self._consumer_thread: Optional[threading.Thread] = None
        self._consumer_stop = threading.Event()
        # 消费者线程内部状态（仅该线程访问，无锁）
        self._buffer: List[Tuple[ParsedPacket, float]] = []
        self.last_message_time: Optional[float] = None

        # 统计
        self.invalid_packets = 0
        self.loop_packets = 0

        # MQTT
        self.mqtt_client: Optional[mqtt_client.Client] = None
        self.connected = False

        # 运行状态
        self.running = False
        self._shutdown_requested = False   # 信号处理设置的独立标志（run() 前收到信号也保留）
        self._stopped = False              # 是否已执行过 stop（幂等保护）
        self.check_interval = 0.1

        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        self.logger.info("FMO Echo 服务已初始化（协议 v1）")
        self.logger.info(f"流结束超时: {self.stream_timeout}s，重放 vendor: {self.replay_vendor:#06x}")
        self.logger.info(f"订阅主题: {self.config['topics']['subscribe']}")
        self.logger.info(f"发布主题: {self.config['topics']['publish']}")

    # ------------------------------------------------------------------
    # MQTT 回调
    # ------------------------------------------------------------------

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        if reason_code == 0:
            self.connected = True
            self.logger.info(
                f"已连接到 MQTT 代理: {self.config['mqtt']['broker']}:{self.config['mqtt']['port']}"
            )
            topic = self.config['topics']['subscribe']
            result = client.subscribe(topic)
            self.logger.info(f"已订阅主题: {topic}, 结果: {result}")
            self.event_log.log(
                "service_started",
                version=1,
                vendor=self.replay_vendor,
                subscribe_topic=topic,
                echo_timeout=self.stream_timeout,
            )
        else:
            self.connected = False
            self.logger.error(f"连接 MQTT 代理失败，返回码: {reason_code}")

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        self.connected = False
        if reason_code == 0:
            self.logger.info("已主动断开 MQTT 连接")
        else:
            self.logger.warning(f"MQTT 连接断开，原因码: {reason_code}")

    def _on_message(self, client, userdata, msg):
        """消息接收：解析 → 防循环 → 入队（消费逻辑在独立线程串行执行）"""
        try:
            try:
                packet = PacketParser.parse(msg.payload)
            except ProtocolError as e:
                self.invalid_packets += 1
                self.event_log.log(
                    "packet_invalid", reason=e.reason, bytes=len(msg.payload)
                )
                self.logger.warning(
                    f"丢弃非法消息包（{e.reason}）: {e.detail}，长度 {len(msg.payload)}B"
                )
                return

            header = packet.header
            if self._is_own_replay(header):
                self.loop_packets += 1
                self.event_log.log(
                    "loop_detected",
                    uid=header.uid,
                    callsign=header.callsign,
                    vendor=header.vendor,
                )
                self.logger.debug(
                    f"忽略自己重放的消息 - UID={header.uid}, 呼号='{header.callsign}'"
                )
                return

            # 只入队，串行消费交给消费者线程
            self._queue.put(packet)
        except Exception as e:  # 防御：回调内异常不得中断网络线程
            self.logger.error(f"处理接收消息时出错: {e}", exc_info=True)

    # ------------------------------------------------------------------
    # 防循环（决策 D1）
    # ------------------------------------------------------------------

    def _is_own_replay(self, header) -> bool:
        """判定消息包是否为本服务重放的回声（vendor + 呼号前缀双条件）"""
        if header.vendor != self.replay_vendor:
            return False
        if self.replay_uid and header.uid == self.replay_uid:
            return True
        return header.callsign.startswith(self.callsign_prefix)

    # ------------------------------------------------------------------
    # 连接
    # ------------------------------------------------------------------

    def connect(self):
        """连接 MQTT 代理并订阅"""
        client_id = (
            f"{self.config['mqtt']['client_id_prefix']}_{random.randint(0, 10000)}"
        )
        self.mqtt_client = mqtt_client.Client(
            paho.mqtt.enums.CallbackAPIVersion.VERSION2, client_id
        )
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_disconnect = self._on_disconnect
        self.mqtt_client.on_message = self._on_message

        if self.config['mqtt']['username']:
            self.mqtt_client.username_pw_set(
                self.config['mqtt']['username'], self.config['mqtt']['password']
            )

        self.logger.info(
            f"正在连接到 MQTT 代理: "
            f"{self.config['mqtt']['broker']}:{self.config['mqtt']['port']}"
        )
        try:
            self.mqtt_client.connect(
                self.config['mqtt']['broker'],
                self.config['mqtt']['port'],
                self.config['mqtt']['keepalive'],
            )
            self.mqtt_client.loop_start()
        except Exception as e:
            self.logger.error(f"连接 MQTT 代理失败: {e}", exc_info=True)
            raise

    # ------------------------------------------------------------------
    # 超时与重放
    # ------------------------------------------------------------------

    def _emit_received(self, packet):
        """记录一个真实包到缓冲并写事件（消费者线程内调用）"""
        header = packet.header
        is_stream_start = not self._buffer
        self._buffer.append((packet, time.monotonic()))
        self.last_message_time = time.time()

        if is_stream_start:
            self.event_log.log(
                "stream_start",
                uid=header.uid,
                callsign=header.callsign,
                vendor=header.vendor,
                stream_begin_utc=header.stream_begin_utc,
                frames=header.frame_num,
            )
        self.event_log.log(
            "packet_received",
            uid=header.uid,
            callsign=header.callsign,
            vendor=header.vendor,
            frames=header.frame_num,
            bytes=len(packet.data),
            checksum_ok=True,
        )
        self.logger.debug(
            f"接收消息 [缓存 {len(self._buffer)}] - UID={header.uid}, "
            f"呼号='{header.callsign}', 帧数={header.frame_num}"
        )

    def _maybe_replay_if_idle(self):
        """若缓冲非空且距最后一包 ≥ timeout 秒 → 回放整段。

        规则（用户澄清）：回放进行中到达的包丢弃；回放完成那一刻起重新缓存。
        实现：回放前清空队列里已到达的包 → 同步回放 → 回放后清空队列（回放
        期间新到达的包）并重置 buffer/last_message_time。
        """
        if not self._buffer or self.last_message_time is None:
            return
        if time.time() - self.last_message_time >= self.stream_timeout:
            self.logger.info(
                f"检测到流结束（≥{self.stream_timeout}s 无新消息），"
                f"重放 {len(self._buffer)} 个消息包"
            )
            self._replay_messages(self._buffer)
            self._buffer = []
            self.last_message_time = None
            self._drain_queue()  # 丢弃回放过程中新到达的包，从此刻重新缓存

    def _drain_queue(self):
        """清空队列（丢弃回放期间到达、不参与下一段回放的包）"""
        while True:
            try:
                self._queue.get_nowait()
            except queue.Empty:
                break

    def _consume_once(self, timeout):
        """消费一个包（或超时触发回放）；返回 True 表示取到包，False 表示超时。

        消费者线程与测试都可用它做同步、确定性的验证。
        """
        try:
            packet = self._queue.get(timeout=timeout)
        except queue.Empty:
            self._maybe_replay_if_idle()
            return False
        if packet is None:  # 哨兵：停止
            return False
        self._emit_received(packet)
        return True

    def _consume_loop(self):
        """消费者线程：循环消费队列；入队即时缓冲，周期检查空闲超时回放"""
        while not self._consumer_stop.is_set():
            self._consume_once(timeout=0.5)

        # 退出前冲刷剩余缓冲
        if self._buffer:
            self.logger.info(f"消费者线程结束，冲刷剩余 {len(self._buffer)} 个消息包")
            self._replay_messages(self._buffer)
            self._buffer = []

    def _rewrite_packet(self, packet: ParsedPacket) -> bytes:
        """重写头部字段后重新序列化整包（帧区与 CRC 保持不变）

        回放的包是新的一股语音流：stream_begin_utc 应更新为回放时刻，
        而非保留原发送者的值（否则回声冒充旧流，污染下游判定）。
        """
        header = packet.header
        new_callsign = f"{self.callsign_prefix}{header.callsign}"
        now_ms = int(time.time() * 1000) & 0xFFFFFFFF
        new_header = header.copy_with(
            vendor=self.replay_vendor,
            callsign=new_callsign,
            stream_begin_utc=now_ms,
            timestamp=now_ms,
        )
        if self.replay_uid:
            new_header.uid = self.replay_uid
        return new_header.to_bytes() + b"".join(
            f.to_bytes() for f in packet.frames
        )

    def _replay_messages(self, batch):
        """按原始接收时间轴重放缓存消息（绝对截止时间，防累积漂移）"""
        if not batch:
            return
        publish_topic = self.config['topics']['publish']
        first = batch[0][0].header
        self.event_log.log(
            "replay_started",
            uid=first.uid,
            callsign=first.callsign,
            packets=len(batch),
        )

        success = failed = 0
        started_at = time.monotonic()
        receive_times = [t for _, t in batch]

        for i, (packet, _) in enumerate(batch):
            try:
                if i > 0:
                    target_elapsed = receive_times[i] - receive_times[0]
                    delay = started_at + target_elapsed - time.monotonic()
                    if delay > 0:
                        time.sleep(delay)

                modified = self._rewrite_packet(packet)
                result = self.mqtt_client.publish(publish_topic, modified)
                if result.rc == mqtt_client.MQTT_ERR_SUCCESS:
                    success += 1
                    self.logger.debug(
                        f"重放 [{i + 1}/{len(batch)}] - UID={packet.header.uid}, "
                        f"呼号='{packet.header.callsign}' -> "
                        f"'{self.callsign_prefix}{packet.header.callsign}'"
                    )
                else:
                    failed += 1
                    self.logger.warning(
                        f"发布消息 [{i + 1}] 失败，返回码: {result.rc}"
                    )
            except Exception as e:
                failed += 1
                self.logger.error(f"重放消息 [{i + 1}] 时出错: {e}", exc_info=True)

        duration = time.monotonic() - started_at
        self.event_log.log(
            "replay_finished",
            uid=first.uid,
            callsign=first.callsign,
            packets=len(batch),
            ok=success,
            failed=failed,
            duration_s=round(duration, 3),
        )
        self.event_log.log(
            "stream_end",
            uid=first.uid,
            callsign=first.callsign,
            packets=len(batch),
            duration_s=round(duration, 3),
        )
        self.logger.info(
            f"重放完成 - 成功: {success}, 失败: {failed}, 总计: {len(batch)}"
        )

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    def _signal_handler(self, signum, frame):
        self.logger.info(f"接收到信号 {signum}，准备关闭服务...")
        self.running = False
        self._shutdown_requested = True

    def run(self):
        """启动消费者线程（MQTT 网络线程由 connect 的 loop_start 管理）"""
        if self._shutdown_requested:
            # run() 之前已收到停止信号（信号处理与 run 启动存在竞态）
            self.stop()
            return
        self.running = True
        self.logger.info("FMO Echo 服务已启动")
        self._consumer_stop.clear()
        self._consumer_thread = threading.Thread(
            target=self._consume_loop, name="fmo-consumer", daemon=True
        )
        self._consumer_thread.start()
        try:
            while self.running:
                time.sleep(self.check_interval)
        except KeyboardInterrupt:
            self.logger.info("接收到键盘中断")
        except Exception as e:
            self.logger.error(f"服务运行时出错: {e}", exc_info=True)
        finally:
            self.stop()

    def stop(self):
        """优雅停止：断开 MQTT、记录状态（幂等）"""
        if self._stopped:
            return
        self._stopped = True
        self.running = False
        self._shutdown_requested = True
        self.logger.info("正在停止 FMO Echo 服务...")
        # 停止消费者线程（while 循环每 0.5s 检查 stop 标志，最多等 0.5s 退出）
        self._consumer_stop.set()
        if self._consumer_thread is not None and self._consumer_thread.is_alive():
            self._consumer_thread.join(timeout=5.0)
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()
        self.event_log.log("service_stopped", reason="signal")
        self.logger.info("FMO Echo 服务已停止")
