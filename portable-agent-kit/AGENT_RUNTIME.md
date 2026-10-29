# Agent Runtime：运行与验收

此入口运行真实模型决策，执行注册工具，把本轮工具结果直接传回下一轮模型，并由宿主对合同中声明的产物做读回。跨任务记忆只补充上下文；它不能替代本轮观察或验收。

## 本地运行

普通用户直接给 Skill 目标。宿主从本次授权确定写入范围，并复用可信测试或由独立上下文准备验收后，可直接接入：

```powershell
python scripts/agent_task.py --goal "按原始订单计算销售、退货和未知金额，生成 summary.json" `
  --workspace C:/work/task --allow-write summary.json `
  --authorization-ref current-user-request --control-root C:/work/control/new-task `
  --review-script C:/work/review/independent.py --model YOUR_MODEL --max-steps 16
```

源码运行时脚本路径为 `portable-agent-kit/scripts/agent_task.py`。`--control-root` 必须是工作区外尚不存在的目录；存在时不会重新执行。`--allow-write` 可重复指定文件或以 `/` 结尾的目录，`.` 表示宿主已授权整个工作区。模型提交的计划不能扩大此范围、增加预算或指定验收器。未提供可信独立验收返回 `NEEDS_VERIFIER`，不会仅用文件存在宣称完成；必要业务决定缺失返回 `NEEDS_INPUT`。验收代码是宿主信任边界，不能直接采用执行模型的自评。

入口保存原始目标、文件清单、规划事件、计划、合同和运行回执。默认一次真实模型规划消耗总决策预算 1 步；控制面另外计 start/finish 事件，两种预算分别记录。自动合同绑定独立验收脚本，直接调用下层入口也不能省略或更换它。初始输入及已有可编辑文件绑定版本，输出范围保持宿主约束。

已有合同的底层入口：

```powershell
python portable-agent-kit/scripts/agent_run.py --provider codex `
  --goal "读取 stock.json 并写入 report.json，汇总数量和SKU数" `
  --workspace C:/work/task --memory C:/work/control/memory.sqlite3 `
  --contract C:/work/control/task.json --db C:/work/control/tasks.sqlite3 `
  --receipt C:/work/control/run.json
