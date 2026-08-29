"""FMO Repeater 主包

基于 FMO 语音数据开放协议 v1（https://bg5esn.com/docs/fmo-voice-codec-spec/）
的 MQTT 中继器管理与工具服务。

分层：
- protocol : 协议层（消息包/传输帧/编码语音帧，纯数据结构）
- codecs   : 编解码层（RADPCM / OPUS）
- service  : 服务层（配置/Echo/日志/守护进程）
"""

__version__ = "2.0.0"
