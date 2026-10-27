# 第5轮：Windows旧CLI的停止、保锁与恢复

2026-10-23，接续用户对上一轮明确候选的“可以”授权，从`64ff73f`建立`fix/windows-worker-handoff`；fetch核对远端没有新变更，main仍`0aab8b4`，不合并、不发布、不更换全局安装。使用实际安装的Solo和当前项目/软件交付引用；仅处理保留的CLI兼容入口，原生委派不是该入口，不新增队列或调度器。

## 起点与真实失败

上轮发现的`codex_worker._stop`在Windows无条件调用不存在的`os.killpg`。本轮先冻结准确的`64ff73f`源码，用真实本地CLI替身、Git候选副本与合法takeover重放：仍抛AttributeError、没有最终receipt，原父子进程仍活。fixture随后仅按已记录的自有PID清理并核对，不运行模型或读取认证。基线脚本SHA256 `bf2670bb28e3e8fbe5b3c2cfee1fb54d3e77d446831b03f5fc743c08193ea751`。

进一步核对恢复链发现，队列把worker抛出的异常无条件折成BLOCKED终态，会解除ACTIVE防线。独立基线Git archive重放同一停止未知场景，实际得到BLOCKED而预期RUNNING，失败断言保留。只修taskkill而不修失败后的归属，会继续存在双写风险，因此两处属于同一恢复链必要修复。

## 实施范围

- Windows只使用系统绝对路径taskkill，按本次Popen PID加`/T /F`，命令与wait分别有界；系统目录缺失明确失败，不按程序名结束进程。POSIX保持TERM、有限等待、KILL和回收。正常完成、时间到点或安静本身不触发停止。
- 停止尝试返回`process_cleanup={status,method,leader_stopped,tree_stopped,errors}`。停止失败和意外异常也持久保存BLOCKED/ERROR、父决定、工作区和诊断。`process_running`为false/true/null；leader退出但树仍未知不能记false，未知不生成稳定交接patch或对活现场做结束fingerprint。receipt/progress与父队列读写显式UTF-8。
- 队列对明确停止未知保留RUNNING、PID、结果与活动锁；即便候选过期或路径越界也不能先解锁。不发RESOURCE_FINISHED，也不把待处理父决定假记为运行结束。on_start之后失去控制的泛异常同样保留未知；启动前失败及无新字段的旧回执保持兼容。
- resource/review对账共用只读进程探针，Windows使用SYNCHRONIZE句柄和零等待查询，拒绝访问/未知结果不能当已结束。停止未知需要宿主结构化扫描证明，且原launcher/child均不再活；对账后仍保留原失败，需要诊断解决后才能新派发。

## 关于旧存活探针的纠正

最初根据[Python os.kill文档](https://docs.python.org/3.12/library/os.html#os.kill)误推“Windows的sig=0必定TerminateProcess”，随后本机确认`CTRL_C_EVENT`本身就是0，不能这样断言。只对自有短命fixture的两次试验分别观察到未就绪进程初始化中断、READY后未停止且本次测试Python收到KeyboardInterrupt；因此不能声称旧0探针稳定杀进程，也不能把它当POSIX式无副作用查询。失败观察及错误假设保留，不再重复旧信号入口。新的只读探针独立连续查询20次，目标保持运行，fixture显式结束后返回false；未对真实Agent或用户进程运行旧探针。

## 交付质量与边界

| 场景 | 修复前实际结果 | 修复后实际结果 |
| --- | --- | --- |
| Windows明确takeover | AttributeError、无最终回执、父子仍活 | 确认父子结束，持久PARENT_HANDOFF，保留部分patch及父决定 |
| OS停止失败或清理意外异常 | finally可逃出、缺最终证据 | 持久BLOCKED/ERROR，现场保留，无可接手patch |
| leader先退出，树停止未知 | 没有可靠交接确认 | process_running=null、停止未知，不假放行 |
| 队列收到明确停止未知 | BLOCKED终态可能解锁 | RUNNING/活动锁保留，取消、交付、改配置、采纳patch和重复派发受阻 |
| 确认原launcher/child退出后恢复 | 未作为本轮旧实现结论补记 | INTERRUPTED、旧失败逐字保留；未解决诊断仍拒绝，解决后仅新派发一次 |
| 正常静默跨检查点 | 既有继续运行契约 | 仍正常完成，不因时间或安静结束它 |

主Agent最终Windows实跑`test_worker_stop.py`9项，18.589秒，全通过；`test_resource_stop.py`7项，11.862秒，全通过。另有独立9项worker复跑、20次只读探针与实际Git/SQLite恢复链、最终STALE/SCOPE锁优先级定向审查通过，最终无阻断性发现。故意注入停止失败的测试返回时进程按契约仍活，会出现Popen ResourceWarning；fixture之后明确清理并逐PID确认结束，不隐去警告或把仍活误称停止成功。

本机队列fixture首次被既有Windows Git根路径分隔符字符串比较挡住。测试仅将真实Git根返回值规范为Path.resolve，原Git/SQLite、状态机及实际进程均保留；生产approve未修改，该既有问题作为后续候选。恢复时用另一个自有、已退出短进程的PID模拟旧launcher结束。因此本轮支持worker和队列停止/恢复不变量，不支持“完整Windows队列从零端到端已通过”的结论。原旧CLI测试用Unix shebang，不能冒充真实Windows运行；本轮新增.cmd fixture覆盖Windows，POSIX原套件另由CI执行。

CI在既有Windows Python3.9/3.12任务中新增两组定向停止/恢复测试，首条失败不会被第二条覆盖；Ubuntu继续完整native套件，其余便携任务保持。结果以[本分支CI](https://github.com/lizi-product-studio/solo-dev-delivery/actions?query=branch%3Afix%2Fwindows-worker-handoff)为准。不重跑未改的真实Codex回调、生成模型或导出包功能来扩张结论；这是无模型的真实OS进程及本地CLI替身测试，未验证主动逃逸的进程树或真实模型业务推理。

复现定向测试：

```powershell
python -B -X utf8 -m unittest discover -s tests -p test_worker_stop.py -v
python -B -X utf8 -m unittest discover -s tests -p test_resource_stop.py -v
```

完整基线、初次探针失误、真实回执、现场、警告与独立报告保留在本机仓库外`outputs/autonomy-round5-2026-10-23/`。入库仅去敏摘要、维护测试、方法和成本，不保存个人路径、客户原文、认证或运行会话。[去敏结果](2026-10-23-worker-handoff-results.json)与[用量快照](2026-10-23-worker-handoff-costs.json)分别记录质量及消耗。

## 人工介入与成本口径

本次初始授权1次；后续澄清0、额外审批0、用户手工修复0。主Agent恢复链核对、错误假设、失败试验、两个子Agent的实施与独立审查均计成本，不把协调视为免费。用量从本次输入`2026-10-23T05:12:57.735Z`起，只统计明确的三个线程、包含自动接续并按response去重。缓存输入含在总输入内，推理含在输出内；统计截止后的提交、CI等待、最终答复及未落盘记录不在快照中。不换算订阅额度或金额，不把异题轮次用量差异当成本优势。

快照截止`2026-10-23T05:27:38.784Z`，881.049秒（约14.7分钟）；总输入13524465，其中缓存13267968、未缓存256497，输出49146；93次去重响应、89次宿主可见工具调用。包括未选实现假设与失败试验，截止后的提交和CI等待另计。
