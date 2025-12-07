"""
FMO 数据包头部处理模块

本模块提供 FMO（FM Over Internet）数据包头部的解析、序列化和修改功能。
FMO 数据包由 22 字节固定头部和可变长度载荷组成。
"""

import struct


class FMORawHeader:
    """
    FMO 原始数据包头部类

    表示 22 字节的 FMO 数据包头部结构，包含以下字段：
    - VERSION (4 字节): 协议版本号 (uint32)
    - PADDING1 (2 字节): 保留填充字段 (uint16)
    - UID (2 字节): 设备/用户唯一标识符 (uint16)
    - PADDING2 (2 字节): 保留填充字段 (uint16)
    - CALLSIGN (12 字节): 无线电呼号，UTF-8 编码，空字节填充

    所有字段使用小端字节序（little-endian）。
    """

    # 字段大小常量（单位：字节）
    VERSION_SIZE = 4
    UID_SIZE = 2
    CALLSIGN_SIZE = 12
    PADDING_SIZE_1 = 2
    PADDING_SIZE_2 = 2

    # 头部总大小：22 字节
    HEADER_SIZE = VERSION_SIZE + PADDING_SIZE_1 + UID_SIZE + PADDING_SIZE_2 + CALLSIGN_SIZE

    def __init__(self, version: int, uid: int, callsign: str, padding1: int = 0, padding2: int = 0):
        """
        初始化 FMO 头部对象

        Args:
            version: 协议版本号
            uid: 用户/设备唯一标识符
            callsign: 无线电呼号（最多 12 字节 UTF-8 编码）
            padding1: 第一个填充字段（默认为 0）
            padding2: 第二个填充字段（默认为 0）
        """
        self.version = version
        self.uid = uid
        self.callsign = callsign
        self.padding1 = padding1
        self.padding2 = padding2

    @classmethod
    def from_bytes(cls, data: bytes) -> 'FMORawHeader':
        """
        从字节流反序列化 FMO 头部

        Args:
            data: 原始字节流，至少包含 22 字节头部数据

        Returns:
            FMORawHeader: 解析后的头部对象

        Raises:
            ValueError: 如果数据长度不足 22 字节
        """
        if len(data) < cls.HEADER_SIZE:
            raise ValueError(
                f"数据长度不足: 期望至少 {cls.HEADER_SIZE} 字节，实际得到 {len(data)} 字节"
            )

        # 使用 struct 解包：小端序 (<)，uint32 + 3×uint16 + 12字节字符串
        version, padding1, uid, padding2, callsign_bytes = struct.unpack(
            "<IHHH12s",
            data[:cls.HEADER_SIZE]
        )

        # 移除尾部空字节并解码为 UTF-8 字符串，错误时使用替换字符
        callsign = callsign_bytes.rstrip(b'\x00').decode('utf-8', errors='replace')

        return cls(version, uid, callsign, padding1, padding2)

    def to_bytes(self) -> bytes:
        """
        将头部对象序列化为 22 字节

        Returns:
            bytes: 序列化后的 22 字节数据
        """
        # 编码呼号为 UTF-8 并截断至 12 字节
        callsign_encoded = self.callsign.encode('utf-8')[:self.CALLSIGN_SIZE]

        # 不足 12 字节时右侧填充空字节
        callsign_padded = callsign_encoded.ljust(self.CALLSIGN_SIZE, b'\x00')

        # 使用 struct 打包为二进制数据
        return struct.pack(
            "<IHHH12s",
            self.version,
            self.padding1,
            self.uid,
            self.padding2,
            callsign_padded
        )

    def __repr__(self) -> str:
        """返回头部对象的字符串表示，便于调试"""
        return (
            f"FMORawHeader(version={self.version}, "
            f"padding1=0x{self.padding1:04X}, "
            f"uid={self.uid}, "
            f"padding2=0x{self.padding2:04X}, "
            f"callsign='{self.callsign}')"
        )


def replace_header_in_stream(stream: bytes, **updates) -> bytes:
    """
    从完整数据流中解析头部，修改指定字段，重新序列化头部，保留载荷不变

    这是实现 Echo 服务的核心函数：能够拦截 FMO 数据包，修改头部信息（如 UID 和呼号），
    同时保留原始的无线电数据载荷。

    Args:
        stream: 原始完整字节流（至少包含 22 字节头部）
        **updates: 要修改的字段，支持的字段名：
            - version: 协议版本号
            - uid: 用户/设备标识符
            - callsign: 无线电呼号
            - padding1: 填充字段1
            - padding2: 填充字段2

    Returns:
        bytes: 新的字节流（修改后的头部 + 原始载荷）

    Raises:
        AttributeError: 如果指定的字段名不存在

    Example:
        >>> original_packet = b'...'  # 22字节头部 + 载荷
        >>> # 修改 UID 和呼号
        >>> modified_packet = replace_header_in_stream(
        ...     original_packet,
        ...     uid=65535,
        ...     callsign="RE>BD8BOJ"
        ... )
    """
    # 解析原始头部
    header = FMORawHeader.from_bytes(stream)

    # 更新指定的字段
    for key, value in updates.items():
        if not hasattr(header, key):
            raise AttributeError(f"FMORawHeader 没有属性 '{key}'")
        setattr(header, key, value)

    # 重新序列化头部
    new_header = header.to_bytes()

    # 提取原始载荷（跳过前 22 字节的头部）
    payload = stream[FMORawHeader.HEADER_SIZE:]

    # 拼接新头部和原始载荷
    return new_header + payload
