# 四项 P2 关闭复核

复核时间：2026-10-23。正式第一 block 启动后，只读核对冻结代码与 author selftest；未读取任何 `runs/`、正式结果或候选产物，未运行模型，未修改 runtime、benchmark、fixtures 或候选。本次新增文件仅为本说明与独立 Git 回归输出。

## 冻结身份

- `design/fixture_tools.py` SHA256：`b6bd7b62445eaa59fd0f55158490c7bfd498d3229505ac013af5414b37bb853b`，与根协调者给定值完全一致。
- `benchmark.py` SHA256：`ad5bd2d02e5b1c4a5f4d4fdccc17985f39e66b24e1a369aac2b9b22363ddf24a`。
- `runtime.py` SHA256：`924506dbb74a0686245f67c3f1ee9430a6ca775f1edf0fdd901d3704c76e43de`。

## 逐项结论

| 原 P2 | 结论 | 代码与证据 |
| --- | --- | --- |
| 交接隐藏长度/关键词门槛，同时漏检可执行语义 | 自动判分缺陷已关闭；正式业务语义仍须盲审 | `fixture_tools.py:713-725` 删除长度、权限词面和 publish 关键词评分，只自动核非空产物及真实 preview_id；返回 `requires_blind_handoff_review=true`、`automatic_semantics_assessed=false` 和三项业务准则。最终完成规则明确为自动通过且三项盲审通过。`benchmark.py:191` 将此规则预注册。author selftest 的短中文交接与假权限词案例均通过相应控制：通过只表示自动层，不能认证语义。 |
| CLI 删除输出导致 checker 抛异常、误归为 harness_error | 已关闭 | `fixture_tools.py:644-655` 捕获缺失输出的 OSError，以 `actual=None` 记业务失败；成功分支也捕获解析 None 的 TypeError。author selftest `02-reading-packet:deleted_cli_output_is_business_failure` 为 true，其控制源码位于 `selftest.py:109-115`。 |
| 多工作树合法交付被隐藏的“恰好一棵额外树”规则拒绝 | 部分关闭，保留下述路径选择边界 | `benchmark.py:28` 已公开要求最终答复标出唯一交付目录；`check_submission` 支持在多棵真实、可归属 Git worktree 中接受唯一声明路径，保留根结果，不按最高分挑选。独立原生 Git 回归通过；但声明解析仍使用子串匹配，且不解析相对 sibling 路径。 |
| API 只验证单一成功案例，漏掉错误类型契约 | 已关闭 | `fixture_tools.py:609-627` 对任务1全部18个输入批量调用 API；`:662-682` 对任务2全部21个输入调用 API，并用 isinstance(ValueError) 检查错误情形，支持合法子类。同进程重复调用还可发现残留状态。author selftest 的两题 reference_passes 与 `API_ValueError_contract_independent_of_CLI` 均为 true。 |

上述 selftest 来自 `design/selftest-results.json`，其标记为 `author_selftest_not_model_results`。本次读取并核对了相关控制源码；没有重新运行整套 fixture selftest，也没有把其结果当作模型表现。

第三题自动 `completed` 或运行器的自动完成状态不能直接当作最终业务完成。全部正式展览交接仍须按照同一预注册准则进行独立、隐藏组别的语义审查。本次未读取产物，因此未执行、也未宣称完成该盲审。

## 工作树独立回归与残余边界

按根协调者授权执行 `python -B -m unittest test_benchmark -v`。退出码为 0，1 项测试通过，实际创建了 Git 仓库与两棵 worktree，验证唯一声明树的选择及未声明时保留歧义。原始输出保存在 [worktree-selftest-independent.txt](worktree-selftest-independent.txt)。这是宿主回归测试，不是模型 run；未扩大测试范围或修改冻结实现。

残余 P2 是路径声明的解析方式：`benchmark.py:65-69` 用 `absolute in normalized` / `relative in normalized` 判断声明。若两棵已拥有的工作树分别为 `.worktrees/fix` 和 `.worktrees/fix2`，仅声明后者也会同时匹配两者。纯内存复核得到 `declared_match_count=2`，从而可能退回未完成的根目录。另一个合法写法 `../feature` 指向 sibling worktree 时，当前代码只匹配其绝对路径，不解析这个相对声明。现有回归的 `one` / `two` 命名不覆盖这些边界。

**尚未证明上述边界影响任何正式 run 或评分。** 本次保持不接触正式结果。根协调者已接受该限制：保留冻结首次运行器与首次结果，不修改候选、不向候选反馈判分信息；最终对所有组统一按精确路径边界、真实 Git 拥有路径做后处理审计。若确有受影响记录，以同一规则全组重判并并列保留首次与重判结果、原因和选择证据，不把重判通过改写为首次通过；若没有受影响记录，仅保留此潜在限制说明。

其余三项原 P2 有明确关闭证据。工作树项因上述解析边界保持“部分关闭”，不以原生 Git 常规回归通过替代边界完整性结论。
