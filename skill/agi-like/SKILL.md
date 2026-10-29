---
name: agi-like
description: 面向通用任务的 AGI-like 自主交付控制层：按事实判断边界和能力，编排执行、独立验收、恢复失败并受控积累新技能；简单问答与普通闲聊不启动流程。
metadata:
  version: "1.1.0"
---

# AGI-like 自主交付控制层

只在任务有明确产物、需要多步推进、会改变行为/数据/界面，或用户要求 Agent 自己发现问题和持续改进时启用。总状态、分流、边界、证据、停止条件和下一步的唯一规范是 [`autonomy-core.md`](references/autonomy-core.md)；简单问答和可回退的一步小改直接完成并做必要读回。

主 Agent 对原始目标负责到底：完成已授权的计划、工具调用、子任务和排障后继续读回目标。技术故障先诊断恢复；只有必要业务决定、身份/权限/凭据、不可逆外部动作，或已证实没有安全恢复路径时才交接。`unknown` 保持未知，实际结果由真实入口和可信验收器确认。

按需读取专用引用：

- 排障与交接：[`engineering-practice.md`](references/engineering-practice.md)、[`blocker-recovery.md`](references/blocker-recovery.md)。
- 主动找问题与界面：[`delivery-self-review.md`](references/delivery-self-review.md)、[`frontend-quality.md`](references/frontend-quality.md)。
- 正式验收与比较：[`execution-gate.md`](references/execution-gate.md)、[`result-verification.md`](references/result-verification.md)、[`evaluation-contract.md`](references/evaluation-contract.md)。
- 宿主与本机系统：[`host-integration.md`](references/host-integration.md)、[`software-delivery.md`](references/software-delivery.md)、[`portable-workflow.md`](references/portable-workflow.md)。
- 质量合同与受控变强：[`quality-contracts.md`](references/quality-contracts.md)、[`self-improvement.md`](references/self-improvement.md)。

需要持久状态、租约、独立验收或升级候选时，使用导出包内的 `scripts/control_plane.py`、`scripts/independent_verify.py` 和 `scripts/self_improvement.py`。这些工具约束状态和证据，但不增加账号、权限、宿主工具、后台运行或定时器；其他宿主必须实际加载并验证本 Skill。
