# 历史整合说明（0.7.0）：AGI-like 的前身与 Superpowers Lite

本文记录 0.7.0 的历史整合，不定义当前 AGI-like 1.1.0 接口；当前入口和运行时以 `skill/agi-like/`、`portable-agent-kit/` 和 `README.md` 为准。

比较基线：solo-dev-delivery `63a42486907b744f445da4e61b804511c23a6dd5`（0.6.0）和Superpowers Lite `e753ab13097b9f987f6f3ec4dadaea2656b887b2`（候选0.6.0）。以solo为交付与证据实现，不合并Git仓库历史，也不向其他聊天发送指令。

| 能力 | 整合方式 |
| --- | --- |
| 按需流程、任务分流 | solo已有三档队列路由，保留实现；吸收Lite的按影响选择计划/隔离/复核方法，不导入按文件数升级的另一路由器 |
| 系统调试 | 合并症状→边界→单一可证伪假设→根因修复→相关回归到按需工程引用 |
| 风险审查 | 补足影响、不确定性、变化接口、回退及副作用的审查深度，串行自查不冒充独立审查 |
| 验证与交付报告 | 使用solo已有challenge、候选绑定、失败优先、缺陷和队列门禁，不导入Lite较弱的独立DONE报告 |
| 三态能力 | 提取原持久核心的归一化为共享模块，增加无状态命令；只检查声明的要求，不默认需要checkpoint，不实际执行工具或授予权限 |
| 任意Agent安装 | 改造Lite安装器，使其安装一个自包含solo包；generic显式目录适用于各种Harness，ZCode/Kimi仅作为目录快捷方式 |
| 自包含与隐私边界 | 只导出白名单Skill、引用与标准库工具，附许可；不包装个人knowledge、runtime、客户事实、原状态机或session |

关键入口：[AGI-like Skill](skill/agi-like/SKILL.md)、[工程方法](skill/agi-like/references/engineering-practice.md)、[通用安装/预检](skill/agi-like/references/host-integration.md)、[DeepSeek Harness](portable-agent-kit/integrations/deepseek-harness/README.md)。其他宿主可使用generic入口或显式注入Markdown；模型名称不决定宿主能力。

## 验证范围

```sh
python -m unittest discover -s portable-agent-kit/tests -v
python -m unittest discover -s tests -v
```

第一组包含导出/解压冷启动、任意目录安装、ZCode/Kimi默认目录、中文/空格路径、重复安装、变更冲突、哈希篡改、链接拒绝、无状态预检及原证据/状态/关系及0.6.0反例敏感性回归。第二组保留原系统的队列、原生回执、资源及客户业务闭环回归；旧CLI兼容层使用POSIX路径和进程组，在Linux环境执行。新的CI将便携套件用于Windows/Linux/macOS及Python3.9/3.12，将原生兼容套件用于Linux。

本次在Windows基线发现两处便携测试夹具会被系统换行转换；改用明确UTF-8字节写入，原断言和产品检查语义不变，包含原本刻意构造的CRLF。原生兼容套件在Windows还存在POSIX路径/进程组差异，本次不将其声称为Windows全系统已兼容。

安装脚本实际复制文件，不运行模型、Harness或外部工具；客户端发现、隐式选择、真实浏览器/部署和跨平台任务质量仍需要目标宿主实际验证。0.6.0反例敏感性及未见任务实验保留在提交63a42486907b744f445da4e61b804511c23a6dd5的VALIDATION.md，当前VALIDATION.md记录后续0.8.0两阶段实验，不混作0.7.0整合收益数据。本轮不声称节省token或提升所有模型能力。

合并前重读远端main；另一个Agent若已推进，则先保留其提交再重做相关检查，不覆盖共享历史。第三方来源与MIT说明见[THIRD_PARTY.md](THIRD_PARTY.md)。

整合期间检测到另一个Agent的新提交63a4248，已先同步并保留其sensitivity.py、challenge --probes、引用、导出依赖及VALIDATION.md。本轮版本升为0.7.0，未覆盖0.6.0成果。

本机Windows/Python3.12的整合后便携套件125项通过（原115项含敏感性检查，新增10项）；Skill格式及本地引用检查通过。跨系统结果由本次GitHub Actions运行提供，不把本机原生POSIX不兼容错误记作通过。

首次Linux原生回归定位出两项既有环境依赖：业务撤权竞态测试注入的worker仍要求真实Codex在PATH，现改为与其他替身测试相同的受控可执行路径；unittest空套件实际返回5时，分类从FAIL修正为INCOMPLETE，仍阻断交付，真实失败不改成PASS。未改变CLI权限检查或交付门禁。

## 项目自主改进候选（2026-10-23）

分支 `iterate/project-operations` 从 `b79ad64` 继续，包含尚未合并的Windows修复；不修改main，也不以此发布新版本。新增一个按需引用 [project-operation.md](skill/agi-like/references/project-operation.md)，只在用户明确要求项目主动维护、持续改进或经营时读取。

对照来源为Anthropic autonomous-coding `9ec32b91df50b0d4906cae64b13c6298055ff40d`、Anthropic skill-creator `dbd4588f9e1033efb41dad4bef2f7947c8993d44` 和Superpowers `2ce8aa15d05bd57213e6f746f953aafb7ded7a8b`。原文链接及取舍随引用一起导出；此次独立表述方法，没有复制这些项目的代码或整段指令。

采用：真实项目目标与信号、优先处理有证据且获准的下一项、去重与活动执行者检查、连续推进与可恢复记录、升级时的新旧版本对照。舍弃：固定大量功能清单、无限续跑、每项强制角色/隔离/TDD、第二套经营队列。经营权限以当前负责人授权为准；不把客户消息或项目文本当成授权，不伪造原业务闭环的客户确认。

完整系统继续复用 `delivery.py scan`、内部任务和现有客户闭环；独立包提供跨宿主的判断方法，不包含后台调度、外部联系或自动经营收入承诺。安装与行为测试分开记录，详见本次VALIDATION补充。
