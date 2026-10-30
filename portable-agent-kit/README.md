# Portable AGI-like Kit

AAP-0.1是兼容请求信封；1.9.2阶段版保留version 2状态门禁并增加 `delivery-contract/1`、SQLite 控制平面、租约、检查点、独立证据验收和受控自升级候选。Skill 仍是模型入口，控制平面把状态、边界、幂等和证据留在模型外；它不会自动增加模型判断、业务知识、账号权限、宿主工具或后台调度能力。

当前Codex日常任务优先使用本地MCP与原生hooks接入：`native_start`登记原目标和独立验收，`native_status`查看状态，`native_verify`请求验收；模型继续使用原生工具，Stop负责验收与有界返修。独立模型循环保留给其他宿主或显式隔离任务。功能、安装与已知问题见[仓库首页](../README.md)和[使用指南](../USAGE.md)。

## 文件与入口

- `core/state_machine.py`：无模型、无网络依赖的 0.2 状态机；逐项验收要求宿主 `verify_evidence` 回调确认实际证据。旧 `state_version` 状态会以 `STATE_UPGRADE_REQUIRED` 拒绝，不自动迁移。
- `control_plane/`：`delivery-contract/1` 合同规范化、SQLite 任务/事件/租约持久化、独立验收回调和 `self-improvement/2` 升级候选协调器。
- `agent_runtime/native_session.py`：当前Codex会话的持久状态、真实hook观察、独立结束验收及有界返修。
- `scripts/native_mcp.py`：本地stdio服务，只开放native_start/native_status/native_verify。
- `scripts/native_task.py`：原生入口的CLI等价操作及hook配置；登记不等于已观察到接入。
- `agent_runtime/`：模型适配器、显式工具注册、长期记忆、技能调用和跨任务经验循环。
- `agent_runtime/models.py`：OpenAI-compatible 模型适配器；通过 `AGI_LIKE_MODEL_URL`、`AGI_LIKE_MODEL`、`AGI_LIKE_API_KEY` 接入真实模型。
- `agent_runtime/skills.py`：候选技能、激活技能和跨任务使用次数的持久库。
- `scripts/agent_run.py`：真实模型入口，提供受限文件工具、长期记忆和技能库；没有模型配置时明确失败，不伪造模型结果。
- `AGENT_RUNTIME.md`：受管执行、可信审查脚本、项目记忆、技能回归、暂停续接和有界 worker 的使用说明。
- `scripts/agent_queue.py`：依赖任务的有限批次 worker，通过同一 agent_run 合同验收，不自动重试未知任务。
- `CONTROL_PLANE.md`：控制平面、宿主契约和最小生命周期说明。
- `scripts/control_plane.py`：创建任务、领取租约、查看计划、提交状态事件、恢复对账和读取事件历史；不执行任意 shell 或外部动作。
- `scripts/independent_verify.py`：由不同于任务 owner 的 reviewer 重新读取证据，校验路径、哈希和确定性断言。
- `scripts/bridge.py`：由 Agent/Skill 会话登记真实目标、授权范围和 session_id，让 Agent Runtime 跟踪实际会话，而不是手工测试任务。
- `scripts/runtime_event.py`：宿主钩子提交单个 Runtime 事件，把工具调用、证据、暂停、恢复、取消、对账和完成写回同一任务。
- `scripts/session.py`：仅做受限本地产物检查的 CLI，不调用模型、任意 shell、浏览器或外部服务。用 `init`、`next`、`check --check ID`、`finish` 推进；仅支持契约内的文本、JSON、CSV 检查，其他验收必须交给真实宿主验证。
- `core/local_checks.py`：上述 CLI 的只读检查器；只证明明确声明的机械属性，不证明语义、体验、授权或平台状态。
- `scripts/challenge.py`：不新建队列即可冻结候选、在临时副本运行审查者unittest、精确核对汇总及生成表格；改动依赖使旧结果失效。它运行受信Python代码，不是安全沙箱或独立身份认证。`--help`与Skill的`references/result-verification.md`给出范围和调用。
- `core/coverage.py`：把原文段落、声明结果和真实逐方法回执联系起来，优先补缺口、复用有效证据；未知/预算不足不放行，覆盖齐全后仍需宿主判断语义充分性。
- `core/json_relations.py`：精确的未知汇总与权威数据表格生成/比对；只证明声明的关系，不证明源数据、自由文本或业务口径正确。
- `schemas/aap-0.1.json`：AAP-0.1 请求信封 schema；0.2 状态门禁及新增操作见 `PROTOCOL.md`。
- `adapters/example_adapter.py`：内存 JSONL 演示，不执行工具、不持久化 state，也不是可直接接入的平台适配器。
- `scripts/export_skill.py`（系统源码包入口）：从完整系统目录生成自包含 Skill 文件夹和 ZIP；导出内容不带个人偏好或本机运行时。
- `scripts/capability_probe.py`：可选无状态预检；只检查本次显式需要的能力，输出READY/BLOCKED/NEEDS_CONTEXT，不创建任务状态、不执行工具。与持久核心共享三态归一化。
- `scripts/host_replay.py`：校验宿主安装、加载、调用、分流和读回回执是否绑定当前候选版本，并重算原始证据哈希；不执行宿主、不解释截图，也不把模型自报当作兼容性证明。
- `scripts/blind_review.py`：为冻结评测生成不泄露臂信息的证据包，并校验独立判分回执、候选版本和证据绑定；未知保持未知。
- `scripts/eval_contract.py`：校验冻结的0.13评测合同和JSONL运行回执，并输出保留unknown的描述性指标；不调用模型、不授予权限、不替代交付门禁。
- `scripts/quality_contract.py`：按需核对宿主、UI、系统设计、自主循环和A/B合同；未知与缺项不替代真实业务验收。
- `scripts/capacity_policy.py`：分类容量、配额、权限和传输故障，提出有界恢复建议；不自动修改账号或切换模型。
- `scripts/self_improvement.py`：记录带哈希证据的经验，按不同 run/task 聚类重复根因，读取独立审核/回归/灰度/回滚计划回执生成候选；不改源码、不发布、不调度。
- `scripts/release_check.py`：只读核对当前版本元数据、导出ZIP、BUNDLE文件哈希和发布校验值；不调用模型、不验证宿主加载或业务质量。
- `../scripts/install_agent.py`：安装已导出的Skill目录；generic模式显式指定任意宿主的真实Skill根目录，ZCode/Kimi CLI支持目录快捷方式。

