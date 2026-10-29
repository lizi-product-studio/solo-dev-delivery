# 自升级与长期变强

Skill 不会因为被调用就自动改写自己。长期变强必须由宿主保存真实回执，再按“记录 → 根因去重 → 提案 → 独立审核 → 回归 → 灰度 → 激活读回或回滚”循环推进。升级候选的状态机在 `self-improvement/2` 中，不能用同一任务的重复日志或命令行字符串伪造多次证据。

便携入口是 `scripts/self_improvement.py`：

```sh
python scripts/self_improvement.py record --ledger control/experience.jsonl --input run-receipt.json \
  --evidence-root evidence/
python scripts/self_improvement.py propose --ledger control/experience.jsonl \
  --candidate 1.1.0-rc1 --change-summary "修复重复出现的工具调用失败" \
  --patch-ref changes/patch.diff --evidence-root evidence/ --output control/proposal.json
python scripts/self_improvement.py gate --proposal control/proposal.json \
  --independent-review control/review.json --regression control/regression.json \
  --canary control/canary.json --rollback-plan control/rollback-plan.json \
  --output control/release-candidate.json
python scripts/self_improvement.py wake --run-id run-1 --budget 2 --steps-used 0 \
  --max-steps 3 --authorization-scope "local repository" \
  --allow collect --forbid publish --output control/wake.json
```

经验账本只接受带 `run_id`、任务、候选版本、终态和带 SHA-256 的证据引用的回执。失败必须有分类、根因和原始消息；`unknown` 保持未知。提案只有在同一任务/根因/候选下出现来自不同 run 的重复证据时生成，且提案不直接改源码。

发布候选必须读取并绑定独立审核、回归、灰度和回滚计划回执；每份回执必须包含相同 `proposal_id`、候选版本和带哈希证据。任何 `FAIL`、`INCOMPLETE` 或 `UNKNOWN` 都阻止候选发布。回滚只写回执并要求激活读回，不删除原始证据。

宿主仍需提供持久状态、唤醒、进程管理、预算和人工接管。这个入口不创建后台调度器、不重试外部动作、不扩大账号权限；它证明的是有界自升级流程，不是无人值守生产自治。

`wake` 是宿主每次唤醒时的有界决策点：预算耗尽、最大步数、授权缺失或外部结果未知都会返回 `PAUSED`，宿主应保存回执并停止；`READY` 只允许进入一次收集与分类动作，不能据此连续循环。
