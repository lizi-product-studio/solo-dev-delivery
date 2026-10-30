---
name: agi-like
description: 面向通用任务的 AGI-like 自主交付控制层：按事实判断边界和能力，编排执行、独立验收、恢复失败并受控积累新技能；简单问答与普通闲聊不启动流程。
metadata:
  version: "1.9.2"
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

Codex 日常多步交付优先接入当前原生会话，保持宿主已有模型、工具和上下文：在修改前由 Agent 根据原始目标确定输入/产物范围，复用可信项目检查，或先从原始需求准备独立验收；已有项目测试可声明为只读输入，宿主在执行前冻结副本，再优先调用本地 `agi_like_runtime` 的 `native_start` 工具，传入 hook 提供的真实会话ID、工作区、输入、产物及现成验收位置。它在宿主中执行，不依赖模型沙箱读取Skill脚本。未提供MCP工具的宿主才使用包内 `scripts/native_task.py begin --workspace ... --input ... --output ... --review-script ...`。输入/产物参数可重复；会话ID默认取宿主 `CODEX_THREAD_ID`，启用 hooks 后原始目标直接来自实际提交的用户请求。不要要求用户手写合同、数据库路径或技术配置。详细方法见 [`codex-native.md`](references/codex-native.md)。

原生 hooks 在工具调用前后记录真实观察，在结束时独立验收；失败反馈回同一会话，由 Agent 在已授权范围内修复。`begin` 的登记不等于 hooks 已连接，须以 `status` 中的 `hook_observed` 和实际事件为证。hooks 尚未加载或信任时，仍由 Agent 在交付前主动调用 `native_task.py verify`，失败就修复后再验；不得把登记、模型自评或测试文件存在当作完成。最多按原预算返修，不无限继续；未知动作先对账，不重新建任务绕过状态。

非 Codex 宿主或明确需要独立模型循环时，使用 `scripts/agent_task.py`，由宿主准备本次工作区、写入授权和可信验收；本地 JSON/文本资料任务缺现成脚本可用 `--prepare-review`。它不是当前 Codex 会话的自动订阅入口。已有合同可调用 `agent_run.py`。同一目标只使用一种执行模式，避免与 bridge 重复建任务；`bridge.py` 仅登记，不能证明实际接管。完整运行说明见包根目录 `AGENT_RUNTIME.md`，源码中位于 `portable-agent-kit/AGENT_RUNTIME.md`。

需要运行项目检查或访问服务时，由宿主按本次授权准备 `--tool-profile`，使用固定命令/HTTP 操作，勿把配置工作转交用户手填。自然语言入口支持 Codex 与兼容模型 API。外部写入另需明确授权；请求失败或结果未知不盲重试。队列可显式 watch 等待新任务，停止与时间/任务上限均由宿主控制。

独立模型循环任务用同一 `--control-root` 执行 `agent_task.py status/resume/pause/cancel/reconcile`；停止请求只有在执行器确认安全边界后才算生效，未知结果先读回，不重新开一笔任务重做。技能提炼、真实 holdout/内容变异回归、激活及撤回用 `scripts/skill_manage.py`；失败归档不等于学会，历史经验不授予当前权限。

`agent_runtime/` 提供模型、工具、项目记忆和技能循环，控制平面保留状态与独立证据，`scripts/agent_queue.py` 提供有界批次。桥接失败时标记 `UNREGISTERED`；没有实际事件回写时不声称控制平面已接管。宿主必须实际加载并验证本 Skill；这些工具不增加账号、权限或自动后台运行。
