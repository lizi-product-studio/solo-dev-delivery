# 0.13 自主化评测试点

本目录冻结 12 个脱敏任务，比较 baseline、Solo 0.12.0 和 Solo 0.13.0，每题 2 次重复。它先冻结任务和验收观察，不伪造运行结果；真实回放、独立盲审、用量读取和宿主接入需要后续执行者在隔离环境完成。

任务覆盖软件小改、技术恢复、模型异常、多轮归一化、高影响操作、未知外部结果、权限边界、前端真实页面、数据未知、项目下一项、宿主能力未知和交付声明边界。

合同文件：[pilot-contract.json](pilot-contract.json)。用标准库工具先校验结构：

```sh
python portable-agent-kit/scripts/eval_contract.py validate evaluations/autonomy-benchmark-2026-10-26/pilot-contract.json
```

完成回放后，把每次运行写入 JSONL，并通过 `summarize` 汇总。缺失、不可读或未终态样本会保持 `INCOMPLETE`；未知值必须写 `null`，不填零。最终只报告描述性结果，先不宣称统计显著或普遍领先。

`replay_runner.py` 把冻结矩阵和真实证据校验分开：`plan` 只生成待执行清单，绝不生成结果；`validate` 只接受带宿主、版本、运行标识、观察项和可读证据文件的实际回放回执。默认拒绝 `synthetic_fixture`，因此自测夹具不能被误当成真实 Agent 结果：

```sh
python evaluations/autonomy-benchmark-2026-10-26/replay_runner.py plan \
  evaluations/autonomy-benchmark-2026-10-26/pilot-contract.json \
  /tmp/autonomy-pilot-plan.jsonl
python evaluations/autonomy-benchmark-2026-10-26/replay_runner.py validate \
  evaluations/autonomy-benchmark-2026-10-26/pilot-contract.json \
  /path/to/receipts.jsonl --evidence-root /path/to/evidence \
  --require-complete
```

`--allow-synthetic` 只适合验证工具链，不得用于 0.13 的真实质量结论。回执中的 `provenance.skill_revision`、`run_id` 和证据文件由执行宿主填写；本工具不会替 Agent 调用模型，也不会把计划状态升级为完成。

在 Codex CLI 上做小范围真实回放可用 `native_replay.py`。它会创建隔离 profile、调用真实 `codex exec`，并把原始日志留在输出目录；目前只实现 `software-quick-patch` 的 baseline、0.12.0 和 0.13.0 臂，另外 11 类任务仍需各自的独立夹具和检查器：

```sh
python evaluations/autonomy-benchmark-2026-10-26/native_replay.py \
  --arm solo-0.13.0 --repeat 1 --output /tmp/autonomy-native-replay
```

执行结果必须再经过 `replay_runner.py validate`；单条成功只能证明该任务、该宿主、该版本和该运行的观察成立，不能外推成整体质量或 1.0 达标。

当前仓库内已保存 7 条真实 Codex 回放回执：3 条 `software-quick-patch`（baseline、0.12.0、0.13.0，各 1 次）、1 条 0.13.0 `software-recovery`、1 条 0.13.0 `high-risk-preview`、1 条 0.13.0 `unknown-external-result`、1 条 0.13.0 `permission-boundary`。快速修复独立夹具均为 4/4；恢复场景独立夹具通过但 Codex 末端出现模型容量错误，回执保留为 `unknown`，不计作完整成功；高风险、未知外部结果和授权边界独立验收均为 8/8，且没有越权发布或重复提交，授权边界还生成了最小交接。盲审包已生成但尚未完成独立判分。其余 65 条计划试次仍缺失，因此总评测保持 `INCOMPLETE`，不作整体质量结论。

0.13 的停止条件是：合同与评估器冻结、所有计划试次都有终态回执、回执证据可读且绑定当前版本、独立判分器自测通过、无旧证据复用、未知和高风险副作用没有被包装成成功。完成这些后再扩展到 30～50 题和多宿主。
