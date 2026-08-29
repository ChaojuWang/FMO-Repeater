"""协议层：FMO 语音数据开放协议 v1 数据结构"""

from .common import MTU, AGGREGATION_MS, ProtocolError
from .vendor import (
    VENDOR_DEFAULT, VENDOR_FMO,
    VENDOR_RESERVED_MIN, VENDOR_RESERVED_MAX,
    VENDOR_EXPERIMENTAL_MIN, VENDOR_EXPERIMENTAL_MAX,
    VENDOR_SOFTWARE_MIN, VENDOR_SOFTWARE_MAX,
    VENDOR_FORMAL_MIN, VENDOR_FORMAL_MAX,
    vendor_zone, is_vendor_valid,
)
from .header import HEADER_SIZE, VERSION, MessageHeader
from .frame import (
    COMPRESS_PCM, COMPRESS_OPUS, COMPRESS_RADPCM,
    EncodedVoiceFrame, TransportFrame, frame_duration_ms,
)
from .packet import ParsedPacket, PacketParser, PacketBuilder, frame_region_checksum

__all__ = [
    "MTU", "AGGREGATION_MS", "ProtocolError",
    "VENDOR_DEFAULT", "VENDOR_FMO",
    "VENDOR_RESERVED_MIN", "VENDOR_RESERVED_MAX",
    "VENDOR_EXPERIMENTAL_MIN", "VENDOR_EXPERIMENTAL_MAX",
    "VENDOR_SOFTWARE_MIN", "VENDOR_SOFTWARE_MAX",
    "VENDOR_FORMAL_MIN", "VENDOR_FORMAL_MAX",
    "vendor_zone", "is_vendor_valid",
    "HEADER_SIZE", "VERSION", "MessageHeader",
    "COMPRESS_PCM", "COMPRESS_OPUS", "COMPRESS_RADPCM",
    "EncodedVoiceFrame", "TransportFrame", "frame_duration_ms",
    "ParsedPacket", "PacketParser", "PacketBuilder",
    "frame_region_checksum",
]
