# 任务清单：语音录制与保留策略（变更 002）

> 状态标记：`[ ]` 待办 ｜ `[x]` 完成

## T1 配置与时间

- [x] T1.1 新增 recording 默认配置、容量/时长解析与完整校验。
- [x] T1.2 MQTT/TransmissionCompleted 传递首包 wall time。
- [x] T1.3 config.yaml.example 增补默认关闭、`512M`、`1w` 示例。

## T2 Recorder

- [x] T2.1 实现 disabled no-op、单 PTT 单 WAV、扁平毫秒命名和原子替换。
- [x] T2.2 实现 RADPCM/OPUS 解码、codec 变化 WARNING/事件及坏帧隔离。
- [x] T2.3 实现无 OPUS 时 `.opusraw`、异常混合时 `.fmoraw` 降级。
- [x] T2.4 实现启动及保存后按年龄/总容量 rotate 和清理事件。

## T3 集成与测试

- [x] T3.1 RepeaterService 仅在启用时构造、订阅 Recorder。
- [x] T3.2 新增 recorder、config、wall time、rotate 单元测试。
- [x] T3.3 增补离线组合测试并运行默认 `./run_tests.sh` 全绿。
- [x] T3.4 用户完成实际服务测试，确认基本功能正常。

## T4 文档合并

- [x] T4.1 更新 README、CLAUDE.md、配置示例。
- [x] T4.2 合并至 docs/design/architecture.md、service.md、logging.md、testing.md。
- [x] T4.3 集成验收后标记 merged 并合入 main。

## T5 审查修复

- [x] T5.1 正整数 `max_total_size` 按字节接受并补充回归测试。
- [x] T5.2 Recorder 增加在途取消，组合根在等待事件总线前调用 stop。
- [x] T5.3 补充取消顺序、解码中止和临时文件清理测试，运行默认离线测试。
