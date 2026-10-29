# Agent Runtime 集成验收（2026-10-29）

本记录对应工作区当前源码。尚未发布新版本，也没有把真实任务通过外推为完整系统或 AGI/ASI 已完成。

更新：本轮已整理为 1.2.0 候选并安装到本机；文末的领域验收和打包记录是当前状态，早期段落中的“尚未发布/尚缺”保留为时间顺序记录，不能当成当前所有能力均缺失或均完成。

## 真实 Codex 工具任务

执行命令：`python scripts/verify_codex_runtime.py --output <fresh-run-directory>`。

实际 run 位于工作区 `outputs/codex-runtime-acceptance-20261029-r1/`（相对仓库父级的项目目录）。Codex 读取三行订单 CSV 并生成 `summary.json`，宿主独立重算数量 10、金额 109。任务 `real-09d548698f9c` 最终为 `COMPLETED`，独立验收为 `PASS`，收到 9 条真实 JSONL 事件。

原始证据：`codex/stdout.jsonl`、`codex/stderr.log`、`codex/process.json`、`workspace/summary.json`、`events.json`、`review.json`、`result.json`。

PowerShell shell snapshot 日志仅为警告；本次退出码为 0。此前仅凭 stderr 尾部把它认作失败根因的说法已撤回。

## 通用入口的真实模型循环

使用 `portable-agent-kit/scripts/agent_run.py --provider codex --contract ... --db ... --receipt ...`；模型仅输出 JSON 决策，Runtime 执行注册文件工具。

实际 run：`outputs/agent-entry-acceptance-20261029-r2/`，任务 `agent-1f194ef8eb88`。4 步为 `read_file → write_file → read_file → finish`。输出 `report.json` 包含数值 `total_units=22`、`sku_count=3`；控制状态 `COMPLETED`，两项证据 `host_verified=true`，记忆库包含 3 条 episode、1 条已验收 success。

原始证据：`model-events/<task_id>/step-*/stdout.jsonl`、`stderr.log`、`process.json`、`receipt.json`、`workspace/report.json`、`control.sqlite3`、`memory.sqlite3`。r1 是被中断的尝试，没有回执，不计通过。

## 缺陷修复与回归

- stdout/stderr 并行读取，全程执行超时；正常 `turn.completed` 不终止进程。
- 模型参数位置修正；命令 exit_code 非零映射失败，流缺少终态不通过。
- 模型每轮直接收到当前 observations，长期检索不再承担传递本轮工具结果的职责。
- 没有可信验收器的 finish 返回 `UNVERIFIED`，不写成功记忆。
- CLI 绑定合同与控制平面；真实文件读回生成绑定证据后才能完成。
- 活跃技能调用真实注册工具；CLI 连接技能库，成功经验由验收后路径记录。
- 技能激活禁止无门禁 promote，要求真实回归回执、版本、规格哈希和证据文件绑定。

集成回归：75 passed，46 个子测试通过。范围为 Agent CLI、Runtime、Codex 适配器、技能激活、RuntimeSession、事件 CLI、控制平面、验收适配器、升级协调器、发布检查与旧状态机。

## 完整目标仍缺的证据

- 从跨任务经验生成可执行的参数化技能，并经过隐藏任务、灰度、回滚和真实迁移验收。目前经验候选仍主要是日志集合。
- 记忆治理：项目隔离、纠错、过期、来源与检索评测。
- 长任务暂停/崩溃恢复的真实模型续接、多任务调度、外部副作用对账的真实适配器。
- 当前桌面活动聊天自动订阅事件；受管 CLI 的事件转发不能代替这一能力。
- 多领域、弱/强模型同条件对照；当前两个小任务不能证明跨行业提升。
- 此轮源码尚未重新发布，安装包不作为当前新增功能的验收证据。

## 后续进展：参数化技能与记忆治理

此前“经验候选主要是日志集合”的缺口已补上一个有界实现：重复成功且拓扑一致的轨迹可以编译为参数化 `tool-recipe/1`，配套真实用例执行的回归器、证据激活门、执行前预检、停用/恢复及版本回滚。

