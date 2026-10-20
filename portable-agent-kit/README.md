# Portable Solo Delivery Kit

这是栗子软件交付系统的跨 Agent 平台封装。它把平台无关的部分固定为 **AAP-0.1（Agent Adapter Protocol）**：任务契约、阶段、能力探测、幂等、检查点、证据、失败分类和交付阻断。

它可以被 DeepSeek Harness、zcode、KimiCode、其他本地 Agent、脚本 Agent 或较弱模型使用，前提是宿主能把 JSON 请求转给适配器，并提供声明过的工具能力。平台名称和模型名称不会自动获得权限或质量；没有浏览器、持久化、读回或身份能力时，内核会降级为计划、源码检查或阻断，不会把缺失能力猜成已完成。

对这三个平台，当前能确认的是接入方式，而不是它们在每个版本中的实际工具清单：

| 宿主 | 可复用部分 | 接入前必须核对 |
| --- | --- | --- |
| DeepSeek Harness | `SYSTEM_PROMPT.md`、AAP JSON、状态机、证据门禁 | 是否能运行受控工具、保存 checkpoint、把 tool result 原样回传 |
| zcode | 同上 | 是否能提供工作区读写、测试、候选版本和持久状态 |
| KimiCode | 同上 | 是否能把外部动作和未知结果交给适配器，而不是让模型自行声称成功 |

因此不是把 Codex 的 skill 文件直接复制过去，而是让平台实现 `adapters/ADAPTER_CONTRACT.md`。只支持聊天文本的平台也能使用需求分析和交付检查部分，但不能宣称完成代码、浏览器、部署、备案或真实发布。

Cursor 可直接使用 `integrations/cursor/AGENTS.md`，或将 `integrations/cursor/solo-delivery.mdc` 放入目标项目的 `.cursor/rules/`。Kimi Code 可参考 `integrations/kimi-code/README.md`，从 CLI、桌面端、VS Code、MCP 或 Agent Skill 入口接入。DeepSeek Harness 可参考 `integrations/deepseek-harness/README.md`，把内核作为本地插件或 MCP 服务接入。其他 IDE Agent、CLI Agent 和自建 Harness 可按 `integrations/generic/README.md` 接入；平台的规则文件只负责把 Agent 引到协议和内核，不能代替宿主工具、状态存储和证据回执。

## 包内内容

- `core/state_machine.py`：无模型、无网络、无厂商依赖的状态机和交付门禁。
- `schemas/aap-0.1.json`：请求信封的最小结构。
- `SYSTEM_PROMPT.md`：给弱模型的短指令，强调单步、证据和升级边界。
- `adapters/example_adapter.py`：JSONL 参考适配器。真实平台只需替换能力探测、工具执行和持久化，不改核心规则。
- `examples/`：能力清单、简单任务和未知结果示例。
- `tests/`：跨平台一致性测试。

## 适配一个新平台

适配器至少实现四件事：

1. 将宿主工具映射为 `fs.read`、`fs.write`、`shell.exec`、`test.run`、`browser.open`、`browser.act`、`artifact.hash` 等结构化调用。
2. 返回三态能力 `supported / unsupported / unknown`，不要从模型名称猜能力。
3. 持久化当前 state、checkpoint、evidence 和 request idempotency key，重启后可恢复。
4. 把外部操作、身份核验、付款、发布和未知结果原样返回给内核，让内核阻断或要求人工处理。

平台可以没有子 Agent、浏览器或后台调度：简单任务进入单 Agent 模式；没有浏览器时界面验收标为未验证；没有 scheduler 时不声称会后台监控；没有外部发送能力时只生成待发送材料。质量门槛不因模型较弱而降低。

## 运行示例

```sh
python3 examples/demo.py
python3 -m unittest discover -s tests -v
```

`state_machine.py` 是可移植内核，当前 Codex 系统继续使用自己的原生委派、SQLite 队列和审核回执；本包不会改动它们。导入到其他平台时，必须把 `owner`、授权、项目范围、外部动作政策和实际证据替换成该项目自己的值，不复制校服项目或本机路径。

## 真实能力边界

这个包能让较弱的 Agent 按相同的步骤、门禁和证据格式工作，降低漏步骤、误报完成和未知结果重试的概率。它不会凭空增加产品判断、事实知识、账号权限、备案资格、服务器账号或真实平台操作能力。跨平台是否达到可交付标准，要用同一组 conformance tests 和真实项目结果验证。
