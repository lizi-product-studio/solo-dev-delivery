# 适配器契约

这个文件是给 DeepSeek Harness、zcode、KimiCode 或其他 Agent 宿主的接入说明。它不假设某个平台的 API 名称；平台只要能完成下面的映射就能使用同一套交付逻辑。

## 宿主提供给内核的能力

启动时发送一份能力清单，每项只能是 `supported`、`unsupported` 或 `unknown`：

| 能力 | 用途 |
| --- | --- |
| `fs.read` / `fs.write` | 读取和修改工作区 |
| `shell.exec` / `test.run` | 运行受控命令和测试 |
| `git.read` / `git.write` | 读取候选版本、保存补丁或提交 |
| `browser.open` / `browser.control` / `visual.capture` | 实际页面和设备验收 |
| `checkpoint.persist` / `pause_resume` | 中断恢复和交接 |
| `evidence.hash` | 证据完整性校验 |
| `independent_review` | 启动不同执行者的只读复核 |
| `external.authorized` | 已授权的外部写操作 |

宿主没有某项能力时，内核会降级或阻断：

- 只有聊天文本：可做需求分析、方案和待办材料，不能声称已修改代码、测试或部署。
- 能读写文件但没有浏览器：可开发和做源码/API检查，界面验收必须保持 `UNVERIFIED`。
- 没有持久化：不能宣布长任务可恢复；先让宿主保存 state 和 checkpoint。
- 没有外部授权：可以准备域名、服务器、备案、上架和部署材料，不能购买、提交、发布或发送。
- 没有调度器：不能声称后台自动监控；在交付记录里写下一次检查点。

## 工具调用映射

宿主将自身工具映射为统一名字，并把调用结果封装为：

```json
{
  "tool_call_id": "call-001",
  "tool": "test.run",
  "status": "SUCCEEDED",
  "exit_code": 0,
  "artifacts": [],
  "observed_state": {}
}
```

`UNKNOWN` 表示副作用是否发生无法确定。适配器不能将超时改写成 `FAILED` 或 `SUCCEEDED`。内核会要求 readback/reconcile，防止弱模型重复下单、重复发布或重复写入。

## 模型和资源降级

模型名称、温度或思考档位只是宿主元数据，不是能力证明。主控循环遵循：

1. 任务简单且边界清楚：单 Agent、短步骤、定向验证。
2. 任务复杂或有多个独立组件：分成可验收单元；宿主有子 Agent 才并行，没有就串行。
3. 连续两次确认是推理失败，才允许换更强模型或提高思考强度。
4. 网络、权限、认证、依赖、额度和业务事实错误先解决条件，不能靠升级模型。
5. 仍无法证明时返回 `BLOCKED` 或 `NEEDS_INPUT`，保留原始失败和部分产物。

因此，弱模型可以使用同一框架完成简单项目，并在复杂项目里按检查点逐步推进；它不会因为包装层而自动获得产品判断、事实知识或平台权限。
