# Solo Dev Delivery

让 Agent 在有效授权内持续推进交付：按任务选择方法，遇阻自行诊断恢复，用实际结果验收，并在需要时保留可接续的项目状态。

**0.11.1 是前端专项候选，位于 `feat/frontend-quality`，尚未合并 main。** 它在0.11.0上增加按需加载的产品界面方法：按任务安排文案与密度、保留真实状态、以实际页面和操作验收。通用交付、遇阻恢复与平台能力边界继续沿用；不宣称顶级审美、后端高并发或长期无人经营已经得到证明。证据与失败记录见 [VALIDATION.md](VALIDATION.md)。

## 适用方式

| 任务 | 选择足够的方法 |
| --- | --- |
| 简单问答、一步低风险小改 | 直接完成与必要读回，不建立交付流程 |
| 行为修复、软件或业务多步骤交付 | 按影响使用计划、定向验证和风险审查 |
| 跨模块或高损失变化 | 按需要隔离、拆分、独立审查，复用已有有效控制 |
| 用户授权的项目维护与持续改进 | 从真实失败、反馈和任务记录选下一项，交付后核项目结果 |
| 工具错误、环境障碍或结果未知 | 核实际失败、尝试授权内恢复；未知副作用先对账，独立工作继续 |

入口是 [SKILL.md](skill/solo-dev-delivery/SKILL.md)。方法按需读取，不默认执行固定角色链、TDD、工作树或持久队列。普通技术决定由 Agent 推进；确需本人身份、额外权限、必要业务决定或已证实无可行恢复路径时，及时请求最小用户动作。

## 克隆并导出

Python 3.9+，辅助工具仅用标准库；只加载 Markdown 不要求 Python。以下命令选择本候选分支：

```sh
git clone --branch feat/frontend-quality https://github.com/lizi-product-studio/solo-dev-delivery.git
cd solo-dev-delivery
python -X utf8 scripts/export_skill.py /absolute/new-parent/solo-dev-delivery
```

生成自包含目录及 `solo-dev-delivery-0.11.1.zip`。目标目录和同名 ZIP 必须不存在，以保护已有产物。包内含相对引用、验证脚本、安装器、清单及许可；不包含个人偏好、客户数据、原始聊天、runtime、调度器或旧队列状态。也可直接下载[版本化安装包](packages/solo-dev-delivery-0.11.1.zip)及[校验值](packages/solo-dev-delivery-0.11.1.zip.sha256)。CI在全部测试成功后重建、安装核验并逐字节比对当前版本；旧版本文件保留用于复现。下载不依赖Actions制品配额，不创建GitHub Release或合并分支。

## 安装到目标 Agent

先从宿主当前配置或官方说明确认实际 Skill 搜索根目录，再执行：

```sh
python /absolute/export/solo-dev-delivery/scripts/install_agent.py --destination /absolute/host/skills --dry-run
python /absolute/export/solo-dev-delivery/scripts/install_agent.py --destination /absolute/host/skills
python /absolute/export/solo-dev-delivery/scripts/install_agent.py --destination /absolute/host/skills --verify
```

安装到该根目录下的 `solo-dev-delivery/`。相同字节重复安装不重写；不同版本先备份到搜索根目录外，再安装，不覆盖用户修改。安装失败不会把未完成的包发布成可发现的 Skill。哈希核对包内一致性，不认证下载来源。

ZCode、Kimi CLI 可分别用 `--host zcode`、`--host kimi`；显式 `--destination` 优先。DeepSeek Harness、自建 Agent 和其他 IDE/CLI 使用通用模式，不猜模型供应商对应的目录。仅支持提示词加载的宿主可显式读取 `SKILL.md` 并提供包位置。目录与调用方式见 [宿主接入](skill/solo-dev-delivery/references/host-integration.md)。

**文件安装、客户端发现、自动选择和真实交付是不同结论。** 自动选择需要宿主实际加载元数据与指令。Skill 不增加工具、账号、权限或后台能力，不承诺所有平台下载后立即自动生效。

