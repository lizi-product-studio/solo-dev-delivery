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

本地 JSON/文本数据任务可用 `--prepare-review` 替代 `--review-script`：独立上下文从原始目标与完整资料提出内容规则，另一上下文核对覆盖，由固定宿主解释器生成冻结的 unittest 包装；不执行模型生成的任意代码。支持字段比较、求和、计数、排序、键集合与文本映射。期望值须引用原始资料或由其计算，读取范围绑定于合同输入/输出；拒绝未覆盖产物、自比较和缺少来源的常量。两次模型决策计入原有总预算，暂停续接不重置。资料超过64KiB、检查无法覆盖要求、覆盖审查非PASS时返回 NEEDS_VERIFIER，不启动执行。保留原始请求、两次决策、规则、哈希和回执。此方式减少人工写验收的工作；同提供方模型可能相关，覆盖审查不是业务认证，软件行为、UI和平台副作用仍需专用验收。

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

## 管理同一任务

### 项目命令与 HTTP 服务

宿主可传 `--tool-profile C:/work/control/services.json`，其中 commands/http 是命名映射。例如 commands.project_check 的 argv 为 `["python", "-m", "unittest", "discover"]`；http.inventory 的 url 为宿主批准的固定服务 URL，method 默认 GET。模型只能通过 `run_command(name)`、`http_request(name, body)` 选择这些名称，不能提供任意命令参数、URL 或认证头。可用 required 数组声明必须实际成功观察的调用，如 `{"tool":"run_command","name":"project_check"}`。

命令不经过 shell，工作目录固定在项目，环境默认仅包含基本系统路径。执行项目代码继承当前用户的 OS 权限，不能当作文件工具沙箱；宿主只能为本次获准操作提供配置。命令返回 exit_code/passed，调用被观察不等于业务通过；最终仍需独立验收。超时保留 UNKNOWN，使用已有进程树停止逻辑，失败不盲重试。

HTTP 默认只读；POST/PUT/PATCH/DELETE 需宿主明确传 `--allow-external-writes`。auth 配置引用 env 变量名，可选 header/prefix，认证值不交模型且响应会遮蔽该值。跨地址重定向被拒绝；外部写入使用绑定 Runtime 动作的 Idempotency-Key，但服务端是否支持仍由服务决定，未知写入需要真实适配器读回，不能仅靠此头宣称 exactly-once。

`agent_task.py --provider api` 可使用 `AGI_LIKE_MODEL_URL/MODEL/API_KEY` 完成规划和执行，适用于兼容 Chat Completions 的服务；Codex 专用 effort/isolate 参数不能混用。`--max-seconds` 是执行阶段墙钟上限（含暂停，不含此前规划），检查点持久化，续接不重置。

```powershell
python scripts/agent_task.py status --control-root C:/work/control/new-task
python scripts/agent_task.py pause --control-root C:/work/control/new-task --reason "等待安全暂停"
python scripts/agent_task.py resume --control-root C:/work/control/new-task
python scripts/agent_task.py cancel --control-root C:/work/control/new-task --reason "用户取消"
python scripts/agent_task.py reconcile --control-root C:/work/control/new-task --action-id <status中的pending.id>
```

`--steps-per-run 1` 可在启动时分块暂停，也可用于 resume 的单次执行块；总模型预算不变。status 只读 SQLite，不建立新任务。请求响应 `PAUSE_REQUESTED/CANCEL_REQUESTED` 表示意图已持久化；只有 `ACKNOWLEDGED` 才表示执行循环已在安全边界停止。调用在途时等待返回，原子动作派发门保证更早的停止请求优先；不会靠外部声称进程停止而清掉未知。

resume 从冻结的原目标、合同、模型、学习路径和验收器恢复，不重做规划。已完成、取消、未知、审查预算耗尽或仍有租约的任务不能靠普通 resume 绕过。已知模型错误可安全暂停并释放执行者；模型步骤和工具工作预算均保留。管理性 resume/finish 不挤占工具动作预算，工具调用前另核预算，不能先产生副作用再发现超限。

reconcile 默认直接读取原始动作意图与当前文件，仅支持能推导原始精确字节的 write_file/write_json；核对全部绑定后，在同一事务内更新控制面与 Journal，再安全暂停。重复同一读回幂等。技能、未受管工具、缺失 before-image 的局部修改、未确认执行器停止或过期租约仍需要相应宿主适配器，不会用无关文件存在洗掉未知。待取消请求在结果对账后才确认取消。