```

`codex` 使用本机已登录 CLI；`api` 使用 `AGI_LIKE_MODEL_URL`、`AGI_LIKE_MODEL`、`AGI_LIKE_API_KEY`。模型 API 的 chat-completions 协议必须由所选服务支持。不会读取或导出宿主令牌。`--model` 可显式选择已获访问权的模型。

`--effort` 和 `--isolate-model-config` 仅适用于 Codex，后者要求明确模型。它跳过用户配置，不等同于经过认证的工具安全隔离。决策子进程出现未受管工具、超时或缺失终态时，保存待对账动作，不自动重新调用。

文件工具包含 `read_file/read_json/write_file/write_json`、按 JSON Pointer 局部修改的 `update_json`、从 JSON 字符串字段生成一致文本的 `write_text_from_json`。所有写入均通过同一合同范围守卫；局部修改先在内存全部验证再写一次，业务 JSON 的 status 包装在 data 内，不用作执行状态。

新入口默认启用 `--auto-verify`；下层手动启用时必须有合同和可信 review。实际读取声明输入、成功写过完整产物集合且候选哈希变化后，宿主运行独立验收，通过同一完成门后可立即停止，不依赖模型主动 finish。重复候选不重复审查；执行模型的 finish 仍不能绕过自动合同的输入读取、版本和验收门。验收前后与完成提交时重新核对候选、输入及 reviewer 字节，审过的版本和最终证据必须相同。

合同示例：

```json
{
  "owner": "agent-runtime",
  "candidate": "input-version-1",
  "authorization_ref": "本次用户授权",
  "scope": ["report.json"],
  "excluded": ["外部写入"],
  "policy": {"workspace_write": true, "shell_required": false},
  "acceptance": [{
    "id": "total", "expected": "total_units=22", "environment": "local",
    "assertion": {"type": "json_pointer_equals", "path": "report.json", "pointer": "/total_units", "value": 22}
  }]
}
```

`workspace_write` 明确授予本地文件写入能力；`shell_required: false` 表示当前文件工具流程不需要任意 shell。旧合同省略 `shell_required` 时保留原先依赖 shell 的行为。CLI 没有 shell 工具；未授权写入或依赖不可用能力时会阻断。工具范围检查仍不等价于操作系统安全沙箱。

## 结果与边界

- `COMPLETED`：合同当前版本的全部产物断言通过控制平面复核。
- `UNVERIFIED`：模型结束了推理，但没有可信验收器；不写成功记忆或学习。
- `CONTROL_BLOCKED`：缺少产物、断言失败或控制平面有未解决阻断。
- `MODEL_ERROR`：模型服务、JSON协议或CLI进程异常。
- `BUDGET_EXHAUSTED`：达到模型循环上限，不能视为完成。

`--receipt` 保存决策、工具观察和控制平面事件；同目录 `model-events/<task_id>/` 保存 Codex 每一轮的 stdout、stderr 和退出码。CLI 会等待正常退出，不因 `turn.completed` 强制杀进程。Codex 工作区执行与外部 Runtime 决策是两种模式：直接 CLI 工具事件回放由 `scripts/verify_codex_runtime.py` 验证；决策模式由 `agent_run.py --provider codex` 驱动注册的文件工具。

确定性验收能核对结构和数值，不能证明自由文本建议、法律结论或未知业务政策正确。技能执行仅可调用注册工具；候选记录不代表通过隐藏回归或具备跨领域迁移能力。真实跨域对照评测、长期运行和当前桌面会话的自动事件订阅仍需分别完成验收。

## 经验到可执行技能

`SkillLibrary.propose_from_repeated_success(goal)` 仅在至少两个不同任务的成功记录具有相同工具拓扑时编译 `tool-recipe/1`。每个工具参数变成显式 `$input`；训练文件名、正文和客户值不复制进技能。不同拓扑、失败、未知、无真实观察的轨迹不编译；这只是流程复用，不推断新的业务算法。

`regress_recipe()` 使用宿主提供的至少两个未出现在训练任务中的参数用例和预期结果实际执行工具，保存逐用例证据及 `skill-regression/1`。模型不接收这些用例。`activate()` 重新校验证据、版本和规格哈希；无门禁的 `promote()` 已禁用。

`execute()` 会再次核对激活证据，并在第一次副作用前验证全部工具名、参数和签名。文件写入仍受当前任务合同 scope 限制，技能本身不能扩大权限。失败灰度可 `disable(name, reason=...)`；恢复仍核证据。`rollback(name, version, reason=...)` 只能恢复曾通过激活门且证据仍有效的归档版本，并保留状态事件；下一版本号继续递增。

## 项目记忆

`agent_task` 默认将同一工作区的记忆和技能放在 `~/.agi-like/projects/<workspace-hash>/`，连续任务复用同一数据库；`--project-store` 可指定其他状态基目录，其下仍按工作区哈希分别保存两种数据库。目录在任务工具范围外；不同工作区不共用技能库。没有验收器或必要资料时不会创建学习数据库。任务控制目录、回执和检查点仍逐笔独立。

底层 CLI 默认按已解析工作区路径的哈希隔离记忆；显式共享同一数据库且需要复用同一项目多个目录时传 `--project-id`。`MemoryStore` 保存来源、过期时间、更正链和撤回原因；普通检索排除过期、撤回和被替代条目，旧内容仍可审计。Runtime 写入的观察绑定 task/step，只有验收通过的任务才写 success。

中文双字词与英文 token 检索是词法检索，不是 embedding 语义检索。`correct()` 需要新来源并新增记录；`retract()` 保留历史。过期知识、项目经验和历史成功都不能授予当前任务权限。

## 安全暂停、重启和未知动作

`agent_run.py --steps-per-run 1` 在一个安全步骤后暂停，不重置 `--max-steps` 的任务总预算。指定固定 `--task-id`；下次用相同工作区、目标、合同、模型配置和预算加 `--resume --task-id ...` 接续。带控制平面时检查点默认与任务放在同一 SQLite 文件；无合同时用 `--state-db` 指定保存位置。控制文件必须位于工具工作区外。

`RunJournal` 在模型调用前、动作执行前和观察保存后持久化状态，并用 generation 防止并发覆盖。新进程会把已完成的观察交给模型，跳过已完成步骤。若上次停在 `ACTION_PENDING`，动作可能已产生副作用，返回 `NEEDS_RECONCILIATION`，不重放；由可信宿主提供绑定 action_id 的读回验证回调后才能更新检查点。模型调用中断或完成提交中断也保持待核对，不能凭超时认定什么都没发生。

控制面租约过期后必须显式 `reclaim`，证明需要绑定 task_id 和旧 lease_id；过期 `claim` 无法直接偷锁。暂停/取消/对账等安全停止操作不受步骤预算阻断；lease 失效后不会再执行新的工具动作。此实现不认证宿主传入的进程停止证明，真实进程管理仍属于宿主。

## 有界多任务 worker

`scripts/agent_queue.py --queue queue.sqlite3 enqueue --task-id T --job job.json` 登记任务；`--depends-on` 可重复指定已存在的前置任务。job 必须包含 goal、workspace、contract，可选 provider、model、max_steps、project_id、effort、isolate_model_config、review_script、auto_verify。队列和直接入口执行相同的参数约束，review 在工作区外按字节冻结，不能静默忽略模型配置或绑定的验收器。保存的只是 JSON，不执行其中的任意命令。

`scripts/agent_queue.py --queue queue.sqlite3 work --worker local-1 --control-root C:/work/control --max-tasks 2` 同步执行最多两笔任务；每笔通过公共 `agent_run` 入口和合同验收，留下独立回执及哈希。只有前置任务 COMPLETED 才能领取后续任务；失败、未知、进程消失不会自动重试或夺取运行中任务。任务暂停目前在批次队列中按非完成处理，需宿主显式接续，不能把有限批次 worker 描述为长期无人值守调度器。
