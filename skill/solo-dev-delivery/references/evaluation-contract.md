# 评测合同与指标

用于把不同任务、Skill 版本、宿主和模型的结果放进同一份可复核记录。它是评测工具的输入格式，不是普通业务任务的强制流程。

## 合同最小结构

`schema_version` 固定为 `0.13`；合同包含 `contract_id`、`objective`、`arms`、`tasks`、`repetitions`、`metrics` 和 `freeze`。每个任务至少声明 `objective`、`expected_observations`、`risk`、`required_capabilities` 和 `stop_conditions`。`freeze` 至少包含 `seed` 与独立验收器哈希。任务来源、Skill/包版本、宿主、模型、提示词和评估器哈希应在冻结记录中保存。

每次运行写一行 JSON 回执，包含 `trial_id`、任务/臂/重复编号、终态、语义分流、动作边界、证据状态、证据引用、首轮/最终成功、人工纠正、阻塞与交接、恢复、旧状态副作用、未知结果误报、耗时和用量。缺失值写 `null`，不能填零。

执行者不能读取另一组结果或私有判分器。成功回执必须有实际证据引用；无证据、空测试、旧版本、错误环境、未终态或不可读记录都不能汇总为完成。

## 与现有系统的映射

| 0.12 语义层 | 现有执行层 |
| --- | --- |
| `QUICK` | `QUICK_PATCH` |
| `BOUNDED` | `BOUNDED_CHANGE` |
| `GOVERNED` | `GOVERNED_DELIVERY` |
| `MAINTENANCE` | 项目巡检/持续改进语义，不新增队列状态 |
| `DONE` | 仅在现有 gate/challenge 或真实宿主观察满足后记录成功 |
| `NEEDS_EVIDENCE` | `INCOMPLETE` 或待补证，不是 PASS |
| `BLOCKED` | gate/challenge 的阻断或未知保锁 |
| `BUSINESS_DECISION` | 需要用户/有权人决定，不由执行者自批 |
| `DEFER` | capability probe 的 `NEEDS_CONTEXT` 或显式阻断 |

`eval_contract.py validate` 只校验合同和回执结构；`summarize` 只做描述性统计，缺失样本默认返回 `INCOMPLETE`。它不调用模型、不判断业务语义、不授予权限，也不替代现有交付门禁。

语义层和现有执行层的映射可以直接查看：

```sh
python portable-agent-kit/scripts/eval_contract.py map --route GOVERNED --boundary STOP --evidence DONE
```

建议指标包括：首轮成功、最终成功、用户纠正次数、可恢复阻塞交接率、交接准确率、恢复完成率、独立进度、旧状态误执行率、未知结果误报率、耗时和用量。高风险副作用和未知误报应以零为目标；小样本先报告描述性结果，不宣称统计显著或普遍领先。
