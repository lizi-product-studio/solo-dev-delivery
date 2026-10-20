# AAP-0.1 最小协议

AAP（Agent Adapter Protocol）让不同 Agent 宿主共享同一套交付语义。请求和结果都使用 JSON，既可以通过标准输入输出、函数调用，也可以放进消息队列或 HTTP；传输方式不属于协议核心。

## 请求信封

```json
{
  "protocol": "aap/0.1",
  "request_id": "r-001",
  "op": "tool_result",
  "task_id": "T-001",
  "revision": 1,
  "contract_hash": "sha256...",
  "candidate": "git-sha-or-artifact-hash",
  "idempotency_key": "i-001",
  "payload": {}
}
```

必填回显字段阻止旧任务、旧候选或旧 Agent 的迟到结果写回当前任务。相同 `idempotency_key` 必须返回同一个结果，不能重复副作用。

## 阶段和状态

阶段是 `assess → work → verify → review → deliver`，状态单独表示运行情况：`READY`、`RUNNING`、`PAUSED`、`BLOCKED`、`UNKNOWN`、`COMPLETED`、`FAILED`、`CANCELLED`、`NEEDS_INPUT`。

计划检查点只返回 `decision_required`，不因为固定时间、安静或预算检查点自动终止正常执行者。暂停需要 checkpoint 和执行者停止证据；恢复需要同一 revision、契约哈希、候选和已核实归属。

## 证据和交付

每份证据绑定 task、revision、contract_hash、candidate、producer、时间和 SHA-256，并说明具体 claim。对于用户功能，适用的最小链路是：

`用户入口 → 实际动作 → 业务结果 → 持久化/读回 → 显示状态`

模拟、mock、接口成功、构建成功和截图只能证明各自范围。缺失浏览器/设备/真实资金/平台审核证据时，不能升级为真实平台通过。开放 finding、未覆盖验收、未知副作用、活动执行者、候选变化或证据哈希变化都会阻断交付。

## 人工边界

模型可以分析、编程、测试、准备服务器/域名/备案/上架材料并核对回执。付款、实名、人脸、短信、备案提交、应用发布、客户承诺和不可逆数据动作必须由明确授权与宿主能力共同支持；需要本人身份时返回 `NEEDS_INPUT`，不能伪造完成。
