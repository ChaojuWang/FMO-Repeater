# 任务清单：语音录制功能（变更 002）

> 状态标记：`[ ]` 待办 ｜ `[x]` 完成 ｜ `[~]` 部分完成
> 设计：design.md（status: designed，待实施）

## T1 配置
- [ ] T1.1 `config.py` 新增 `recording` 节（enabled 默认 false / directory 默认 recordings）
  - 验收：默认配置不含 recording 时 load_config 不报错（默认关闭）；validate 校验 enabled bool、enabled=true 时 directory 非空
- [ ] T1.2 `config.yaml.example` 增补 recording 节注释
  - 验收：模板含 recording 节且 enabled: false

## T2 Recorder 实现
- [ ] T2.1 `fmo_repeater/service/recorder.py` Recorder 类骨架（开关 no-op 模式、锁、输出目录创建）
  - 验收：enabled=false 时 feed/finalize no-op、无目录创建
- [ ] T2.2 事件消费（§4）：每个 TransmissionCompleted 独立录制；codec 变化 segment 收口
  - 验收：两个事件两组文件；单事件混合 codec 两 segment 两文件
- [ ] T2.3 WAV 落盘（RADPCM/OPUS）：wave 标准库 8kHz/16bit/mono；duration_ms 统计
  - 验收：`wave` 读回参数与样本数正确；样本数=640×帧数（RADPCM）/320×帧数（OPUS）
- [ ] T2.4 OPUS 降级 `.opusraw`（含 8B 编码帧头）与事件 degraded: true
  - 鬿收：opus_is_available()=False 时产出 .opusraw 且字节流=编码帧序列
- [ ] T2.5 文件命名与 callsign 清洗（§5）
  - 验收：`{YYYYMMDD-HHmmss}-{uid}-{seq:03d}-{codec}` 格式；同(callsign,秒)seq 递增；`..`/空格/空呼号清洗安全
- [ ] T2.6 错误处理（§9）：单帧解码异常跳帧计数；写盘失败事件+WARNING 不中断服务
  - 验收：注入坏帧后整流仍落盘且 recording_saved.frames 为成功帧数

## T3 RepeaterService 集成
- [ ] T3.1 注册 Recorder 为独立 TransmissionConsumer
  - 验收：recording.enabled=false 不订阅；开启后 mock MQTT 全流程文件生成

## T4 事件日志
- [ ] T4.1 新事件 recording_stream_start / recording_saved / recording_discarded
  - 验收：JSONL 行字段完整（file/frames/duration_ms/bytes/degraded）

## T5 测试
- [ ] T5.1 `tests/test_recorder.py`（design.md §10 全项）
  - 验收：./run_tests.sh 全绿；OPUS 用例缺依赖自动 skip
- [ ] T5.2 `tests/test_config.py` 增补 recording 节用例
  - 验收：默认值/校验规则通过

## T6 文档
- [ ] T6.1 `docs/design/logging.md` 事件表增补三个新事件
- [ ] T6.2 `docs/design/service.md` 增补 Recorder 章节
- [ ] T6.3 `README.md` / `CLAUDE.md` 更新（recording 配置说明）
- [ ] T6.4 迭代完成：changes/002 合并入 docs/design/ 并标记 status: merged
