# 前端 Skill 来源研究：供 Solo 后续改进取舍

获取日期：2026-10-23（Asia/Shanghai）；核对时间：2026-10-23 08:37 UTC。范围是阅读、比较与形成候选决策；没有安装或运行外来 Skill，也没有修改 Solo 或冻结中的对照候选。

最有价值的共同点是：界面围绕实际任务组织，文字和视觉元素各有用途，异常状态可以恢复。不能从这些来源推导出“所有后台都要有品牌大标题”“统一换成某种高级风格”，也没有证据证明安装某个 Skill 就必然改善产物。以下采纳建议是结合本次用户痛点作出的设计判断，尚不是实验结论。

## 实际读取的来源与版本

| 来源 | 可复核快照 | 许可证据 | 实际作用 |
|---|---|---|---|
| [Anthropic frontend-design 原文](https://github.com/anthropics/skills/blob/dbd4588f9e1033efb41dad4bef2f7947c8993d44/skills/frontend-design/SKILL.md) | 仓库 HEAD `dbd4588f9e1033efb41dad4bef2f7947c8993d44`；文件无独立版本号 | 同目录 [LICENSE.txt](https://github.com/anthropics/skills/blob/dbd4588f9e1033efb41dad4bef2f7947c8993d44/skills/frontend-design/LICENSE.txt) 为完整 Apache-2.0 | 设计生成与自评指导 |
| [Vercel web-design-guidelines Skill 原文](https://github.com/vercel-labs/agent-skills/blob/063bee94c3f4df8453406c830b0a7df0f2860278/skills/web-design-guidelines/SKILL.md) | 仓库 HEAD `063bee94c3f4df8453406c830b0a7df0f2860278`；元数据版本 `1.0.0` | [README 的 License 段](https://github.com/vercel-labs/agent-skills/blob/063bee94c3f4df8453406c830b0a7df0f2860278/README.md#license) 声明 MIT；常见根目录及同目录 LICENSE 路径未取得全文 | 审查入口，不是完整设计方法 |
| [Skill 引用的 command.md](https://github.com/vercel-labs/web-interface-guidelines/blob/434b7f91364665f2f733b310ec54809bf8f37937/command.md) | 独立仓库 HEAD `434b7f91364665f2f733b310ec54809bf8f37937` | [LICENSE](https://github.com/vercel-labs/web-interface-guidelines/blob/434b7f91364665f2f733b310ec54809bf8f37937/LICENSE) 为完整 MIT，Vercel Labs 2025 | 可定位到文件行的代码审查清单 |
| [Vercel 官方 Web Interface Guidelines](https://vercel.com/design/guidelines) | 动态网页，无页面版本号；与上述独立仓库关联 | 页面未取得独立许可声明，不从 GitHub MIT 自动外推 | 解释规则语境与品牌偏好边界 |
| [WCAG 2.2 固定发布版](https://www.w3.org/TR/2024/REC-WCAG22-20241212/)；[APG 模态框模式](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/) | WCAG：W3C Recommendation，2024-12-12；APG 为动态指导页 | [W3C 文档许可](https://www.w3.org/copyright/document-license-2023/) | 用规范和模式校核无障碍底线；APG 不等于 WCAG 合规认证 |

上述三个 SHA 是本次读取时的仓库快照，不冒充目标文件的最后修改提交。GitHub API 因匿名限流未能取得文件级提交信息，已改用只读 `git ls-remote` 核对 HEAD，并读取固定 SHA 的 raw 文件。没有用 star、下载量或 README 宣传作为效果证据。两个核心来源已提供互补视角，不为数量增加第三套风格指令。

## 原文值得吸收的部分

Anthropic 当前原文把实际题材、用户语言、信息性结构、克制和截图自评放在一起；反对无用途装饰，也保留有理由的风格选择。文字应帮助操作，动作名称前后一致，失败和空态给方向。它也含设计工作室式的独特视觉、hero 和事前设计计划要求；这部分适合新建品牌页面时参考，不应自动覆盖现有工作台。[原文](https://github.com/anthropics/skills/blob/dbd4588f9e1033efb41dad4bef2f7947c8993d44/skills/frontend-design/SKILL.md)

Vercel Skill 要求另取最新审查规则并输出代码位置。所引清单覆盖语义、键盘焦点、表单、长内容、空态、动效与状态。采用时应区分可测故障与偏好，不把完整清单当作每次小改动的强制流程。[Skill](https://github.com/vercel-labs/agent-skills/blob/063bee94c3f4df8453406c830b0a7df0f2860278/skills/web-design-guidelines/SKILL.md)、[清单](https://github.com/vercel-labs/web-interface-guidelines/blob/434b7f91364665f2f733b310ec54809bf8f37937/command.md)

官方网页把 Title Case、`&` 等列为 Vercel 自身偏好，明确不普适；清单却将它们平铺为文案规则。它与 Anthropic 的 sentence case 也不一致。中文后台应服从产品词汇和上下文，不机械移植英文排版。官方页还存在防止移动输入自动缩放的示例与不禁用缩放的原则张力，因此原文也需要校核。[Vercel 官方页](https://vercel.com/design/guidelines)

## 建议落实的 8 项决策及验法

本表是本次研究提出的候选，不声称来源已经证明效果。是否写入 Solo、以何种强度写入，应结合冻结试验结果裁决。

| 决策 | 工作台／后台的落点 | 验证方式 | 强度与来源 |
|---|---|---|---|
| 1. 先识别页面的主要工作 | 从现有流程推断操作者、对象和下一步；列表、编辑器、审批页直接展示业务对象。营销页才按传播目标考虑 hero、品牌叙事、展示动效 | 首屏是否能找到当前对象、主要操作和当前状态；没有为填版面而造的业绩数字 | 默认判断；不要求用户先填写设计问卷。结合任务推断，受 Anthropic 真实内容原则启发 |
| 2. 每段文字都要承担具体用途 | 保留命名、操作指引、限制、结果与恢复；删除常驻宣传、教学口吻和与决策无关的实现说明。开发者工具的环境／API 信息若决定操作，仍然有用 | 审读实际渲染文字，包括弹窗、空态、切换后内容和 iframe；逐处问“删掉后用户会失去什么必要信息” | 默认文案审查；不建立“提示／权限／环境”等禁词表 |
| 3. 结构表达业务关系 | 需要比较就用可扫描的行列，需要详情就用分区；卡片、编号、徽标只有表达分组、顺序或状态时使用 | 用真实或明确标注的代表性数据走一次比较、筛选、编辑；确认结构帮助任务，空白和装饰没有挤掉关键字段 | 默认判断；不禁卡片，也不规定唯一布局 |
| 4. 视觉先保持可读和一致 | 沿用现有组件和品牌；控制层级、间距、对齐和状态色。新品牌页可选择鲜明视觉，但不强制换字体、深色、渐变或大字号 | 看实际截图中的主次、对齐、截断；检查文字／控件对比度和键盘焦点，不用“高级感”代替证据 | 基础质量；视觉探索可选，来源为设计原则及 WCAG 相关准则 |
| 5. 删冗余时保护操作安全信息 | 权限不足、连接失败、环境未就绪、未保存、失败和无结果分别表达原因与可行下一步；不能通过隐藏提示、伪造成功把页面变干净 | 至少构造与本次改动有关的失败／权限反例，检查提示仍出现且恢复入口有效；输入错误与字段对应，状态可被辅助技术感知 | 默认反例；[WCAG 3.3.1／3.3.3](https://www.w3.org/TR/2024/REC-WCAG22-20241212/#error-identification)、[4.1.3](https://www.w3.org/TR/2024/REC-WCAG22-20241212/#status-messages) |
| 6. 精致必须包含可操作性 | 动作和结果同词；导航与按钮语义正确；键盘可完成主流程。动效解释用户操作的结果，不妨碍下一步 | 用键盘完成本次主流程；有模态框时检查进入焦点、Tab 循环、Esc、关闭后焦点返回；启用减少动态效果检查 | 按受影响控件选查；[APG 模态框](https://www.w3.org/WAI/ARIA/apg/patterns/dialog-modal/) |
| 7. 依据目标设备检查真实状态 | 后台优先覆盖目标桌面宽度；典型可用 1920／1366，移动端按实际需求覆盖。长名称、密集数据与空列表应能承载 | 保存关键状态截图并读图；查横向溢出、遮挡、裁切和切换后的布局。表格必要横滚可以保留，不能用全局隐藏溢出掩盖故障 | 随变更范围选取证据；不是每次都跑全设备矩阵 |
| 8. 只吸收可解释的规则，不叠加外来流程 | Solo 保持短入口，前端检查按需引用；品牌偏好、强制独特性、固定色数、每次联网取全套规则、全部 state 都写 URL 均不普遍强制 | 后续用冻结样例比较任务完成、冗余文案、保留关键提示、布局和交互故障；失败时能定位具体规则，再决定采用／撤回 | 当前只列候选，不修改冻结试验，也不以来源声望判胜 |

## 明确舍弃与保留可选

- 不因界面是后台就删除所有说明。复杂输入的格式、费用、不可逆后果、权限和恢复信息属于任务本身；适合就近说明或按需展开。
- 不把任何特定字体、配色、圆角、阴影或排版风格写成 Solo 的通用审美。英文大小写、`&`、全局深链、列表超过固定数量就虚拟化等，应依产品、语言、性能测量与交互需要判断。
- 不把“独特”当作后台验收目标；可预期、好扫描、能完成工作优先。营销页可以追求记忆点，但仍需操作与异常验证。
- 不把截图、静态代码扫描或某个清单通过宣称为完整 WCAG 合规。研究本身未验证任何候选界面，也未证明上述决策优于当前 Solo。

本报告全部为归纳与取舍，没有复制外部 Skill 文段。若后续实际引入其文本或代码，再按相应来源许可保留所需许可与归属；Vercel Skill 的许可全文证据缺口保留在清单中。
