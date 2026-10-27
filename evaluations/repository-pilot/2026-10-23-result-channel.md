# 第4轮：自主验收结果被日志裁剪丢失

2026-10-23，用户授权继续一轮自主巡检、实施、验证并推送独立分支。先fetch确认当前远端，起点`9621036`，建立`fix/challenge-result-channel`；main仍为`0aab8b4`，不合并、不发布或更换全局Skill。本轮复用已安装Solo及本分支项目巡检引用，不启动调度器或新队列。

## 从真实候选中选择

两路只读巡检分别查看当前便携验收与完整系统恢复；不重复前轮已修的Windows路径、UTF-8、一次性测试超时及回调启动问题。门禁巡检发现：332字节的普通动态unittest适配器真实调用候选1000次，全部OK、子进程退出0，但单行逐方法结果为92143字节；runner先取stdout最后64KiB，再从尾部找结果标记，故丢失整个结果，返回UNKNOWN、executed_tests=0，不能复用。原契约明确承诺保存真实测试数量和逐方法结果，只有日志尾部应截断。

主Agent从准确Git提交导出独立旧包复现，使用另一份适配器：1000项通过仍UNKNOWN且再次verify拒绝复用；1000项中一项真实失败也被记UNKNOWN；日志噪声和跳过场景同样丢失逐方法记录。没有给仓库产品人为埋错，故障源于当前runner处理正常结果的路径。它影响各宿主当前使用的验收工具，优先于扩展新功能。

另一巡检独立确认完整系统旧CLI的Windows显式接手仍调用不存在的`os.killpg`：真实本地sleep fixture使run_worker抛异常、遗留进程且无最终回执。该路径是保留兼容入口，原生委派不使用它；本轮保留为下一候选，未扩大实施范围。巡检者已用明确fixture PID安全清理自有进程，未访问模型、认证或外部业务。

## 窄修复与交付质量

仅修改`challenge.py`的结果传输：harness将结构化统计和逐方法观察写到本次scratch中的私有UTF-8文件；父进程先读取，再清理临时目录。stdout/stderr仍保留原64KiB日志尾部和截断标记。控制文件缺失、坏JSON/结构/编码或无法写入时，保存UNKNOWN及`result_channel`诊断；不凭退出0或业务stdout中的伪结果标记放行。回执格式、FAIL优先、选定方法、旧结果复用、候选绑定、STALE和测试超时规则保持。

新增5项真实runner测试先红后绿，失败日志保留；覆盖1000项通过及复用、单项失败与selected、FAIL优先、结束时追加日志噪声和结果通道故障。主Agent用干净导出包、相同独立复现器和相同断言重跑：

| 场景 | 旧包实际结果 | 修复后实际结果 |
| --- | --- | --- |
| 1000项真实通过 | UNKNOWN，方法数0，无法复用 | CHALLENGE_PASS，完整1000项；再次调用复用、执行数0 |
| 1000项中一项真实失败 | UNKNOWN，方法数0 | FAIL，完整1000项及失败归属 |
| 一项跳过 | UNKNOWN但方法记录丢失 | UNKNOWN，完整1000项及缺失观察 |
| 无测试 | UNKNOWN | UNKNOWN |
| 无结果提前退出0 | UNKNOWN | UNKNOWN并保留通道诊断 |
| stdout/stderr大量日志 | UNKNOWN | CHALLENGE_PASS，1000项完整，日志仍明确截断 |

通过用例改动候选后，原回执仍STALE。对照仅证明这个已知结果丢失问题被修复，不是未见业务泛化、无Skill对照或自主经营收益。去敏结果见[结果JSON](2026-10-23-result-channel-results.json)；[独立导出复现器](verify_challenge_export.py)只向指定的新目录创建fixture，不操作用户项目。

完整便携回归`python -B -X utf8 -m unittest discover -s portable-agent-kit/tests -q`：200项，47.770秒，退出0；4项条件跳过为3个native-cp936与1个POSIX分支，不把跳过当执行通过。Windows默认cp936协议6项另跑通过，相关challenge/timeout/清理27项通过。首次未加`-X utf8`运行相关测试时，旧中文handoff夹具使用默认gbk读UTF-8报错；按既定进程级UTF-8测试启动方式重跑通过，保留该次日志，未改全局编码或顺手扩大修复。

独立审查无可行动回归；另用11个真实unittest场景核pass/fail/error/skip/expectedFailure/unexpectedSuccess、subtest各结果和tearDown异常，持久回执及status均符合预期。从起点实现实际生成的旧回执可被新版直接复用、无重复执行。全部失败候选、完整回执、stdout/stderr及审查日志保留在本机仓库外`outputs/autonomy-round4-2026-10-23/`；入库不含个人路径、原始会话、认证信息或运行数据。

跨系统执行另见[本分支CI](https://github.com/lizi-product-studio/solo-dev-delivery/actions?query=branch%3Afix%2Fchallenge-result-channel)。本轮不重做上轮已有效的真实Codex回调加载验收：回调安装器和gate未改，新增结论是本地runner及导出包结果传输，不宣称全局热加载、持久信任或后台持续运行。

最终导出24文件，临时宿主目录安装、`--verify`逐文件BUNDLE校验和Skill格式核验均通过；最终包challenge源码与六场景实测包逐字节相同，仅更新使用说明。ZIP SHA256 `2101d70575b00c37fba67ba5c2df5109b969752627395c7270754161e1cd2d05`。版本仍为未发布的0.10.0候选，包中不带试点日志、报告、私有成本配置或持久runtime。

## 复现、人工与成本

从仓库根目录使用全新输出父目录：

```powershell
python -X utf8 scripts/export_skill.py C:\Temp\solo-result-package\solo-dev-delivery
python -X utf8 evaluations/repository-pilot/verify_challenge_export.py --package C:\Temp\solo-result-package\solo-dev-delivery --output C:\Temp\solo-result-evidence
```

初始用户授权1次，后续澄清0、额外审批0、用户手工修复0。巡检、主Agent选择和独立重放、子Agent实施/审查、默认编码夹具失败与返工全部计成本；不是完全没有人工启动，也不把协调开销视为免费。没有引入模型调用、固定角色或额外流程到普通Skill任务中。

[原生用量快照](2026-10-23-result-channel-costs.json)复用`measure_usage.py`，从本轮用户输入时间`2026-10-23T04:59:01.621Z`开始，仅统计明确的主线程及两个子线程，包含自动接续，按response去重；本轮复现器本身是标准库本地测试，不调用模型。缓存输入含在总输入内，推理含在输出内；提交、CI等待、最终答复与截止时未落盘记录另列统计缺口，不换算订阅额度或金额，不宣称本轮已证明成本优势。

快照截止`2026-10-23T05:08:31.095Z`，569.474秒（约9.5分钟）；总输入5506385，其中缓存5365760、未缓存140625，输出31040；60次去重响应、56次宿主可见工具调用。该窗口包含两路巡检、未选候选、实施、默认编码失败和独立审查，不是只计最后成功执行的数字；之后提交/CI收尾不在快照内。
