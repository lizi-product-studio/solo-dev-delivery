# DeepSeek Harness 接入

DeepSeek Harness 适合接入 Portable Kit：它采用插件化架构，工具、会话、存储、循环、调度、UI 和 MCP 都可以组合。接入时把 AAP-0.1 内核作为一个本地工具或 MCP 服务暴露给 Harness，让模型只能通过结构化事件推进任务状态。

推荐映射：

| AAP 能力 | DeepSeek Harness 侧 |
| --- | --- |
| `core/state_machine.py` | 本地插件或 MCP 服务 |
| `fs.*`、`shell.exec`、`test.run` | Harness 工具插件 |
| `checkpoint.persist`、evidence | Harness session/storage 插件或项目目录中的受控状态文件 |
| `independent_review` | Agent/Subagent 插件；回执必须绑定不同执行者 |
| `external.authorized` | 单独的授权插件；未知结果返回 UNKNOWN |

使用顺序：

1. 启动 DeepSeek Harness，打开目标项目，加载 `SYSTEM_PROMPT.md`、`PROTOCOL.md` 和项目自己的 `AGENTS.md`。
2. 用 `capability-manifest.json` 逐项探测 Harness 当前真的拥有的工具，不从模式名称猜测。
3. 将 `core/state_machine.py` 包成本地插件或 MCP server。模型提出事件，内核返回下一动作和阻断原因。
4. 先运行包内 conformance tests，再在隔离项目执行一条简单修改→测试→读回→复核流程。

当前包没有伪装成 DeepSeek 官方插件，也没有自动安装或连接账号。DeepSeek Harness 处于持续迭代的开发预览阶段，具体插件 API 需要按当前仓库版本实现；如果只把规则文本放进 Harness，能复用方法，但不能获得状态机的程序门禁。

模型档位仍按 `simple / standard / deep` 传给宿主。Codex 的 `gpt-6-luna` 只属于 Codex 模型映射；DeepSeek Harness 要使用它当前配置的模型或别名，协议和质量门禁保持一致。
