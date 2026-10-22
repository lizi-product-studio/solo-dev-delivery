# 0.7.0：solo-dev-delivery 与 Superpowers Lite 的整合

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

关键入口：[Skill](skill/solo-dev-delivery/SKILL.md)、[工程方法](skill/solo-dev-delivery/references/engineering-practice.md)、[通用安装/预检](skill/solo-dev-delivery/references/host-integration.md)、[DeepSeek Harness](portable-agent-kit/integrations/deepseek-harness/README.md)。其他宿主可使用generic入口或显式注入Markdown；模型名称不决定宿主能力。

## 验证范围

```sh
python -m unittest discover -s portable-agent-kit/tests -v
python -m unittest discover -s tests -v
```

第一组包含导出/解压冷启动、任意目录安装、ZCode/Kimi默认目录、中文/空格路径、重复安装、变更冲突、哈希篡改、链接拒绝、无状态预检及原证据/状态/关系及0.6.0反例敏感性回归。第二组保留原系统的队列、原生回执、资源及客户业务闭环回归；旧CLI兼容层使用POSIX路径和进程组，在Linux环境执行。新的CI将便携套件用于Windows/Linux/macOS及Python3.9/3.12，将原生兼容套件用于Linux。

本次在Windows基线发现两处便携测试夹具会被系统换行转换；改用明确UTF-8字节写入，原断言和产品检查语义不变，包含原本刻意构造的CRLF。原生兼容套件在Windows还存在POSIX路径/进程组差异，本次不将其声称为Windows全系统已兼容。

安装脚本实际复制文件，不运行模型、Harness或外部工具；客户端发现、隐式选择、真实浏览器/部署和跨平台任务质量仍需要目标宿主实际验证。VALIDATION.md保留另一个Agent的0.6.0反例敏感性及未见任务实验，不冒充本轮收益数据。本轮不声称节省token或提升所有模型能力。

合并前重读远端main；另一个Agent若已推进，则先保留其提交再重做相关检查，不覆盖共享历史。第三方来源与MIT说明见[THIRD_PARTY.md](THIRD_PARTY.md)。

整合期间检测到另一个Agent的新提交63a4248，已先同步并保留其sensitivity.py、challenge --probes、引用、导出依赖及VALIDATION.md。本轮版本升为0.7.0，未覆盖0.6.0成果。

本机Windows/Python3.12的整合后便携套件125项通过（原115项含敏感性检查，新增10项）；Skill格式及本地引用检查通过。跨系统结果由本次GitHub Actions运行提供，不把本机原生POSIX不兼容错误记作通过。