每次运行保留独立不可变回执；共享 receipt/intake 按 checkpoint generation 和 invocation 身份发布，旧暂停运行者不能覆盖新续接的完成结果。operations 目录保存管理动作与此前回执。当前入口管理是显式调用；没有安装守护进程、后台定时器或桌面事件订阅。

## 结果与边界

`agent_task.py --semantic-review` 可在必需的独立脚本测试之外增加一次新上下文模型评审。它只接收完整原始目标、当前输入/产物字节和合同检查，不接收执行者的结论、对话或记忆；PASS 必须覆盖全部检查并引用实际文件 SHA。候选、角色或评审配置改变会使缓存失效，提交完成时再次核对字节。

`--max-semantic-reviews` 默认 3，从 `--max-steps` 总模型尝试预算内预留，规划、执行和评审总数在回执中合计；评审受剩余时长限制。失败调用也记入尝试上限，相同候选复用已绑定评审，不重复模型调用。模型判断可能相关或出错，因此该层补充领域测试，不能代替法律/医疗/生产等独立事实核验；FAIL/UNKNOWN/证据不齐均不能凭模型自评完成。

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

公共管理入口复用上述实现：

源码开发中的 `skill_manage.py --library DB improve --run receipt-a.json --run receipt-b.json --output NEWDIR --model MODEL` 可从至少两笔不同任务的实际独立测试失败提出新工作流候选；API 模型可加 `--provider api`。先核对真实控制事件、失败测试、候选 packet 和 reviewer 字节，不接受模型文字自述或尚未对账的 UNKNOWN。失败必须共享实际失败检查；重复同一任务或已处理的同组证据不会累加成新候选。

提出者只可组合已注册工具和显式参数，不能复制训练值、运行代码、改预算/权限或直接激活。来源哈希与诊断假说记录在候选中。候选仍需下面的独立产物 holdout/内容变异审查；这条连接本身不证明故障已修复，也不等于新算法学习。

```powershell
python scripts/skill_manage.py --library C:/work/project/skills.sqlite3 propose --goal "重复目标"
python scripts/skill_manage.py --library C:/work/project/skills.sqlite3 review --name <candidate> --suite C:/work/review/suite.json --output C:/work/review/new-result --reviewer independent-host
python scripts/skill_manage.py --library C:/work/project/skills.sqlite3 activate --name <candidate> --receipt C:/work/review/new-result/receipt.json
```

可信宿主套件格式为 `skill-artifact-suite/1`，cases 至少两个且不能只改 id 复制。每个 case 有 id、只读 inputs、输出 scope、技能 arguments 和内容 assertions；编辑已有产物时可用 initial_outputs 初始化已授权目标文件。支持文件哈希、UTF-8 片段和 JSON Pointer 内容断言，每个输出都必须覆盖；仅存在文件或返回 written=true 不足以激活。

review 实际执行隔离文件工具，读回产物，再生成有效内容变异（例如保持 JSON 结构而把 42 改成 43）检查对应 oracle 能拒绝。套件、实际结果、原始/变异产物都绑定哈希。activate 是单独动作，重新核对当前候选版本和全部证据；disable/restore/rollback 需要明确原因。调用时另核 expected_version/spec，防止运行中升级导致绑定与执行版本不同。

实际失败的技能调用带可信版本和控制面动作后，Runtime 将其不可变回执归档并记录到失败账本；不会凭模型自述或单纯整笔任务未通过就认定某技能有错。`record-failure --name ... --run-receipt ... --sha256 ...` 可显式归档，`--quarantine` 仅能停用仍匹配的 ACTIVE 版本。默认未知归档保留原对账义务。该学习环节验证的是参数化流程的受控复用，自主算法学习仍未验证。

技能清单会展示工具步骤与参数绑定，使模型能分清输入和写入目标。宿主为工具声明 read/write/unknown 效果；自定义工具默认保守 unknown。纯前置校验、或仅完成只读步骤后的可信拒绝可反馈给模型纠正；已经完成任何可能写入的步骤后再失败，仍须对账。模型声明的效果或自报失败原因不参与这项安全判断。

## 项目记忆

`agent_task` 默认将同一工作区的记忆和技能放在 `~/.agi-like/projects/<workspace-hash>/`，连续任务复用同一数据库；`--project-store` 可指定其他状态基目录，其下仍按工作区哈希分别保存两种数据库。目录在任务工具范围外；不同工作区不共用技能库。没有验收器或必要资料时不会创建学习数据库。任务控制目录、回执和检查点仍逐笔独立。