## 验证与遇阻恢复

低风险任务复用现有检查。有候选绑定或正式验收需要时，再用包内 `scripts/gate.py` 或 `scripts/challenge.py`；完整源码入口在 `portable-agent-kit/scripts/`。失败、未知、缺项和证据过期不能当作通过，UI、外部服务和生产结果需要对应环境的实际观察。

- [交付门禁](skill/solo-dev-delivery/references/execution-gate.md)：原文、候选、验收与证据绑定，按业务决策选择检查。
- [结果核验](skill/solo-dev-delivery/references/result-verification.md)：独立本地检查、证据复用、失败优先和按需反例。
- [恢复与最小交接](skill/solo-dev-delivery/references/blocker-recovery.md)：技术排障、等价入口、未知副作用与必要用户动作。
- [产品界面与前端设计](skill/solo-dev-delivery/references/frontend-quality.md)：按任务选择文案、密度、视觉与交互检查；不统一模板、不用删重要提示换取简洁。
- [项目巡检与接续](skill/solo-dev-delivery/references/project-operation.md)：选任务、避免重复开工、投入边界和跨会话恢复。

可选能力预检只检查任务实际依赖，不执行工具或创建 session：

```sh
python portable-agent-kit/scripts/capability_probe.py --capabilities host.json --require fs.read --format text
```

`supported / unsupported / unknown` 保留三态，输出 `READY / BLOCKED / NEEDS_CONTEXT`；READY 不代表任务完成或获准外部操作。格式见宿主接入说明。

Codex Stop 定义由 `gate.py install-hook` 准备，须经宿主审核、信任并加载才生效。移动包或解释器后需复核定义，不能把安装等同于自动拦截。更新 Skill 不启用 hooks、定时或后台任务，也不向其他聊天发送指令。

## 完整系统与兼容

源码保留 `scripts/delivery.py`、SQLite 队列、原生审查登记、旧 CLI worker、业务接手和 portable session；明确需要正式状态、锁、客户闭环或持久交接时才使用。已有任务继续原门禁和锁，不迁移活动运行，不以轻量流程绕过已知失败。见 [本机软件交付](skill/solo-dev-delivery/references/software-delivery.md)和 [Portable Kit](portable-agent-kit/README.md)。

本机偏好与授权属于所在实例，不随独立包传播；历史授权不扩展到新项目的发布、支付、通信或经营承诺。原生用量由宿主提供，没有数据时保留未知，不能填零或宣称节省成本。

## 测试与证据

```sh
python -X utf8 -m unittest discover -s portable-agent-kit/tests -v
python -X utf8 -m unittest discover -s tests -v
```

完整原生兼容套件含 POSIX CLI fixture；Windows 以 CI 明确执行的交付、停止和所有权用例为准。系统和 Python 版本范围见 [CI 定义](.github/workflows/validate.yml)。CLI 导入开项 JSON 另需 Node.js。

[验证记录](VALIDATION.md)保留首次失败、撤回候选、实际范围、行为样例及成本；[三组18次对照](evaluations/skill-comparison-2026-10-23/REPORT.md)、[0.11.0统一验收](evaluations/release-0.11.0/README.md)、[仓库试点](evaluations/repository-pilot/2026-10-23.md)与[遇阻评测](evaluations/blocker-recovery/README.md)提供复现资产。对照中三组质量均6/6；Solo相比无额外Skill未证明成功率收益且用量增加，不把流程完整当成净收益。下载后的独立说明见[INSTALL.md](INSTALL.md)。

## 来源

整合 Superpowers Lite 的分流、系统调试和风险审查，保留 Solo 的候选绑定、验证、状态和授权边界。[整合说明](INTEGRATION.md)与[第三方许可](THIRD_PARTY.md)保留来源；`SOURCE_SNAPSHOT.json` 记录规范 Git 源文件与版本化分发摘要。
