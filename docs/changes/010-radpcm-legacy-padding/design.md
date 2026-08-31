# 详细设计：RADPCM 旧格式填充字节兼容修复

> status: merged
> 变更编号：010

## 1. 根因

RADPCM 头固定 8B。早期实现将偏移 6 的 `adpcmBytes` 写为 8-bit，合法线值因
320 截断为 64；偏移 7 是未初始化填充，因此可为任意字节。当前 `parse_frame`
把偏移 6..7 读为 uint16 后，错误要求高字节为 0，导致 `0xE440` 等现场帧失败。

Echo 不解码编码载荷，所以原样回放正常；Recorder 调用 `RadpcmDecoder` 后才触发
此错误。消息包长度 408B 和 CRC 均正常也证明外层帧没有错位。

## 2. 解析规则

保持读取、长度和数据偏移不变：

```python
if adpcm_raw == 320:
    legacy = False
elif adpcm_low == 64:
    legacy = True
else:
    raise ProtocolError("bad_length", ...)
```

- 新格式必须精确等于 uint16 320，并优先识别；旧格式填充恰好为 `0x01` 时
  与新格式线值相同，按新格式标记，但二者帧布局和解码结果一致。
- 其他低字节为 64 的值按旧格式识别，高字节作为未初始化填充忽略，包括 0。
- `data` 仍取 `frame[8:328]`；不会把填充字节当音频，也不改变固定 328B 帧长。
- 低字节不是 64 的其他值仍拒绝。兼容分支接受所有 `0x??40` 是官方旧格式要求。

## 3. 测试与文档

- 参数化测试填充字节 `0x00/0x01/0xE4/0xFF`，均可正常解析；除与新格式
  `0x0140` 重合的 `0x01` 外均识别为 legacy。
- 明确覆盖现场值 `0xE440`，并验证完整 decoder 能输出 1280B PCM。
- 验证低字节非 64 的随机 uint16 仍返回 `bad_length`。
- 将规则合并到 `docs/design/codecs.md`，测试覆盖更新到 `docs/design/testing.md`。
