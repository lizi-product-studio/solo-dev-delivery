# 五项薄弱能力的质量合同

这些合同把跨平台、界面、系统设计、自主运行和成本收益转成可核对的输入；只在任务需要时使用。`PASS` 只代表声明字段与证据齐全，`INCOMPLETE` 代表缺失或未知，`FAIL` 代表明确失败，均不等价于生产保证。

统一入口：

```sh
python scripts/quality_contract.py validate --kind ui --input ui-review.json
python scripts/quality_contract.py validate --kind system --input system-design.json
python scripts/quality_contract.py validate --kind host --input host-adapter.json
python scripts/quality_contract.py validate --kind autonomy --input cycle.json
python scripts/quality_contract.py compare --results results.jsonl --min-valid-pairs 3
```

## 跨平台安装与适配

宿主回放继续使用 `install → load → invoke → route → verify` 五阶段。`host-adapter/1` 额外要求宿主身份、版本、平台、每项能力的三态、五阶段观察和证据来源；任一能力为 `unknown` 或阶段缺失，都只能得到 `INCOMPLETE`。因此“文件复制成功”不会再被描述成“平台已兼容”。

每次真实回放应保存宿主版本、Skill/包哈希、能力快照哈希、调用命令或界面观察，以及最终读回。没有实机记录的平台保留 `unknown`，不因为另一个平台通过而外推。

## 前端设计质量

`ui-quality/1` 要求至少两个真实目标视口、真实长度内容、主路径的功能/视觉/无障碍三项观察、空/错/成功状态和证据引用。`ui-quality/2` 在此基础上还要求键盘、焦点、失败恢复、未保存恢复四项交互观察及各自证据引用，并要求空/错/成功三种状态全部覆盖。可以选桌面与窄屏、两个桌面尺寸或真实设备，取决于目标用户；不固定像素模板。解释性文字占比过高会进入 `INCOMPLETE`，但不能靠删掉错误、权限、未保存和失败提示来凑 PASS。

验收顺序是：真实内容与任务定位 → 主操作与状态读回 → 视觉层级、密度、遮挡和裁切 → 键盘/焦点/语义 → 失败与未保存恢复。截图、DOM 或代码扫描只能支持对应部分，不能单独证明“好看”。

## 后端、数据库与高并发

`system-design/1` 要求先写出流量口径（峰值/突发 RPS、并发、负载）、SLO（可用性、P95 延迟、错误率）和容量模型（单实例吞吐、实例数、余量、由 RPS×延迟推导的并发），再逐项声明幂等、背压、有限重试、授权与审计、迁移回滚、可观测性、压测和恢复演练。`system-design/2` 还要求带请求数、并发、P95、错误率的压测回执，以及包含回滚读回的恢复演练回执。缺一项就是 `INCOMPLETE`；明确失败就是 `FAIL`。

这套合同不替模型选择数据库，也不从 DAU 猜并发。容量设计要把请求路径、热点、连接池、队列、缓存、数据库索引/锁、降级和恢复结果绑定到实测环境；没有压测和故障演练时只能交付设计草案。可用 `scripts/load_report.py` 汇总宿主已经保存的 JSONL 观测：

```sh
python scripts/load_report.py --input observations.jsonl --concurrency 8 \
  --p95-target-ms 300 --max-error-rate 0.01 --min-requests 1000 \
  --window-seconds 60 --output load-report.json
```

它只重算观测值并保留阈值缺口；没有并发、延迟和错误率门槛时返回 `INCOMPLETE`，超出门槛返回 `FAIL`，不会把本地报告写成生产容量证明。

对本地 SQLite 证据，可先只读检查结构，再用绑定参数查询：

```sh
python scripts/db_inspect.py inspect --db runtime/queue.sqlite3
python scripts/db_inspect.py query --db runtime/queue.sqlite3 \
  --sql "SELECT status, COUNT(*) FROM tasks GROUP BY status" --limit 100
```

该入口拒绝写语句、多语句和超过上限的结果；远程数据库、生产权限和敏感数据处理仍由宿主授权与数据库工具负责。

## 自主经营与长期运行

`autonomy-cycle/1` 和 `autonomy-cycle/2` 是 [`autonomy-core.md`](autonomy-core.md) 的合同化输入：它们补充有界账本、最大步数、检查点和恢复读回字段，不另建一套自主状态机。

长期运行需要宿主提供唤醒、持久状态和进程管理；Skill 只提供有界的决策合同。超过预算、遇到身份/权限或业务决定、外部结果无法对账时必须停在 `INCOMPLETE` 或交接，而不是循环重试。

## 成本与质量收益

`ab-compare/1` 只对两臂都 `completed + task_success` 的完整配对计算质量、耗时、Token 和可选的美元成本差值。传入 `--require-cost-evidence` 时，每个有效配对必须带 `cost_usd`，或同时带 `usage.total_tokens` 与 `pricing_usd_per_million_tokens.total`；缺失时输出 `INCOMPLETE`。容量错误、连接错误、超时和策略阻断保留为 censored；低于 `min-valid-pairs` 时输出 `INCOMPLETE`，禁止写成“更快、更省或更好”。

每次对照应冻结模型、推理档位、工具、提示词变体、预算、任务契约和独立判分器，并保留原始会话日志。模型容量错误由 `scripts/capacity_policy.py` 分类为可延迟重试；协议冻结时不偷偷换模型，非冻结的普通任务才可由宿主按授权选择备用模型。先报告有效样本数和失败原因，再报告均值或中位数；没有同口径基线就不归因。`quality_contract.py compare` 只做机械汇总，不代替独立判分。

当有效配对少于 `min-valid-pairs`，或结果中出现容量失败时，输出中的 `supplemental_evidence_plan` 会列出需要补的有效配对数、容量失败任务和可执行步骤。步骤要求保留原始失败回执，按冻结的同一模型与任务协议在有界恢复后重试，并在两臂都 `completed + task_success` 前继续保持 censored；计划最后给出重新运行 `compare` 的命令。容量失败永远不会被计入 `valid_completed_pairs`，也不会被当作质量、耗时或 Token 优势的证据。