底层 CLI 默认按已解析工作区路径的哈希隔离记忆；显式共享同一数据库且需要复用同一项目多个目录时传 `--project-id`。`MemoryStore` 保存来源、过期时间、更正链和撤回原因；普通检索排除过期、撤回和被替代条目，旧内容仍可审计。Runtime 写入的观察绑定 task/step，只有验收通过的任务才写 success。

中文双字词与英文 token 检索是词法检索，不是 embedding 语义检索。`correct()` 需要新来源并新增记录；`retract()` 保留历史。过期知识、项目经验和历史成功都不能授予当前任务权限。

## 安全暂停、重启和未知动作

源码开发中的执行时长门读取合同 `budget.max_seconds`，首次执行时将起点持久化到检查点，暂停等待也计入；续接不会重新计时。模型适配器使用剩余时长限制自身调用，返回后和工具派发前再次核预算。到期不启动新动作；已经在途的工具仍须返回或对账，安全取消可在预算耗尽后确认。此门不代表 token/金额硬预算，也不强制终止任意自定义工具；旧版没有时钟记录的检查点需使用原运行时或明确迁移。

`agent_run.py --steps-per-run 1` 在一个安全步骤后暂停，不重置 `--max-steps` 的任务总预算。指定固定 `--task-id`；下次用相同工作区、目标、合同、模型配置和预算加 `--resume --task-id ...` 接续。带控制平面时检查点默认与任务放在同一 SQLite 文件；无合同时用 `--state-db` 指定保存位置。控制文件必须位于工具工作区外。

`RunJournal` 在模型调用前、动作执行前和观察保存后持久化状态，并用 generation 防止并发覆盖。新进程会把已完成的观察交给模型，跳过已完成步骤。若上次停在 `ACTION_PENDING`，动作可能已产生副作用，返回 `NEEDS_RECONCILIATION`，不重放；由可信宿主提供绑定 action_id 的读回验证回调后才能更新检查点。模型调用中断或完成提交中断也保持待核对，不能凭超时认定什么都没发生。

控制面租约过期后必须显式 `reclaim`，证明需要绑定 task_id 和旧 lease_id；过期 `claim` 无法直接偷锁。暂停/取消/对账等安全停止操作不受步骤预算阻断；lease 失效后不会再执行新的工具动作。此实现不认证宿主传入的进程停止证明，真实进程管理仍属于宿主。

## 有界多任务 worker

`scripts/agent_queue.py --queue queue.sqlite3 enqueue --task-id T --job job.json` 登记任务；`--depends-on` 可重复指定已存在的前置任务。job 必须包含 goal、workspace、contract，可选 provider、model、max_steps、project_id、effort、isolate_model_config、review_script、auto_verify。队列和直接入口执行相同的参数约束，review 在工作区外按字节冻结，不能静默忽略模型配置或绑定的验收器。保存的只是 JSON，不执行其中的任意命令。

`scripts/agent_queue.py --queue queue.sqlite3 work --worker local-1 --control-root C:/work/control --max-tasks 2` 同步执行最多两笔任务；每笔通过公共 `agent_run` 入口和合同验收，留下独立回执及哈希。只有前置任务 COMPLETED 才能领取后续任务；失败、未知、进程消失不会自动重试或夺取运行中任务。任务暂停目前在批次队列中按非完成处理，需宿主显式接续，不能把有限批次 worker 描述为长期无人值守调度器。

新增显式 `work --watch --max-tasks 10 --max-seconds 3600 --idle-seconds 1` 可等待新任务；`stop --control-root ...` 请求在当前任务后停止，重新启动需 `--clear-stop` 明确清除旧请求。worker-state.json 记录运行/空闲/停止，worker.lock 防止同控制根重复启动；未知结果使 watch 停止。总 watch 时间在任务之间检查，各任务另受自己的执行时间预算，不强杀在途动作。此模式没有安装 OS 自启动服务，也没有完成长期稳定性观察。

技能内部注册工具调用由宿主单独记录为 tool_observations，纳入必须输入读取、服务调用及完整产物验收；不采信技能返回值中自述的调用记录。重复相同成功读取时给模型剩余输入与重新规划提示，此提示不保证所有模型都会遵守。
