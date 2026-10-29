---
name: agi-like
description: 面向通用任务的 AGI-like 自主交付控制层：按事实判断边界和能力，编排执行、独立验收、恢复失败并受控积累新技能；简单问答与普通闲聊不启动流程。
metadata:
  version: "1.4.0"
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

先选择一种执行模式，避免同一目标重复开工：受管任务优先由宿主从用户请求确定工作区和允许修改路径，复用已有可信验收，或让独立上下文从完整原始目标与资料准备验收，再调用 `scripts/agent_task.py`。它接收自然语言目标、自动规划并生成合同，驱动同一任务；用户无需手写合同或创建面板任务。规划不授予权限，也不能替代独立业务验收；缺必要业务资料时保留未知，缺可信验收时保持 `NEEDS_VERIFIER`。

已有合同可直接调用 `scripts/agent_run.py`。当前 Codex 会话自行执行时使用宿主原生工具；`scripts/bridge.py` 只可登记会话和目标，不代表已订阅内部事件。不要为受管任务再用 bridge 创建第二笔记录。执行、暂停与续接见独立包根目录 `AGENT_RUNTIME.md`，源码中的同一文件为 `portable-agent-kit/AGENT_RUNTIME.md`。

受管任务用同一 `--control-root` 执行 `agent_task.py status/resume/pause/cancel/reconcile`；停止请求只有在执行器确认安全边界后才算生效，未知结果先读回，不重新开一笔任务重做。技能提炼、真实 holdout/内容变异回归、激活及撤回用 `scripts/skill_manage.py`；失败归档不等于学会，历史经验不授予当前权限。

`agent_runtime/` 提供模型、工具、项目记忆和技能循环，控制平面保留状态与独立证据，`scripts/agent_queue.py` 提供有界批次。桥接失败时标记 `UNREGISTERED`；没有实际事件回写时不声称控制平面已接管。宿主必须实际加载并验证本 Skill；这些工具不增加账号、权限或自动后台运行。
