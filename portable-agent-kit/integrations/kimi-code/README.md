# Kimi Code 接入

Kimi Code 可以从 CLI、桌面端或 VS Code 使用，也提供 MCP、Agent Skills 和 Agent/Subagent 定制入口。把 Portable Kit 作为项目级技能或规则资料接入，再把宿主工具结果映射到 AAP-0.1。

建议接入顺序：

1. 将 `SYSTEM_PROMPT.md` 和 `PROTOCOL.md` 作为 Kimi Code 项目的基础规则或技能资料。
2. 将 `core/state_machine.py` 放在项目可读的工具目录，或由本地 MCP/脚本工具调用它。模型不直接修改 state，只提交结构化事件。
3. 用 Kimi Code 的文件、终端、测试、浏览器/MCP 工具完成适配器，能力清单写入 `capability-manifest.json`。
4. 每个阶段保存 state、checkpoint、evidence 和 request idempotency key；Kimi 会话恢复不等于交付状态恢复，持久状态必须由适配器保存。
5. 先跑包内 conformance tests，再用隔离项目验证真实读回和交付阻断。

Kimi Code 官方文档当前列出 CLI、桌面端、VS Code、MCP、Agent Skills 和 Agent/Subagent，并说明可执行代码阅读、文件编辑和命令操作。实际权限、模型池、上下文和会话恢复以你的版本和账号为准；适配器必须通过 probe 取得结果，不能猜测。

资源调度使用角色档位，而不是跨平台硬编码模型名：

- `simple`：宿主最低成本模型，单 Agent，定向检查。
- `standard`：常规实现和测试。
- `deep`：支付、身份、数据完整性、架构和独立复核。

在 Codex 中，`gpt-6-luna` 可以作为 simple 或部分 standard 的低成本子 Agent；在 Kimi Code 中应映射到 Kimi 自己可用的低成本模型或配置别名。两者都必须保持同样的证据和交付门禁。
