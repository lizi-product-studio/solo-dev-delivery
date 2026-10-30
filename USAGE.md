# AGI-like 1.9.1 使用指南

当前为80%阶段版。先看[功能与边界](README.md)，完整参数见[Runtime手册](portable-agent-kit/AGENT_RUNTIME.md)。下面的示例是本地合成数据任务，不涉及生产账户或对外发送。

## 1. 安装

从[当前主分支](https://github.com/lizi-product-studio/solo-dev-delivery/tree/main)下载 [1.9.1 ZIP](packages/agi-like-1.9.1.zip) 和[校验文件](packages/agi-like-1.9.1.zip.sha256)，解压并保留整个 `agi-like/`。确认宿主实际使用的Skill搜索根目录，再运行：

```sh
python /absolute/export/agi-like/scripts/install_agent.py --destination /absolute/host/skills --dry-run
python /absolute/export/agi-like/scripts/install_agent.py --destination /absolute/host/skills
python /absolute/export/agi-like/scripts/install_agent.py --destination /absolute/host/skills --verify
```

不同版本先备份旧 `agi-like/` 至搜索目录外；安装器不会直接覆盖不同内容。`FILES_VERIFIED`只证明文件一致，需要按宿主方式刷新/加载并实际调用。[更多安装说明](INSTALL.md)。源码入口位于 `portable-agent-kit/scripts/`；导出/安装版入口位于 `scripts/`。

## 2. 在 Codex 中日常使用

安装后由Agent先注册本地MCP服务：`codex mcp add agi_like_runtime -- <Python解释器> <实际Skill目录>/scripts/native_mcp.py`，再运行一次 `python <实际Skill目录>/scripts/native_task.py install-hooks`，保留其他hooks并生成本适配器定义。Codex要求审阅并信任准确的hook定义，可通过CLI的 `/hooks` 完成；安装器不修改信任数据库。按宿主需要重新加载会话。

日常直接给目标：

```text
$agi-like
修复当前目录 pricing.py，完整规则见 TASK.md。只修改该文件；
复用已有项目检查，普通技术问题请自行修复，完成后交付结果。
```

Agent应从原目标确定输入与产物，复用可信验收（项目内测试须列为只读输入，由宿主冻结），或先准备独立验收，然后优先调用本地MCP工具 `native_start`，不要求模型执行安装目录里的脚本。没有MCP工具时的CLI等价调用是：

```sh
python <实际Skill目录>/scripts/native_task.py begin --workspace <项目目录> --input TASK.md --output pricing.py --review-script <外部独立验收.py>
```

这是Agent负责的接入操作，用户不必手填命令。会话ID来自宿主，状态目录来自hooks的会话握手；不要在业务目录搜索运行时脚本，hook上下文已提供实际入口。继续使用当前会话的原生工具，不另起JSON模型循环。结束时验收失败会自动送回当前会话，默认最多2次返修；结果未知先对账。

```sh
python <实际Skill目录>/scripts/native_task.py status
python <实际Skill目录>/scripts/native_task.py verify
```

`hook_observed`证明是否收到真实工具事件。hooks未加载时，Agent在交付前主动verify，依据失败修复并再验；不把未信任配置或登记成功当成自动接管。输入/产物和独立测试必须通过同一控制面，才有COMPLETED。不可变回执保存在 `~/.agi-like/native/tasks/<任务ID>/receipts/`；默认项目记忆沿用原有 `~/.agi-like/projects/工作区哈希/`。

原生接入保留Codex权限和工具边界。某些工具路径可能不发hooks，异步进程、中断和外部副作用不能假装已完成；本模式不提供通用外部对账或任意进程自动恢复。详情见[原生接入方法](skill/agi-like/references/codex-native.md)。

以下3至7节介绍保留的独立模型循环入口；其`control-root`管理命令不用于原生会话任务。

## 3. 命令行跑第一笔任务

前提：Python、可用的本机Codex CLI及其模型权限。下面在源码仓库根目录用PowerShell运行；所有目录需按本机调整。请使用一组尚不存在的新任务目录，不要把失败任务换个目录重复提交来绕过未知状态。

```powershell
$workspace = 'C:/agi-like-demo/settlement-task'
$control = 'C:/agi-like-demo/control/settlement-001'
New-Item -ItemType Directory -Path $workspace -ErrorAction Stop | Out-Null
'{"currency":"CNY","signed_amounts":[1280,450,-80,230,-30,75]}' |
    Set-Content -LiteralPath "$workspace/settlement.json" -Encoding utf8

python portable-agent-kit/scripts/agent_task.py `
  --goal 'Read settlement.json. Write report.json with net_amount equal to the sum of signed_amounts, transaction_count equal to its length, and currency copied from the input. Preserve the input. Do not perform external business operations.' `
  --workspace $workspace --allow-write report.json `
  --authorization-ref local-demo-request --control-root $control `
  --prepare-review --provider codex --max-steps 12
```

默认使用CLI的模型配置；需要指定时追加 `--model YOUR_AVAILABLE_MODEL`。不要把占位符作为真实模型名运行。安装版将脚本路径替换为实际安装目录下的 `scripts/agent_task.py`。

预期产物：`report.json` 中净额1925、笔数6、币种CNY。是否完成以本次实际回执为准。`--prepare-review`消耗两次模型决策，自动规划另占一次，均包含在12步总预算内。覆盖不足时返回 `NEEDS_VERIFIER`，不会仅凭文件存在关闭任务。

已有可信验收时，用 `--review-script C:/review/independent.py` 替代 `--prepare-review`。脚本是unittest源文件，必须位于工作区外；宿主应保证其可信和业务覆盖。`--semantic-review`可追加独立上下文的语义判断，但不能替代这些测试。

## 4. 在哪里查看任务，怎样管理

任务在调用入口时创建，没有控制面板。使用同一个 `control-root` 查询和管理：

```powershell
python portable-agent-kit/scripts/agent_task.py status --control-root $control
python portable-agent-kit/scripts/agent_task.py pause --control-root $control --reason 'inspect current artifacts'
python portable-agent-kit/scripts/agent_task.py resume --control-root $control
python portable-agent-kit/scripts/agent_task.py cancel --control-root $control --reason 'stop this task'
```

暂停和取消请求需执行器在安全边界确认；`PAUSE_REQUESTED`不等于已暂停。已完成、取消、预算耗尽或存在未知动作的任务不能靠resume重置预算。要演练暂停续接，可在首次启动加 `--steps-per-run 1`，然后检查状态再resume，不要另起同一业务目标。

结果未知时先从status取得 `action_id`，再由宿主读回对账：

```powershell
python portable-agent-kit/scripts/agent_task.py reconcile --control-root $control --action-id ACTUAL_PENDING_ACTION_ID
```

这里只能自动核对部分精确文件写入；技能、命令、HTTP或其他操作可能返回 `NEEDS_ADAPTER`。它要求对应证据/适配器，不能把“未观察到”当成“没有发生”。

## 5. 产物和记录保存在哪里

| 位置 | 内容 |
| --- | --- |
| 工作区 | 本次允许写入的业务产物 |
| control-root/request.json、plan.json、contract.json | 原始目标、规划和冻结合同 |
| control-root/intake-receipt.json | 入口状态、模型决策总数和执行结果 |
| control-root/receipt.json、runs/ | 当前发布回执、各次调用的不可变回执 |
| control-root/independent-reviews/ | 冻结候选、测试结果和证据 |
| control-root/review-preparation/ | 自动验收编写、覆盖审查和规则（启用时） |
| 不可变回执同目录的 .skill-health.json | 技能健康观察和处置（适用时） |
| 默认 ~/.agi-like/projects/工作区哈希/ | 持久项目记忆与技能数据库 |

`--project-store`可改变项目状态的根目录，实际仍按工作区隔离。不要删除控制目录来“恢复”任务。历史记忆没有授予新权限，也不是模型权重训练。

## 6. 使用兼容模型 API

服务需支持Chat Completions请求以及JSON对象输出。`AGI_LIKE_MODEL_URL`为完整请求地址，不是只填域名或自动追加路径的base URL。

```powershell
$env:AGI_LIKE_MODEL_URL = 'https://YOUR_PROVIDER/v1/chat/completions'
$env:AGI_LIKE_MODEL = 'YOUR_AVAILABLE_MODEL'
# 通过本机安全方式设置 AGI_LIKE_API_KEY，不要写入仓库、回执或截图。
```

首次启动改为 `--provider api --model YOUR_AVAILABLE_MODEL`，保留原目标、授权、预算和验收参数。API模式不接受Codex专用的 `--effort`/`--isolate-model-config`。当前有步骤、执行时长与重试边界，尚无token/金额硬预算；不能宣称弱模型在任意任务上等效强模型。

## 7. 项目测试、HTTP和技能学习

`--tool-profile`由宿主在工作区外准备，指定允许的固定命令、HTTP操作与必要调用；命令固定argv，HTTP凭据引用环境变量。外部写入还需本次授权及 `--allow-external-writes`。配置和完整例子见[Runtime手册](portable-agent-kit/AGENT_RUNTIME.md)。

技能生命周期入口：

```sh
python portable-agent-kit/scripts/skill_manage.py --library /absolute/project/skills.sqlite3 list
python portable-agent-kit/scripts/skill_manage.py --library /absolute/project/skills.sqlite3 health
```

`propose / improve / review / canary / activate`提供提炼、失败提议、内容回归、旧新对照和激活；`monitor / disable / restore / rollback`提供证据监控和恢复。用各子命令的 `--help` 查看参数。模型自称学会不能直接激活；未知效果不会自动重试。受管工作流独立失败可停用，只有满足历史任务与回归证据条件的旧版才能自动恢复；不会重做失败任务或回滚业务文件。

## 常见状态

| 状态 | 含义及下一步 |
| --- | --- |
| COMPLETED | 本次候选通过该合同和实际验收范围；检查产物与回执 |
| NEEDS_INPUT | 缺少必要业务决定/资料，由宿主继续找资料或请求最小决定 |
| NEEDS_VERIFIER | 现有验收无法覆盖，需要可信脚本或领域适配器 |
| PAUSED / CANCELLED | 已确认暂停/取消；继续或重启须遵守当前状态 |
| NEEDS_RECONCILIATION | 某动作结果未知，先核对外部/持久结果 |
| NEEDS_ADAPTER | 当前对账入口无法核验该动作，需要对应适配器 |
| BUDGET_EXHAUSTED / CONTROL_BLOCKED | 原预算或状态边界阻止继续，先查看原因，不重置预算冒充续接 |

遇到技术错误由宿主先诊断和尝试可回退恢复；只有身份、权限、必要业务决定或已证实无可行路径才要求用户接手。更多边界及未完成内容见[后续迭代](NEXT_ITERATION.md)。
