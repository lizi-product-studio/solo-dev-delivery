# AGI-like Agent Runtime

一套以 Skill 为入口的 Agent 运行系统：接收目标，让模型调用受限工具，保存项目记忆和任务状态，以独立证据验收，并受控积累、停用和恢复技能。

**当前版本：1.9.1 · 80% 阶段版。** 最新源码、功能说明和安装包均位于默认分支 [`main`](https://github.com/lizi-product-studio/solo-dev-delivery/tree/main)。80% 是本次阶段范围的工程称呼，不是测量出的成熟度，也不代表达到 AGI/ASI 的80%。原完整路线保留；本次按新授权专注接通Codex日常会话与实际验收，不以模块数量作为质量提升证明。

[下载安装包](packages/agi-like-1.9.1.zip) · [SHA256](packages/agi-like-1.9.1.zip.sha256) · [如何使用](USAGE.md) · [安装说明](INSTALL.md) · [后续迭代与缺口](NEXT_ITERATION.md) · [阶段交付范围](STAGE_80_DELIVERY.md)

## 现在有什么功能

| 能力 | 当前可以做什么 | 边界 |
| --- | --- | --- |
| Skill 入口 | 在支持的宿主中按任务读取方法，判断权限、证据和下一步 | 文件安装不等于客户端发现、自动选择或后台接管 |
| 目标接收与模型循环 | 自然语言目标形成计划和合同，循环执行模型决策、工具和技能 | 宿主明确工作区、写入范围、预算和必要授权 |
| 工具调用 | 工作区文件/JSON操作；宿主配置的固定命令和HTTP操作；Codex CLI或兼容模型API | 不开放任意模型命令；命令执行不是操作系统沙箱 |
| 可恢复任务 | SQLite状态、租约、检查点、暂停、恢复、取消及部分精确文件对账 | 预算不因恢复重置；未知效果不自动重放 |
| 独立验收 | 冻结可信测试脚本，读取实际产物，绑定候选与输入哈希；可追加独立上下文语义评审 | 语义评审是补充，不能替代业务/平台的真实验收 |
| 自动准备数据验收 | 从原始目标和资料编写声明式规则，再独立审查覆盖，由固定解释器执行 | 仅有限JSON/文本比较和聚合；不支持则返回 NEEDS_VERIFIER |
| 长期记忆 | 工作区隔离的持久经验、检索、来源、失效和撤回 | 是外部记忆，不是模型权重训练或通用语义记忆 |
| 受控技能学习 | 从重复成功或真实失败提出参数化流程；留出、变异与旧新对照后激活 | 自动获得任意新算法、跨行业稳定迁移尚未证明 |
| 激活后监控 | 独立任务失败可停用受管工作流；满足证据条件时恢复曾通过任务的旧版 | 通用辅助工具不因调用者参数错误自动停用；不重做失败任务 |
| 有界任务队列 | 依赖排序、单次批量或显式 watch 等待新任务 | 没有安装长期无人值守服务，没有完成长时间稳定性验证 |

Skill 是宿主读取的入口；Runtime 是实际运行模型/工具循环的代码；控制平面保存状态和证据。当前没有可视化控制面板，也不需要通过面板创建任务。

## 怎么开始

在已加载Skill的Codex会话中直接给任务，例如：

```text
$agi-like
修复当前项目的报价计算，按 TASK.md 的规则处理类型、折扣和舍入。
现成项目检查可以复用；普通技术问题请自行修复，完成后给出产物和验收结果。
```

Agent应在修改前实际调用本地MCP工具 `native_start`（CLI等价入口为 `native_task.py begin`），继续用当前Codex的模型、工具和上下文；原生hooks记录实际调用，并在准备结束时独立验收，失败自动反馈到同一会话继续修复。状态目录和会话身份由宿主传递，用户无需写合同或创建面板任务。hooks尚未加载时，Agent仍主动调用verify并修复，不能只登记便声称完成。一次性安装/信任步骤及正常使用见 [USAGE.md](USAGE.md)。

独立模型循环入口 `agent_task.py` 保留给其他宿主或明确需要隔离循环的任务；它不再是普通Codex任务的优先路径。连接是否成立以实际hook事件和验收回执为准，不只看安装或metadata。

首次安装可下载ZIP、解压整个 `agi-like/`，在确认宿主 Skill 搜索目录后执行包内安装器；不同版本先备份旧目录至搜索根目录外。当前这台机器已安装到 `C:/Users/Administrator/.agents/skills/agi-like`，其他环境不要直接套用该绝对路径。

开发者可以从main分支导出：

```sh
git clone --branch main https://github.com/lizi-product-studio/solo-dev-delivery.git
cd solo-dev-delivery
python -X utf8 scripts/export_skill.py /absolute/new-parent/agi-like
```

Runtime和辅助工具使用Python标准库；模型访问、宿主工具和业务权限另行提供。目标导出目录与同名ZIP必须不存在。包内包含相对引用、运行时、控制平面、独立验收器、安装器和清单，不包含个人偏好、客户数据、原始聊天或本机旧任务状态。

## 已验证了什么

本次[Codex日常接入试跑](evaluations/native-codex-20261030/README.md)：已安装MCP与用户hooks下，普通代码修复通过9项独立检查，Runtime及控制面均完成，只有1条初始用户消息。机制故障注入曾通过真实Stop反馈完成同会话修复；各轮接入失败也保留。单笔样例不代表普遍提效或统计A/B收益。

| 观察 | 结果 | 不能据此推断什么 |
| --- | --- | --- |
| 真实模型任务恢复与管理 | 有同一任务暂停、跨进程恢复、取消等证据 | 所有宿主、所有崩溃和未知外部操作均可自动恢复 |
| 失败改进后真实模型复用 | 两笔旧流程失败、新候选通过对照，真实模型调用候选交付 | 通用自主算法发现、跨行业稳定提升 |
| 自动准备结算验收 | 无手写任务验收脚本，真实 gpt-6-luna 共5次决策完成；三种篡改均被拒绝 | 任意代码、UI或生产平台都能自动验收 |
| 技能失败监控与恢复 | 实际Runtime/SQLite/独立测试拒绝新版本产物，恢复有合格证据的旧版 | 此项模型决策为脚本替身，不是新一笔真实模型学习收益 |

原始范围与失败记录见 [验证记录](VALIDATION.md)、[受管运行](evaluations/managed-runtime-20261029/README.md)、[失败改进](evaluations/failure-learning-20261029/README.md)、[自动验收准备](evaluations/automatic-review-20261029/README.md)、[监控恢复](evaluations/skill-health-20261029/README.md)。历史对照也保留：[三组18次比较](evaluations/skill-comparison-2026-10-23/REPORT.md)未证明成功率净收益且用量增加，不能把流程完整当作已证实的提效。

## 还缺什么，下一步怎么做

优先补齐通用代码/UI/平台验收、浏览器与外部副作用对账、长期运行监督及 token/金额硬预算；再用冻结任务集验证不同模型、行业的迁移收益。同时收敛一条发布命令和schema迁移注册。具体顺序、交付物和放行标准见 [NEXT_ITERATION.md](NEXT_ITERATION.md)，完整原计划见 [AGI_LIKE_ITERATION_PLAN.md](AGI_LIKE_ITERATION_PLAN.md)。这些工作仍是待办，本次只更新说明，没有恢复完整系统建设。

## 开发与验证

```sh
python -X utf8 -m unittest discover -s portable-agent-kit/tests -v
python -X utf8 -m unittest discover -s tests -v
python scripts/release_check.py skill/agi-like packages/agi-like-1.9.1.zip --checksum packages/agi-like-1.9.1.zip.sha256
```

不同平台的用例范围见 [CI定义](.github/workflows/validate.yml)。发布CI配置为测试后重新导出、安装和比对当前版本ZIP；是否实际通过以对应提交的运行结果为准。本次阶段版本使用定向检查，没有把历史全量结果当作当前全量通过。

完整运行参数见 [AGENT_RUNTIME.md](portable-agent-kit/AGENT_RUNTIME.md)，历史兼容入口见 [Portable Kit](portable-agent-kit/README.md)。任务分流、恢复与证据规范从 [SKILL.md](skill/agi-like/SKILL.md) 按需读取；简单任务不强制建立队列、子Agent或固定测试流程。

## 来源与许可

融合 Superpowers Lite 的相关方法，来源与许可见 [整合说明](INTEGRATION.md)、[第三方声明](THIRD_PARTY.md)。`SOURCE_SNAPSHOT.json` 绑定当前说明、源码、测试等规范文件；ZIP内的 `BUNDLE.json` 绑定分发内容。哈希证明字节一致性，不认证业务效果或作者身份。
