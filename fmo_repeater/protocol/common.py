"""协议异常定义与公共常量

D5 决策（docs/changes/001-protocol-refactor/design.md §7）：
单一 ProtocolError 异常类，reason 属性携带细节，调用方只需 catch 一处。
"""

_MTU = 1400            # 消息包最大长度（字节）
_AGGREGATION_MS = 250  # 聚合时长上限（毫秒）


class ProtocolError(ValueError):
    """协议解析/构造错误

    Attributes:
        reason: 错误类别（如 'too_short' / 'bad_length' / 'bad_checksum'）
        detail: 人类可读细节
    """

    def __init__(self, reason: str, detail: str = ""):
        self.reason = reason
        self.detail = detail
        super().__init__(f"{reason}: {detail}" if detail else reason)


#: 消息包 MTU（规范 §3.3：超过 1400B 结束当前消息包）
MTU = _MTU
#: 聚合时长上限（规范 §3.3：聚合达到 250ms 结束当前消息包）
AGGREGATION_MS = _AGGREGATION_MS
