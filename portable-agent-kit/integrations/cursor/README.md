# Cursor 接入

Cursor 的 Agent 可以读取项目根目录的 `AGENTS.md`，也可以读取 `.cursor/rules/*.mdc` 项目规则。把下面任一模板复制到目标代码仓库，再把 `ROOT`、项目授权和验收环境替换成真实值。

- 简单项目：复制 `AGENTS.md`。
- 需要按文件范围或手动启用：复制 `solo-delivery.mdc` 到目标项目的 `.cursor/rules/`。
- 需要实际执行：让 Cursor 的终端、文件、测试和浏览器/MCP 工具通过一个适配器调用 AAP-0.1；规则本身不能伪造工具结果。

Cursor 文档支持这两类项目规则；本包只提供接入模板，不假设你的 Cursor 版本、模型或 MCP 工具清单。完成接入后运行本包 `tests/`，再用一个隔离项目验证动作、读回和交付阻断。
