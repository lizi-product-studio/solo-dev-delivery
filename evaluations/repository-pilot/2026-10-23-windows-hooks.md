# 第3轮接续：核实并修复 Windows 回调启动

2026-10-23，从`4b5312d`建立`fix/windows-hook-launch`，不合并main、不发布新版本。保留[上轮失败调查](2026-10-23.md)及候选，不覆盖历史。范围为当前Codex的Windows结束回调启动，未更换全局Skill、持久信任设置、调度器或业务验收规则。

## 先取得真实调用约定

[官方Hooks文档](https://learn.chatgpt.com/docs/hooks)说明`commandWindows`覆盖、stdin JSON、审核信任及一次调用的已审查自动化信任选项，但未指定本机shell argv。先读取已安装二进制的版本、帮助、app-server schema，再用该二进制实际执行生命周期，观察回调进程的父链，不把源码字符串或自己拼出的shell当作宿主证据。

实测`codex-cli 0.163.0-alpha.11.2`，二进制SHA256 `c81ed464e5e7cafd09a5fa26af390c480bed8e9fab2a87a54b285fbd56e7122a`。SessionStart及独立Stop观察均得到：Python回调的直接父进程为bundled `pwsh.exe`，argv包含`-NoProfile -Command <表达式>`，再上层为该Codex。输入为真实事件JSON，并核对同一session；CIM观察显式UTF-8输出，避免中文命令行被cp936误解码。

生命周期测试使用新建的空`CODEX_HOME`、只读工作沙箱、`--no-daemon`和`--ephemeral`；仅该测试进程使用官方文档中的`--dangerously-bypass-hook-trust`，执行已审查的本地测试hooks。未读/复制认证文件、修改持久信任或当前聊天设置。这证明真实命令与事件处理，不证明普通用户已经在`/hooks`信任全局安装，也不证明桌面当前聊天热加载了候选。

模型接口由loopback确定性Responses SSE替身提供，无真实模型调用；它不替代Codex本体、shell、gate或实际业务检查。后续集成测试设置代理拒绝非loopback访问。首次契约探针曾触发Codex自动缓存访问并收到401，保留日志，不能将该首次探针声称为完全无外部HTTP。

## 同一生命周期中的修复前后

从准确的`4b5312d` Git archive生成旧包，避免并行工作区修改污染基线。固定后的包与旧包均放在含中文、空格、单引号、`$`和`&`的实际合法目录。先运行原安装器，再由实际Codex执行其原样生成的Stop定义。

| 场景 | 旧包实际观察 | 修复后实际观察 |
| --- | --- | --- |
| 未验证任务 | Codex直接结束，任务仍待验证，登记残留；失败 | 收到gate阻断反馈，登记保留；通过 |
| 业务验收已通过 | Codex结束，但任务停在VERIFIED、登记残留；失败 | 任务变为DELIVERED，登记删除，正常结束；通过 |
| PASS后改动候选 | 未执行，不补记 | 收到过期阻断反馈，登记保留；通过 |
| 业务断言失败 | 未执行，不补记 | 收到失败阻断反馈，登记保留；通过 |
| Stop原生退出1 | 真实控制探针不阻止结束 | 增强观察后确认Stop确实执行、会话匹配；不阻止结束 |
| Stop原生退出2 | 下一次请求含Stop stderr标记，确认会阻断 | 增强观察后确认Stop父链与事件；仍阻断 |

阻断类case的第二次模型请求携带实际hook反馈后，由本地替身故意返回503结束测试，避免无限循环。因此非零CLI退出只是有界收尾，不能单凭它判定门禁生效；断言还要求真实反馈、任务未DELIVERED及登记保留。通过类必须CLI退出0、只有一次请求、任务DELIVERED且登记已删除。初次exit1仅靠“退出0/一次请求”不足以证明Stop执行，经独立审查补事件、session、父链证据并真实重跑，未改原生产断言。

## 窄修复和回归

`gate.install_hook`继续保留POSIX命令，为Windows生成直接PowerShell表达式，使用当前解释器和gate绝对路径；单引号按PowerShell字面量转义。无二层PowerShell、EncodedCommand或新增常驻包装器。显式转发native退出码，调用失败返回2，避免未设置的LASTEXITCODE被当成成功。相同POSIX命令的旧处理器原地补齐Windows覆盖，保留matcher、timeout、自定义字段及其他hooks；重复安装不重写。

- 全量便携：`python -X utf8 -m unittest discover -s portable-agent-kit/tests -q`，195项，48.220秒，退出0；4项条件跳过为3个native-cp936分支及1个POSIX shell分支，不把跳过计为实际通过。默认Windows cp936的6项协议回归已由实施者另跑通过；真实Linux/macOS执行以此分支CI为准。
- 新增6项测试在真实pwsh及PowerShell5覆盖复杂解释器/脚本路径、中文UTF-8 stdin、精确0/1/2、缺失解释器/脚本、阻断后真实验收和登记删除、旧定义迁移及幂等；Windows本地其中5项执行通过，POSIX一项按宿主跳过。
- 独立审查发现测试夹具复制Windows venv重定向器导致106/`No pyvenv.cfg file`，保留失败后改为仅在搬迁测试runtime时复制base解释器；产品仍绑定当前`sys.executable`。同一venv复跑6项，11.587秒，退出0、POSIX一项跳过。审查最终无阻断项。
- 真实Codex六个生命周期case通过；exit1/exit2增强事件观察后另跑2个case通过。结果去敏摘要见[结果JSON](2026-10-23-windows-hooks-results.json)。完整临时配置、父链、事件与stdout/stderr留在本机仓库外`outputs/windows-hook-launch-2026-10-23/`，不提交个人路径、会话原文或认证信息。

全量回归后只修改了新测试的venv夹具；独立venv定向复跑已验证该改动。最终包、安装/BUNDLE和Skill格式结果由本轮交付读回记录补充，跨系统结果见[分支CI](https://github.com/lizi-product-studio/solo-dev-delivery/actions?query=branch%3Afix%2Fwindows-hook-launch)。无全局激活或长期自主经营结论。

最终干净导出24文件，临时宿主目录安装及`--verify`逐文件BUNDLE核验、`quick_validate.py`均通过；最终包gate源码与六场景实测包逐字节相同，仅使用说明补充有变化。ZIP SHA256 `3f82387a713105cc18f396de7c91ab1e80c3b5c576e90a456c0bc76bb6b027f2`。未将试点报告、会话日志、成本配置或持久runtime打入Skill包，版本仍为未发布的0.10.0候选。

## 可重复的实际宿主验证

手动集成复现器为[verify_codex_windows_hooks.py](verify_codex_windows_hooks.py)，Windows标准库脚本。它需要操作者明确给出已安装Codex、导出包和全新证据目录，自动执行上述已审查hooks的一次调用信任选项；不属于Skill默认流程或CI对其他平台的加载承诺。所有测试文件只写指定的新证据目录，拒绝覆盖旧证据。

```powershell
python -X utf8 scripts/export_skill.py C:\Temp\solo-hook-package\solo-dev-delivery
python -X utf8 evaluations/repository-pilot/verify_codex_windows_hooks.py --codex (Get-Command codex).Source --package C:\Temp\solo-hook-package\solo-dev-delivery --output C:\Temp\solo-hook-evidence
```

人工介入按此次接续单独记录：初始授权1次，后续澄清0、额外审批0、用户手工修复0；不是把初始授权算成无人工。主Agent协调、文档检索、失败试验、子Agent实施和独立复核全部计入成本。首次导出驱动的PowerShell路径插值错误导致导出失败，改用Python参数数组后成功；作为试验开销保留，不归因于产品。

原生用量汇总见[成本快照](2026-10-23-windows-hooks-costs.json)，复用`measure_usage.py`，只读明确的本次主线程及两个子线程，按本次时间窗包含自动接续，不按初始turn过滤。缓存输入含在总输入中，推理含在输出中；替身报告的0 Token只属于离线CLI探针，不能冒充本轮Agent用量为0。统计截止后的提交、CI等待、最终答复及未落盘用量不在快照内，不做订阅额度或金额换算，也不据此声称较旧版省成本。

本轮快照从`2026-10-23T03:41:08.018Z`至`03:57:17.811Z`，969.793秒（约16.2分钟）；总输入8797664，其中缓存8282752、未缓存514912，输出56447。75次去重响应、71次宿主可见工具调用；包括失败实验、协调和独立审查，不视为免费。上述数字仅为该截止时间的累计，不是含后续CI等待的最终账单。
