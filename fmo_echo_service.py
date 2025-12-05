"""
FMO Echo 服务核心模块

实现完整的 FMO Echo 服务，包括：
- MQTT 连接和消息订阅
- 消息缓存和超时检测
- 头部重写和消息重放
- 日志记录和错误处理
"""

import random
import time
import threading
import logging
from logging.handlers import RotatingFileHandler
import os
from typing import Dict, List, Any
import signal
import sys

from paho.mqtt import client as mqtt_client
import paho.mqtt.enums

from fmo_header import FMORawHeader, replace_header_in_stream


class FMOEchoService:
    """
    FMO Echo 服务主类

    功能：
    1. 订阅 MQTT 主题接收 FMO 消息
    2. 缓存连续接收到的消息
    3. 检测超时（无新消息）
    4. 重写头部并重放所有缓存的消息
    """

    def __init__(self, config: Dict[str, Any]):
        """
        初始化 FMO Echo 服务

        Args:
            config: 配置字典
        """
        self.config = config
        self.logger = self._setup_logging()

        # 消息缓存
        self.message_buffer: List[bytes] = []
        self.last_message_time = None
        self.buffer_lock = threading.Lock()  # 线程安全锁

        # MQTT 客户端
        self.mqtt_client = None
        self.connected = False

        # 运行状态
        self.running = False
        self.check_interval = 0.1  # 主循环检查间隔（秒）

        # 注册信号处理器以优雅关闭
        signal.signal(signal.SIGINT, self._signal_handler)
        signal.signal(signal.SIGTERM, self._signal_handler)

        self.logger.info("FMO Echo 服务已初始化")
        self.logger.info(f"超时设置: {self.config['echo']['timeout']} 秒")
        self.logger.info(f"订阅主题: {self.config['topics']['subscribe']}")
        self.logger.info(f"发布主题: {self.config['topics']['publish']}")

    def _setup_logging(self) -> logging.Logger:
        """
        配置日志系统

        Returns:
            logging.Logger: 配置好的日志记录器
        """
        logger = logging.getLogger('FMOEcho')
        logger.setLevel(getattr(logging, self.config['logging']['level']))

        # 清除已有的处理器
        logger.handlers.clear()

        # 格式化器
        formatter = logging.Formatter(
            '%(asctime)s - %(name)s - %(levelname)s - %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )

        # 控制台处理器
        if self.config['logging']['console']:
            console_handler = logging.StreamHandler()
            console_handler.setFormatter(formatter)
            logger.addHandler(console_handler)

        # 文件处理器
        log_file = self.config['logging']['file']
        if log_file:
            # 确保日志目录存在
            log_dir = os.path.dirname(log_file)
            if log_dir and not os.path.exists(log_dir):
                os.makedirs(log_dir, exist_ok=True)

            file_handler = RotatingFileHandler(
                log_file,
                maxBytes=self.config['logging']['max_bytes'],
                backupCount=self.config['logging']['backup_count'],
                encoding='utf-8'
            )
            file_handler.setFormatter(formatter)
            logger.addHandler(file_handler)

        return logger

    def _signal_handler(self, signum, frame):
        """
        信号处理器，用于优雅关闭服务

        Args:
            signum: 信号编号
            frame: 当前栈帧
        """
        self.logger.info(f"接收到信号 {signum}，准备关闭服务...")
        self.stop()

    def _on_connect(self, client, userdata, flags, reason_code, properties):
        """
        MQTT 连接回调

        Args:
            client: MQTT 客户端实例
            userdata: 用户数据
            flags: 连接标志
            reason_code: 连接结果代码
            properties: 连接属性
        """
        if reason_code == 0:
            self.connected = True
            self.logger.info(f"已连接到 MQTT 代理: {self.config['mqtt']['broker']}:{self.config['mqtt']['port']}")

            # 订阅主题
            subscribe_topic = self.config['topics']['subscribe']
            result = client.subscribe(subscribe_topic)
            self.logger.info(f"已订阅主题: {subscribe_topic}, 结果: {result}")
        else:
            self.connected = False
            self.logger.error(f"连接 MQTT 代理失败，返回码: {reason_code}")

    def _on_disconnect(self, client, userdata, disconnect_flags, reason_code, properties):
        """
        MQTT 断开连接回调

        Args:
            client: MQTT 客户端实例
            userdata: 用户数据
            disconnect_flags: 断开连接标志
            reason_code: 断开原因代码
            properties: 断开连接属性
        """
        self.connected = False
        if reason_code == 0:
            self.logger.info("已主动断开 MQTT 连接")
        else:
            self.logger.warning(f"MQTT 连接断开，原因码: {reason_code}")

    def _on_message(self, client, userdata, msg):
        """
        MQTT 消息接收回调

        接收到消息时：
        1. 解析消息头部获取信息
        2. 检查 UID，忽略自己重放的消息（避免循环）
        3. 将消息添加到缓存
        4. 重置超时计时器

        Args:
            client: MQTT 客户端实例
            userdata: 用户数据
            msg: 接收到的消息
        """
        try:
            # 解析头部以获取呼号和 UID
            header = FMORawHeader.from_bytes(msg.payload)

            # 检查 UID，忽略自己重放的消息（避免重放循环）
            if header.uid == self.config['echo']['uid']:
                self.logger.debug(
                    f"忽略自己重放的消息 - UID={header.uid}, 呼号='{header.callsign}'"
                )
                return

            # 线程安全地添加消息到缓存
            with self.buffer_lock:
                self.message_buffer.append(msg.payload)
                self.last_message_time = time.time()

            self.logger.debug(
                f"接收到消息 [缓存大小: {len(self.message_buffer)}] - "
                f"UID={header.uid}, 呼号='{header.callsign}', "
                f"载荷大小={len(msg.payload)} 字节"
            )

        except Exception as e:
            self.logger.error(f"处理接收消息时出错: {e}", exc_info=True)

    def connect(self):
        """
        连接到 MQTT 代理并订阅主题
        """
        # 生成客户端 ID
        client_id = f"{self.config['mqtt']['client_id_prefix']}_{random.randint(0, 10000)}"

        # 创建 MQTT 客户端
        self.mqtt_client = mqtt_client.Client(
            paho.mqtt.enums.CallbackAPIVersion.VERSION2,
            client_id
        )

        # 设置回调
        self.mqtt_client.on_connect = self._on_connect
        self.mqtt_client.on_disconnect = self._on_disconnect
        self.mqtt_client.on_message = self._on_message

        # 设置用户名和密码
        if self.config['mqtt']['username']:
            self.mqtt_client.username_pw_set(
                self.config['mqtt']['username'],
                self.config['mqtt']['password']
            )

        # 连接到代理
        self.logger.info(
            f"正在连接到 MQTT 代理: {self.config['mqtt']['broker']}:{self.config['mqtt']['port']}"
        )

        try:
            self.mqtt_client.connect(
                self.config['mqtt']['broker'],
                self.config['mqtt']['port'],
                self.config['mqtt']['keepalive']
            )

            # 启动网络循环线程
            self.mqtt_client.loop_start()

        except Exception as e:
            self.logger.error(f"连接 MQTT 代理失败: {e}", exc_info=True)
            raise

    def _check_timeout(self):
        """
        检查是否超时

        如果自上次消息后超过配置的超时时间，且缓存中有消息，则触发重放。
        """
        with self.buffer_lock:
            # 如果从未收到消息，直接返回
            if self.last_message_time is None:
                return

            # 检查是否超时
            elapsed = time.time() - self.last_message_time
            if elapsed > self.config['echo']['timeout']:
                # 只有在缓存不为空时才重放
                if self.message_buffer:
                    self.logger.info(
                        f"检测到超时（{elapsed:.2f}秒），开始重放 {len(self.message_buffer)} 个消息"
                    )
                    self._replay_messages()

                # 重置状态
                self.message_buffer = []
                self.last_message_time = None

    def _replay_messages(self):
        """
        重放缓存中的所有消息

        对每个消息：
        1. 解析原始头部获取呼号
        2. 修改头部（UID 和呼号）
        3. 发布到目标主题
        """
        success_count = 0
        fail_count = 0
        publish_topic = self.config['topics']['publish']

        for i, msg_data in enumerate(self.message_buffer, 1):
            try:
                # 解析原始头部
                original_header = FMORawHeader.from_bytes(msg_data)

                # 构造新的呼号（添加前缀）
                new_callsign = f"{self.config['echo']['callsign_prefix']}{original_header.callsign}"

                # 修改头部
                modified_msg = replace_header_in_stream(
                    msg_data,
                    uid=self.config['echo']['uid'],
                    callsign=new_callsign
                )

                # 发布消息
                result = self.mqtt_client.publish(publish_topic, modified_msg)

                if result.rc == mqtt_client.MQTT_ERR_SUCCESS:
                    success_count += 1
                    self.logger.debug(
                        f"重放消息 [{i}/{len(self.message_buffer)}] - "
                        f"原始: UID={original_header.uid}, 呼号='{original_header.callsign}' -> "
                        f"修改后: UID={self.config['echo']['uid']}, 呼号='{new_callsign}'"
                    )
                else:
                    fail_count += 1
                    self.logger.warning(f"发布消息 [{i}] 失败，返回码: {result.rc}")

            except Exception as e:
                fail_count += 1
                self.logger.error(f"重放消息 [{i}] 时出错: {e}", exc_info=True)

        self.logger.info(
            f"重放完成 - 成功: {success_count}, 失败: {fail_count}, 总计: {len(self.message_buffer)}"
        )

    def run(self):
        """
        运行服务主循环

        主循环持续：
        1. 检查超时
        2. 等待一小段时间
        3. 重复直到服务停止
        """
        self.running = True
        self.logger.info("FMO Echo 服务已启动")

        try:
            while self.running:
                # 检查超时
                self._check_timeout()

                # 短暂休眠以避免 CPU 过度占用
                time.sleep(self.check_interval)

        except KeyboardInterrupt:
            self.logger.info("接收到键盘中断")
        except Exception as e:
            self.logger.error(f"服务运行时出错: {e}", exc_info=True)
        finally:
            self.stop()

    def stop(self):
        """
        停止服务

        执行清理工作：
        1. 停止主循环
        2. 断开 MQTT 连接
        3. 记录最终状态
        """
        if not self.running:
            return

        self.running = False
        self.logger.info("正在停止 FMO Echo 服务...")

        # 断开 MQTT
        if self.mqtt_client:
            self.mqtt_client.loop_stop()
            self.mqtt_client.disconnect()

        # 记录最终缓存状态
        with self.buffer_lock:
            if self.message_buffer:
                self.logger.info(f"服务停止时缓存中还有 {len(self.message_buffer)} 个未重放的消息")

        self.logger.info("FMO Echo 服务已停止")


def main():
    """
    主函数：加载配置并运行服务
    """
    from config import load_config, validate_config

    # 加载配置
    try:
        config = load_config('config.yaml')
        validate_config(config)
    except Exception as e:
        print(f"配置错误: {e}")
        sys.exit(1)

    # 创建并启动服务
    service = FMOEchoService(config)

    try:
        # 连接 MQTT
        service.connect()

        # 等待连接建立
        timeout = 10
        start_time = time.time()
        while not service.connected and time.time() - start_time < timeout:
            time.sleep(0.1)

        if not service.connected:
            service.logger.error("连接 MQTT 超时")
            sys.exit(1)

        # 运行服务
        service.run()

    except Exception as e:
        print(f"启动服务失败: {e}")
        sys.exit(1)


if __name__ == '__main__':
    main()
