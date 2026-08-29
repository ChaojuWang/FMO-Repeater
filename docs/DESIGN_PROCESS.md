# SDD 设计流程规范（Spec-Driven Development）

本仓库采用"文档先行"（Design-First / SDD）流程：**任何需求先写设计文档，再实现代码**。

## 1. 目录结构

```
docs/
├── DESIGN_PROCESS.md          # 本文件：流程规范
├── design/                    # 存量设计文档（始终反映当前系统现状）
│   ├── legacy/                # 已退役系统的设计归档（只读，不再修改）
│   ├── architecture.md        # 总体架构
│   ├── protocol.md            # 协议层设计
│   ├── codecs.md              # 编解码层设计
│   ├── service.md             # 服务层设计
│   ├── logging.md             # 日志系统设计
│   └── testing.md             # 测试体系设计
└── changes/                   # 增量设计（进行中的迭代）
    ├── 001-protocol-refactor/ # 迭代一：协议重构
    │   ├── proposal.md        # 提案：动机/目标/范围
    │   ├── design.md          # 详细设计
    │   └── tasks.md           # 任务清单与验收标准
    └── 002-voice-recording/   # 迭代二：录音功能
```

## 2. 流程

1. **提案**：新需求先在 `docs/changes/NNN-<slug>/` 创建 `proposal.md`，说明动机、目标、范围与非目标。
2. **设计**：编写 `design.md`，包含模块划分、接口、数据结构、关键决策（含备选方案与取舍理由）。
3. **任务**：将设计拆解为 `tasks.md`，每项任务带验收标准。
4. **实现**：按 `tasks.md` 实现代码与测试。实现中发现设计缺陷时，**先更新 design.md，再改代码**。
5. **合并**：迭代完成后，将 `changes/NNN/` 的设计内容合并入 `docs/design/` 对应文档（新增章节或整文档成型），并在文档头部标注 `> Merged from changes/NNN`；`changes/NNN/proposal.md` 头部标注 `status: merged`。
6. **归档**：`docs/design/legacy/` 仅存放已退役系统设计，只读。

## 3. 规则

- **文档先行**：先改 `docs/changes/` 增量设计，再实现需求；禁止"先写代码后补文档"。
- **单一事实源**：`docs/design/` 始终与代码保持一致；`changes/` 只保留增量过程记录。
- **决策留痕**：所有关键技术决策（含被否决的备选方案）必须记录在设计文档中。
- **编号递增**：`changes/NNN` 编号单调递增，不复用。
