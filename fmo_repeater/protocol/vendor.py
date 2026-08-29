"""厂家标识（vendor）区间定义与校验

依据规范 §2.3：
- 0x0000 ~ 0x0FFF  保留区（FMO 项目方），他人不得使用
- 0x1000 ~ 0x1FFF  实验区，自由取用，不登记、不审核
- 0x2000 ~ 0x2FFF  软件区，自由取用，不登记、不审核
- 0x3000 ~ 0xFFFFFFFF 正式区，经 fmo-raw-vendor-id 仓库 PR 登记后使用
"""

VENDOR_RESERVED_MIN = 0x0000
VENDOR_RESERVED_MAX = 0x0FFF
VENDOR_EXPERIMENTAL_MIN = 0x1000
VENDOR_EXPERIMENTAL_MAX = 0x1FFF
VENDOR_SOFTWARE_MIN = 0x2000
VENDOR_SOFTWARE_MAX = 0x2FFF
VENDOR_FORMAL_MIN = 0x3000
VENDOR_FORMAL_MAX = 0xFFFFFFFF

#: FMO 项目方自身标识
VENDOR_FMO = 0x0000

#: 本项目（软件实现）默认使用软件区起始值，可通过配置覆盖
VENDOR_DEFAULT = 0x2000

_ZONE_RESERVED = "reserved"
_ZONE_EXPERIMENTAL = "experimental"
_ZONE_SOFTWARE = "software"
_ZONE_FORMAL = "formal"


def vendor_zone(vendor: int) -> str:
    """返回 vendor 所在区间名称

    Args:
        vendor: 厂家标识

    Returns:
        str: 'reserved' / 'experimental' / 'software' / 'formal' / 'invalid'

    Raises:
        ValueError: vendor 超出 uint32 范围
    """
    if not 0 <= vendor <= VENDOR_FORMAL_MAX:
        raise ValueError(f"vendor 超出 uint32 范围: {vendor:#x}")
    if vendor <= VENDOR_RESERVED_MAX:
        return _ZONE_RESERVED
    if vendor <= VENDOR_EXPERIMENTAL_MAX:
        return _ZONE_EXPERIMENTAL
    if vendor <= VENDOR_SOFTWARE_MAX:
        return _ZONE_SOFTWARE
    return _ZONE_FORMAL


def is_vendor_valid(vendor: int) -> bool:
    """校验 vendor 是否可合法使用（用于本服务发包/重写）

    保留区（0x0000~0x0FFF）为 FMO 项目方专用，严禁使用。
    实验区/软件区自由取用；正式区依赖外部登记（此处仅做区间判定，
    无法校验登记状态，返回 True 但调用方应自行确认）。
    """
    return vendor_zone(vendor) != _ZONE_RESERVED
