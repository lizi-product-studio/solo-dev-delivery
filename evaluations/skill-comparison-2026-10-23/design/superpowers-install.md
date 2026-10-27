# Superpowers 冻结与隔离安装说明

本次准备只冻结与读取 upstream；没有运行 Codex、模型、Superpowers 工作流、安装脚本或钩子，也没有安装软件包、读取密钥、修改全局配置或主仓库代码。后续评测由新的主 Agent 会话执行。

## 冻结对象

- 仓库：<https://github.com/obra/superpowers>
- 本地：`outputs/skill-comparison-2026-10-23/sources/superpowers`
- HEAD：`2ce8aa15d05bd57213e6f746f953aafb7ded7a8b`
- 发布标签：`v7.0.1`；Codex manifest 与 `package.json` 均为 `7.0.1`。
- 提交时间：`2026-10-21T14:00:49-07:00`。
- 许可证：MIT，`Copyright (c) 2025 Jesse Vincent`。复制技能时保留 LICENSE 与版权许可声明。
- 工作区检查：干净；深度 1 的完整提交快照，15 项技能、84 个 `skills/` 文件。逐文件 SHA256、Git tree 和官方交叉核对内容见同目录 `superpowers-freeze.json`。
- 获取恢复：首次完整 clone 在零字节 pack 阶段等待后终止，Git 自行清除了未完成目录；再次在不存在的路径浅克隆成功。没有重置已有 checkout。

## 当前官方入口

