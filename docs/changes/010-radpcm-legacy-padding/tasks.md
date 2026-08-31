# 任务清单：RADPCM 旧格式填充字节兼容修复（变更 010）

## T1 实现与测试

- [x] T1.1 `parse_frame` 对旧格式仅检查 `adpcmBytes` 低字节 64。
- [x] T1.2 增加任意高字节、现场 `0xE440` 解码和非法低字节测试。
- [x] T1.3 运行默认离线 `./run_tests.sh`，不执行 MQTT integration。

## T2 文档与提交

- [x] T2.1 合并规则到 codecs/testing 存量设计。
- [x] T2.2 proposal/design 标记 merged，任务全部完成。
- [x] T2.3 创建 GPG 签名提交。
