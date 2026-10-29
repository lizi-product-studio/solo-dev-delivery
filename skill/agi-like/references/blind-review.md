# 独立盲审包

0.13 试点的自动汇总只说明回执字段是否完整，不能替代对实际证据的独立判分。`blind_review.py` 将盲审包、候选版本和证据绑定，拒绝把旧回执、旧候选或未读回证据当作当前结果。

0.13 回执必须提供 `contract_sha256`、`source_revision`、`candidate_sha256`，以及对象形式的 `evidence_refs`。每项证据含 `id`、`sha256`、`trial_id`、`contract_id`、`source_revision`、`candidate_sha256`。

生成给评审人的包时，只输出任务、观察项、回执摘要和证据摘要；映射文件单独保存给汇总者，里面才包含 `arm_id`：

```sh
python portable-agent-kit/scripts/blind_review.py make-packets \
  evaluations/autonomy-benchmark-2026-10-26/pilot-contract.json \
  evaluations/autonomy-benchmark-2026-10-26/receipts.jsonl \
  evaluations/autonomy-benchmark-2026-10-26/blind-packets.jsonl \
  --mapping evaluations/autonomy-benchmark-2026-10-26/blind-mapping.jsonl
```

评审人提交每个包的 JSONL 回执，逐个观察项填写 `PASS`、`FAIL` 或 `UNKNOWN`。`PASS`/`FAIL` 必须引用包内证据；`UNKNOWN` 必须写原因。验证时会检查回执摘要、合同评估器哈希、候选版本和证据来源是否一致，并拒绝评审文件泄露 `arm_id`、`source_revision` 或 `candidate_sha256`：

```sh
python portable-agent-kit/scripts/blind_review.py validate \
  evaluations/autonomy-benchmark-2026-10-26/pilot-contract.json \
  evaluations/autonomy-benchmark-2026-10-26/receipts.jsonl \
  evaluations/autonomy-benchmark-2026-10-26/reviews.jsonl
```

缺少任意试次会返回 `INCOMPLETE`；只有明确使用 `--allow-incomplete` 才允许继续查看部分结果。工具不执行模型、不读取认证，也不把摘要文字推断成业务结论。
