# 修复：echo.uid 保持原值导致客户端自过滤（附属于变更 003）

> status: implemented（随变更 003 一并落地）
> 背景：真实设备测试收不到 Echo 回声语音

## 1. 现象与根因

- 现象：集成测试（探针不区分 uid）全部通过，但真实 FMO 设备收不到回声音频
- 根因：客户端（设备端）按消息头 UID 做**自回声抑制**——收到的包 UID 与本机
  UID 相同则视为"自己发的"直接丢弃。变更 001 将 `echo.uid` 默认值设为 0
  （保持原发送者 UID），重放包因此被设备端的 UID 自过滤拦截
- 对照旧设计：旧实现重写 UID=65535（固定 Echo UID），设备端 UID 不同 → 正常播放。
  重构时误判"保持原值更安全（避免冒用他人 ID）"，忽略了客户端过滤行为

## 2. 决策（D8）

- `echo.uid` 默认值 **0 → 65535**：与旧设计对齐，重放包 UID 固定为 65535
- 65535 在协议 v1 下并非保留值，但作为软件 Echo 的重放 UID 无冲突风险：
  - 若某真实用户恰好持有 UID 65535，设备端会同时收到其语音与回声——可配置
    改为其他值规避，故保留 `echo.uid` 为配置项
  - 防循环不依赖该值（vendor+前缀双条件，D1），且 `uid != 0` 时 UID 匹配
    也参与防循环判定（echo.py `_is_own_replay` 已实现）
- 用户配置文件 `config.yaml` 同步改为 65535

## 3. 变更清单

- `docs/design/service.md`：echo.uid 语义说明（0=保持原值[致自过滤，不推荐]，
  65535=默认重放 UID）
- `fmo_repeater/service/config.py`：DEFAULT_CONFIG echo.uid 0 → 65535
- `config.yaml.example` / `config.yaml`：注释与值更新
- `tests/test_config.py`：默认值断言更新
- `tests/test_echo_service.py`：头重写用例断言 UID=65535（默认）
