# AAP-0.1 信封与 0.2 状态门禁

请求仍使用 `aap/0.1` JSON 信封。本包 0.2.0 加严本地状态机语义，不代表所有平台均已实现或验证这些操作。传输由实际宿主提供；本包不附 HTTP、消息队列或调度适配器。

1.1.0 增加 `delivery-contract/1` 合同规范化和 SQLite 控制平面。控制平面为状态机提供持久任务、事件日志、租约和恢复读回；它不替宿主执行工具，不把租约过期当作安全接管，也不把模型自报的证据变成 PASS。

## 请求信封

```json
{
  "protocol": "aap/0.1",
  "request_id": "r-001",
  "op": "evidence",
  "task_id": "T-001",
  "revision": 1,
  "contract_hash": "<64-char-sha256>",
  "candidate": "git-sha-or-artifact-hash",
  "idempotency_key": "i-001",
  "payload": {}
}
```

状态带 `state_version: 2`。旧状态版本不会自动迁移；操作时返回 `STATE_UPGRADE_REQUIRED`，须按当前契约重新建立状态并保留旧记录供人工处理。重复的幂等键只接受相同请求内容，内容不同时返回 `IDEMPOTENCY_KEY_CONFLICT`。

操作包括 `probe`、`start`、`observe`、`tool_result`、`evidence`、`reconcile`、`open_finding`、`resolve_finding`、`checkpoint`、`pause`、`resume`、`phase`、`finish`、`cancel`。`probe` 只规范化宿主输入，不代表实际探测。必要能力不明确时须阻断；具体外部权限由宿主强制执行。

## 状态与逐项证据

状态为 `READY`、`RUNNING`、`PAUSED`、`BLOCKED`、`UNKNOWN`、`COMPLETED`、`FAILED`、`CANCELLED`、`NEEDS_INPUT`。必须先 `start`，再完成每个契约验收项。每份验收证据须带有效 `check_id`、环境、当前 task/revision/contract/candidate 绑定，并由可信宿主传入 `verify_evidence` 回调重新核验；模型提交的 `PASS`、`verified` 或哈希字符串不构成证明。缺少、失败或不可验证的任一验收项都会阻断 `finish`。新的同项证据取代旧证据，失败重验仍须实际重新执行检查。

未知副作用先用可验证的读回证据执行 `reconcile`，不能盲目重试。Finding 必须关联验收项；`resolve_finding` 要求 finding 打开之后产生、当前且验证通过的相关证据。计划检查点只要求决策，不自动停止执行者；暂停需 checkpoint 和停止证明。

## 验收边界

按任务风险核对实际入口、动作、业务结果、持久化/读回及显示状态。文本、接口、构建、mock 或截图只能支持其实际观察到的部分。宿主须自行认证 evidence URI 的内容、SHA-256、来源与验收结果；核心只验证字段绑定并调用宿主回调，不自行读取 URI。缺少设备、浏览器、身份、真实资金或平台审核证据时，保持未验证。

## 授权

分析、编码和准备材料不等于授权外部行动。付款、实名、人脸、短信、备案提交、发布、客户承诺及不可逆数据动作，必须同时具备具体授权和真实宿主能力；需要本人身份时返回 `NEEDS_INPUT`。跨业务方案可复用判断、来源核对和验证方法，软件任务队列不自动适用于非软件业务。