[冻结 README 的 Codex 安装段落](https://github.com/obra/superpowers/blob/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/README.md#codex-app) 对 App 指定侧栏 Plugins → Superpowers → `+`；CLI 指定 `/plugins` → 搜索 Superpowers → Install Plugin。

这个版本没有 `.codex/INSTALL.md` 或 `docs/README.codex.md`，也没有需要执行的 `superpowers-codex bootstrap`。不能沿用旧版的 bootstrap 命令。

[Codex 清单](https://github.com/obra/superpowers/blob/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/.codex-plugin/plugin.json) 只注册 `skills: "./skills/"`，并显式声明 `hooks: {}`；没有 MCP server、developer 指令文件或初始化程序。通用 `hooks/session-start` 属于其他宿主路径，在当前 Codex 清单中被关闭。[官方 OpenAI 插件文档](https://developers.openai.com/plugins/build/plugins) 说明显式 hooks 配置替代默认文件发现。

不要为了评测手动注册通用 `hooks/hooks.json` 或注入完整 `using-superpowers` 正文；这会改变当前 Codex 原生包的行为。

## 本次采用的可携带 Skill 层

每个 run 使用独立 `CODEX_HOME` 和独立用户 profile，并把冻结仓库的完整 `skills/` 内容原样放到该 profile 的 `.agents/skills/superpowers/`。保留子目录与引用文件，复制 LICENSE；不复制 upstream 顶层 AGENTS.md、其他宿主钩子、贡献约定或发行维护脚本作为评测指令。

这是 **upstream v7.0.1 可携带 Skill 层，在相同宿主与工具能力下的对照**，不能称为当前 Marketplace 完整集成排名。[官方技能文档](https://learn.chatgpt.com/docs/build-skills) 支持本地用户技能位置与按 description 隐式激活；独立安装前后的实际发现列表由评测 probe 记录。

只改变 CODEX_HOME 不足以隔离 `$HOME/.agents/skills`。应同时隔离进程的 HOME/USERPROFILE，检查实际发现的技能、AGENTS、rules、config、hooks；run 的 CWD 使用自己的 Git 根，阻断对主仓库父目录的项目配置继承。管理员/系统技能若仍由宿主提供，三组保持相同并记录。

官方公开 catalog 的交叉核对固定在 `openai/plugins@0722921d5542fc593105c27bd52630babd8b8c2a`，其 Superpowers manifest 为 `6.3.0`，并非本次 upstream `7.0.1`。[官方包清单](https://github.com/openai/plugins/blob/0722921d5542fc593105c27bd52630babd8b8c2a/plugins/superpowers/.codex-plugin/plugin.json) 也有 `hooks: {}`。

[upstream Codex 打包脚本](https://github.com/obra/superpowers/blob/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/scripts/package-codex-plugin.sh) 会从旧官方包补齐 `skills/*/agents/openai.yaml`。已读取公开包的 14 份既有元数据，全部只有 `interface.display_name` 和 `short_description`，没有 policy、dependencies 或额外指令；新增 `diagnosing-superpowers` 在这个旧公开包中为 404。没有把旧版元数据拼入冻结源，也没有运行发布脚本。此处 UI metadata 的缺省不改变技能正文；不据此宣称商店最新版与 upstream 一致。

## 主 Agent 激活与验收

[主入口](https://github.com/obra/superpowers/blob/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/skills/using-superpowers/SKILL.md) 是 `using-superpowers` 的元数据，其 description 指定会话开始时使用。应开启新的、独立顶层 Codex 会话，正常提交该轮任务，由宿主先展示技能元数据，再观察主 Agent 是否读取这个入口及它引用的 `references/codex-tools.md`。

正文存在 `<SUBAGENT-STOP>`：被派发执行具体任务的子 Agent 应忽略该入口。不能通过本评测控制 Agent 的 `spawn_agent` 派发参赛者来替代新主会话，也不能把“子 Agent 没有启动 Superpowers”计为技能失败。Superpowers 自己在正式工作流中生成的子 Agent 是另一回事。

自然触发对照使用三组相同的任务文本，不额外提示某组启用工作流；记录 skills discovery、首次读取、后续技能读取与行动。若需要显式 `$using-superpowers` 作为诊断，应独立标记为 opt-in/probe，不能混入自然触发得分。以上只是准备与验收条件，本次没有执行模型验收。

## 工具适配与已知差异

[Codex 适配文件](https://github.com/obra/superpowers/blob/2ce8aa15d05bd57213e6f746f953aafb7ded7a8b/skills/using-superpowers/references/codex-tools.md) 要求 multi-agent 支持，并明确实际工具列表优先。

| 能力 | 评测适配方式 |
| --- | --- |
| Skill 发现与读取 | 使用宿主 native skill discovery 与文件读取；不增加自制 Skill wrapper 或旧版 bootstrap CLI。 |
| Shell、搜索、读写、补丁、计划 | 三组使用同一宿主工具和权限，记录实际列表。manifest 不要求额外 MCP 或依赖包。 |
| 子 Agent | 仅在 run 独立配置内启用 `features.multi_agent`，三组能力一致。此准备会话的 V2 接口为 spawn_agent、send_message、followup_task、list_agents、wait_agent、interrupt_agent；目标 run 仍以其真实工具列表为准。 |
| 修复轮次 | 若实际提供 V2，复用 followup_task；V2 不提供 close_agent，不捏造同名接口。 |
| 派发上下文与模型 | upstream 推荐隔离子上下文；真实 schema 和更高优先级宿主指令决定可用参数。所有组使用相同可用模型、预算和工具。 |
| 工作树与收尾 | 保留技能对 Git/worktree 的只读探测；权限与可用宿主行为三组一致，不能默默替某一组扩大权限。 |

已发现文档漂移：upstream codex-tools 声称 full-history fork 可传 model/effort，但本准备会话的工具 schema 禁止在 full-history fork 上传这些覆盖值。保留 upstream 正文，按真实 schema 操作并在发生时记录兼容性问题，不静默补丁技能文本。

[官方配置文档](https://learn.chatgpt.com/docs/config-file/config-advanced) 确认 CODEX_HOME 是本地状态目录；它与用户技能目录是不同层。身份、凭据、已有全局插件与本次冻结工作无关，本准备没有访问它们。
