# Codex 当前会话与 Runtime

## 使用原则

本模式保留当前Codex模型、工具和上下文，Runtime负责持久状态、原始目标、真实hook观察、独立验收和有界返修。普通问答不登记。不要用第二个JSON决策模型接管普通Codex软件任务，也不要把创建数据库等同于提高交付质量。

## Agent 主动接入

1. 从本次用户请求和项目约定确定工作区、只读输入和要交付的文件。找已有可信测试；缺少时先从原始目标准备独立unittest验收，在实现前冻结；项目内的现成测试必须同时声明为只读输入，不修改验收迎合错误实现。简单业务资料任务可以选既有自动数据验收入口；不要把复杂UI或外部平台降成文件存在检查。
2. 优先调用本地MCP工具 `agi_like_runtime.native_start`，用hook提供的session_id、workspace、inputs、outputs、review_script接入；状态和验收使用native_status/native_verify。用户不填合同或目录。它在宿主内执行，避免沙箱读取安装目录脚本被拒绝。缺少这些工具时才实际调用 `python <Skill目录>/scripts/native_task.py begin --workspace <绝对工作区> --input <相对资料> --output <相对产物> --review-script <外部验收脚本>`。`--input/--output`可重复。默认从`CODEX_THREAD_ID`取会话身份；hooks会提供实际原始用户请求，Agent不能改写成较容易的目标。未接收到该请求时由Agent用`--goal`传递完整原始目标。控制目录自动放在`~/.agi-like/native`，不让用户填数据库。
3. 继续使用当前会话的原生工具。`native_task.py status`查看当前任务与`hook_observed`，只有实际事件才能证明接通。hooks在结束时执行独立测试，失败会自动把诊断返回同一会话；Agent继续修复授权内缺陷，不把普通技术错误交给用户。
4. hooks未加载/尚未信任时，在最终交付前主动运行 `native_task.py verify`，根据真实失败修复并再验，不仅停留在等待配置。没有PASS不声称完成。默认最多2次返修、100个原生工具调用、3600秒；不可通过重新begin或换目录重置未解决任务。

项目记忆沿用`~/.agi-like/projects/工作区哈希/`，只有通过验收才写成功；经验只是有来源的历史观察，不增加当前权限。每次验收有不可变回执，后续产物变化使当前完成状态失效。

## 安装 hooks 和本地MCP入口

由Agent使用Codex官方 `codex mcp add agi_like_runtime -- <Python解释器> <实际Skill目录>/scripts/native_mcp.py` 注册本地stdio服务。先备份现有config.toml并保留其他设置；只对已授权的本地管理工具配置每工具approval_mode="approve"，enabled_tools仅限这三个，不改变其他工具或全局批准策略。服务只公开native_start、native_status、native_verify，不提供shell或外部业务动作。Codex新会话加载服务后即可直接调用；本地脚本仍由宿主按同一账号执行，不认证模型提供的任意业务权限。

由已获接入授权的宿主运行 `python <Skill目录>/scripts/native_task.py install-hooks`。它备份并保留现有`~/.codex/hooks.json`的其他hook，仅加入本适配器的UserPromptSubmit、PreToolUse、PostToolUse、Stop、Interrupt处理。Codex需要信任准确的hook定义；安装器不会修改信任数据库或托管策略。通过Codex的`/hooks`审阅本适配器定义，按宿主要求重新加载会话。先核对新定义，再通过宿主公开界面完成信任，不通过伪造状态绕过。

## 边界与回执

主状态是`~/.agi-like/native/sessions.sqlite3`；每笔任务在其`tasks/`下保存原始请求、冻结验收、独立执行结果、控制面事件和不可变`receipts/`。状态`COMPLETED`要求独立验收和控制面同时通过；`hook_observed=false`不能宣称自动接通。真实用户消息数、返修次数、原生工具调用数用于分析参与成本，不代表统计显著收益。

原生hooks的工具覆盖和权限来自Codex，不是OS沙箱或外部副作用认证。前置hook仅代表调用意图。正常Stop边界存在未配对前置事件时，保存partial观察覆盖并仅验收本地候选；不宣称完整工具追踪或外部副作用已核实，不据此重放工具。明确返回异步运行或中断有未完成工具时保留`NEEDS_RECONCILIATION`，不能盲重试；验收结果UNKNOWN/STALE也停止自动重验。超过返修预算会要求如实交代未完成项，随后停止连续返修，避免循环。该模式当前没有宣称任意外部操作可自动恢复，外部业务任务仍需对应适配器和明确授权。
