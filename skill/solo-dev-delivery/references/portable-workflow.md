# 跨业务工作与本地检查

先确定专业交付标准，再用检查器核对能机械验证的部分。非软件业务不自动进入软件开发队列。默认用宿主工具直接工作，只有多步骤易漏、需要恢复或验收证据绑定时才登记本地 session。

## 最小交接与弱模型模式

每次只交给执行者：当前一个子目标、原始输入位置、必须保留的事实/边界、输出路径、一个可观察验收。执行后先读工具结果或产物，再给下一步。缺信息时调查或列为假设，不凭空补经营事实。

| 场景 | 关键判断 | 可机械检查的部分 | 仍需实际专业验证 |
| --- | --- | --- | --- |
| 软件迭代 | 用户任务和现有架构 | 配置/输出、构建/测试结果由真实工具给出 | 体验、权限、异常、部署与真实使用结果 |
| 数据分析 | 原始口径、去重、样本和因果限制 | 源文件哈希、金额汇总、输出数据 | 指标是否合理、结论是否过度推断 |
| 营销/经营方案 | 人群、渠道、预算、事实、成功标准 | 预算加总、约束字段、必备内容 | 创意效果、市场事实、承诺和实际投放 |

这不是三个行业模板的全集。领域陌生或机械验证不足时保留未验证项，按实际任务使用专业工具或审查者。

## 可运行入口

导出包里相对 Skill 目录运行 `python3 scripts/session.py`；本机完整系统里运行 `python3 ROOT/portable-agent-kit/scripts/session.py`。Python 3.9+，标准库，无需安装包、模型 API 或平台 SDK。

受信主 Agent/宿主先按已确认需求准备契约，`init` 固定当前输入摘要。后续不能让执行模型改写验收标准、输入或 session 来把失败变通过。该目录不是安全沙箱，同机全权限操作者能绕过；宿主仍需管理写权限与真实授权。

```sh
python3 scripts/session.py init --workspace /absolute/work --contract /absolute/contract.json --session /absolute/control
python3 scripts/session.py next --session /absolute/control
python3 scripts/session.py check --session /absolute/control --check AC1
python3 scripts/session.py finish --session /absolute/control
```

`control` 必须位于交付工作区外，进程退出后用相同目录继续 `next`，不用重新 `init`。历史COMPLETED不再接受新写入；若其当前产物或输入已变，`next` 返回STALE_COMPLETION，需恢复原候选或开启授权新修订。已有 session 不覆盖，写锁不因超时抢占。每次命令读当前文件，结果原子保存；不启动后台进程。

契约示例在导出包的 `assets/contracts/` 或完整系统的 `portable-agent-kit/examples/contracts/`：

- `scope` 列出允许作为交付物检查的相对文件路径，`inputs` 列出必须保持的原始输入。
- `acceptance` 每项包括唯一 `id`、具体 `expected`、真实 `environment` 及可选 `probe`。
- `policy.workspace_write=false` 表示检查器不写交付物，只读文件并保存控制状态；交付物仍由已授权宿主工具完成。`external_actions=false`，不等于获得任何外部权限。
- `probe` 支持 `text.equals`（完整文本一致）、`json.equals`、`json.value`（pointer 是键/索引数组）、`csv.reconcile`（源 CSV 筛选加总与 JSON 金额比较）、`text.contains`（仅内容存在，不证明语义）。`csv.reconcile` 的 source 必须列入 inputs。
- 数值检查保留十进制精度并精确加总；每个数最多100位有效数字、指数绝对值不超过100，超出明确失败。金额可用十进制字符串表达；契约中无法无损保留的小数会拒绝导入，须改成字符串或专用精确数值检查，不能静默修改验收值。单文件上限5MiB，超过CSV解析器字段限额（本机默认128KiB）会保存明确FAIL，不会中途丢失反馈。CSV筛选必须引用真实列，零匹配会记录为零行；口径合理性仍由原需求与专业审核判断。
- 没有可执行 probe 或需要人工/浏览器/独立审查的验收，保留为验收项。本地工具返回 UNKNOWN，整个任务不可完成；接入真实宿主验证，不能删项或伪造 PASS。

检查器自己读文件和重算哈希，保存每次观察。`finish` 再次核对每个验收项、原始输入和证据；任何缺项、FAIL/UNKNOWN 或产物变化都阻断。原始失败保留，修复后新检查可替代同项旧结论。两次已记录失败时，`next` 提示诊断/拆小/升级；它不自动调用更贵模型，也不判断所有失败都来自推理。

该入口是本地产物读回，不是任意工具执行器、独立审核、身份认证或业务能力证明。自定义宿主用 AAP 内核的受信 `verify_evidence` 回调接入自己的真实工具；`host_verified:true` 或模型自写证据不会被当作认证。宿主不能仅凭 capability manifest 声称已探测能力。

## 判断这次升级是否有效

观察同一类真实任务的首次完成率、用户重复纠正、漏检、返工、实际成本。工程回归只验证特定不变量；模拟弱执行者的故障注入和单次子 Agent 前向验证都不能证明任意弱模型提升或所有平台已适配。