真实回放：项目 `outputs/skill-transfer-acceptance-20261029-r1/` 保存 `training.json`、`holdout-receipts/`、`real-receipt.json` 和 `result.json`。训练决策是明确标记的脚本替身，文件工具及宿主读回是真实执行；两个新参数用例为回归；第三个任务使用真实 Codex JSON 决策，经公共 CLI 执行 `skill → finish`，技能 uses=1、控制状态 COMPLETED。输出文件真实包含 units=9，合同独立读回通过。这证明参数化流程可执行和复用，不证明自主发现新算法或跨行业迁移。

记忆已新增项目隔离、来源、过期、更正与撤回；旧数据库保留内容并迁移到 local scope。检索为中英词法方式，不宣称语义学习。

新增变更后的集成回归：87 passed、49 个子测试通过；随后增加的 CLI scope 反例单独运行 3 passed、2 个子测试通过。包含参数提炼、真实文件回归、失败/泄漏用例、激活证据失效、停用恢复、版本回滚、记忆治理及现有 Runtime/控制平面。源码仍未发布，长期模型续接、跨域对照和桌面活动会话自动接入仍未完成。

## 后续进展：真实模型续接与有界队列

真实回放目录 `outputs/runtime-resume-acceptance-20261029-r2/`。第一 CLI 进程执行 1 步后为 PAUSED 并释放安全停止的租约；第二 CLI 进程领取同一任务，保留此前观察，继续到累计 4 步后 COMPLETED。`output.json` 中 sum=42，合同独立验收通过。原始两次 stdout/stderr、paused.json、resumed.json、model-events 和 result.json 均保存。r1 为早期策略字段下的通过记录，r2 已使用明确 workspace_write=true、shell_required=false 的文件工具授权。

崩溃恢复测试使用真实子进程：追加副作用后 `os._exit(23)`，重启不重复追加；核对绑定 action_id 的读回后才继续。并发 generation 更新、过期租约偷换、错误停止证明、无效 TTL 和预算耗尽后的停止操作均有拒绝测试。

新增 `TaskScheduler` 与 `agent_queue.py` 将有界同步批次连接到公共 Runtime CLI。真实 SQLite 竞争领取和两个依赖任务的 CLI 集成测试通过；该部分使用已标注模型替身，不能计作新的真实模型迁移证据。批次没有 daemon 或定时器，未知不重试，暂停任务需显式恢复。这补上调度组件和可执行入口，长期无人值守运行仍未得到验证。

## 三领域真实模型验收与发布候选

`evaluations/runtime-domains-20261029/RESULTS.json` 汇总软件修复、数据统计及采购草稿：各自经过真实文件工具调用、独立 unittest 审查、产物读回和控制面完成门。软件/data 使用 r1；采购 r1 的评估器在运行期间变化，因此最终记录使用复制并冻结评估器的 r2。采购 r2 首次拒绝后修复，通过第二次验收。原始证据位于汇总中列出的本机目录。判分器先经 15 个正确/错误候选自检，其中 12 个为明确的缺失或变异反例。

全量基线：505 passed、1 skipped、180 个子测试；其后安全修复定向回归 65 passed、34 个子测试。修复包括工具执行后异常按 UNKNOWN 保留 pending、部分成功技能同样待对账、证据必须匹配合同路径/环境，JSON bool 不能冒充数字。复现过的“先追加再超时，恢复后又追加一次”已加入防重复执行测试。

候选包版本 1.2.0，独立构建两次 SHA256 相同：58548b68d0ff58c2b1cec5a3abcffadd130441bc6f20511f2cbceccf66e30407；包与用户级安装 71 文件一致。1.1.0 仓库历史包保留 Git 已提交版本，未发布的多次本机构建保存在 outputs。当前仍缺弱模型收益对照、长期无人值守证据和桌面当前聊天自动事件集成，目标保持未完成。