普通任务的核验入口是`python3 portable-agent-kit/scripts/challenge.py --help`。本机完整系统确需持久恢复时的兼容示例：

```sh
python3 portable-agent-kit/scripts/session.py init --session /tmp/demo-session --workspace /path/to/project --contract portable-agent-kit/examples/contracts/software.json
python3 portable-agent-kit/scripts/session.py next --session /tmp/demo-session
python3 portable-agent-kit/scripts/session.py check --session /tmp/demo-session --check AC1
python3 portable-agent-kit/scripts/session.py check --session /tmp/demo-session --check AC2
python3 portable-agent-kit/scripts/session.py finish --session /tmp/demo-session
python3 scripts/export_skill.py /path/to/new-parent/agi-like
```

导出目录须为尚不存在的 `agi-like` 目录。运行器将 session 保存在工作区外；不要删除或覆盖已有 session。Python 3.9+，仅标准库。

## 宿主接入

通用安装入口适用于任何提供兼容Skill目录的宿主，包括DeepSeek Harness、自建Agent及各种CLI/IDE；实际目录和注册机制由宿主文档/配置确认。ZCode/Kimi有已核对目录的快捷方式。安装后脚本和引用自包含，不需要额外安装Lite或带入个人系统。详见[宿主安装](../skill/agi-like/references/host-integration.md)。

**没有完成这些平台的实机加载或自动分流验证**。宿主提供真实工具、权限和证据；控制平面可以保存任务和事件，但真实工具执行、身份和证据回调仍由宿主提供。文件安装通过不代表客户端加载，能力清单只是声明，参考适配器只是内存演示。

完整系统的本机兼容入口默认使用Codex；独立包可由用户选择的兼容宿主加载。外部写入、身份操作、付款、发布和不可逆操作仍需具体授权及真实宿主能力。非软件业务只复用目标澄清、证据判断、验证与失败恢复方法；不得无端套用软件队列或软件验收清单。

此包提供本地安装与有界同步任务队列；不启动HTTP服务、后台调度器或自动授权。实际交付范围以当前项目验收和目标宿主的真实证据为准。
