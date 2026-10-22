# 栗子的软件交付协作

目标是把真实项目做好，减少用户重复提醒、返工和日常操心。当前个人实例目录：`/Users/lizi/Documents/Codex/Systems/solo-delivery`。用户保留方向和经营政策控制权，系统在有效授权范围内逐步承担交付工作；当前能力不等于已实现无人经营。

## 0.8.0：减少默认附加开销，保留主Agent的验收责任

完整保留0.7.0的Lite工程指引、宿主安装/预检及兼容修复。普通任务优先用原生检查；verify只为隔离/证据归属需要，--probes只作有具体疑点的可选诊断。验收指导从原文真实入口、输入及使用方式出发，再按独立业务条件、恢复、临界与决胜关系补观察，停止前核剩余缺口。

最新一题的0.7.0/0.8.0对照计入复核后输出少9.46%、工具轮次少40%，两份产物均通过实际复核；但两份执行者仍漏查，且新版未严格执行提示中的先验验收顺序。因此没有证明主动边界选择已解决，不把本版宣传为自主验收达标。完整失败、返工、资源及限制见[实际验证](VALIDATION.md)。

## 保留0.7.0的Lite与宿主整合

当前Skill吸收Superpowers Lite的工程分流、系统调试与风险审查，作为一个按需引用，沿用现有证据回执和停止判断。跨平台默认仍是一个自包含的solo-dev-delivery包；不需要同时安装四个Lite Skill，不增加第二套DONE判定或默认持久流程。[取舍与验证](INTEGRATION.md)记录具体合并边界与第三方来源。

从源码导出干净包，再安装到目标宿主实际的Skill搜索根目录（Python 3.9+，按本机选择python/python3）：

```sh
python scripts/export_skill.py /absolute/new-parent/solo-dev-delivery
python /absolute/new-parent/solo-dev-delivery/scripts/install_agent.py --destination /absolute/host/skills --dry-run
python /absolute/new-parent/solo-dev-delivery/scripts/install_agent.py --destination /absolute/host/skills
python /absolute/new-parent/solo-dev-delivery/scripts/install_agent.py --destination /absolute/host/skills --verify
```

默认generic模式不猜任何平台路径，支持DeepSeek Harness、自建Agent及各种CLI/IDE的兼容Skill目录；具体注册机制由宿主确认。ZCode/Kimi CLI可改用 `--host zcode` 或 `--host kimi`。不支持Skill目录的宿主可显式加载Markdown并提供包路径。安装校验不代表平台真实调用，完整说明见[宿主安装与无状态预检](skill/solo-dev-delivery/references/host-integration.md)。

无状态预检只在任务依赖未知能力时使用，不创建session：

```sh
python portable-agent-kit/scripts/capability_probe.py --capabilities host.json --require fs.read --format text
```

READY/BLOCKED/NEEDS_CONTEXT分别表示所声明要求有支持、明确不支持、或仍未知；没有实际工具观察不能声称能力已验证。导出包也包含该工具、安装器和许可，所有引用/脚本可随目录移动；不携带个人偏好、客户数据或runtime。

## 默认轻量，Codex 原生优先

新软件聊天通过本机 `~/.codex/AGENTS.md` 加载个人偏好和 `solo-dev-delivery` Skill。主会话直接处理小改，按需使用原生子Agent、会话管理和适用工作树，不再强制为普通委派嵌套启动CLI或填写完整任务契约。资源与验证投入由实际影响决定。

需要范围确认、持久交接、客户状态或正式验收门禁时才使用本地队列。已在队列中的任务保留原状态、锁、失败记录和质量要求。入口是 `python3 scripts/delivery.py`，任务参数由Agent填写，用户无需学习命令或重复批准已授权步骤。

## 保留有实际作用的部分

- `knowledge/preferences.json`：用户明确偏好、来源、适用范围和被替代决定。
- `knowledge/methods.json`：从真实项目提炼的方法与反例；候选不冒称通用经验。
- `review_engine.py`：将候选版本、实际检查、缺陷及证据绑定，阻止缺项、过期或未关闭问题的队列交付。
- `business_loop.py`及队列：在需要时保存客户范围确认、交付/验收、变更和撤回状态；本地发件箱尚未连接真实客户渠道。
- 项目自己的代码、决定和验收证据：技术检查、用户任务、体验及生产状态分别核实。

## 原生执行与兼容路径

普通工作及按需独立审查默认由Codex原生能力执行。正式队列的原生审核完成后，可用 `resource-native-review`登记真实宿主派发及完成证据；该命令不调用模型、不创建子进程、不管理第二套会话或超时器。候选、测试和缺陷门禁继续生效，身份由受信主会话核实，证据文件不是身份认证。

旧 `resource-run`、`codex_worker.py`及监督接口保留兼容，已有回执和任务无需迁移；宿主原生不可用或具体项目要求旧入口时可明确使用。`resource-plan`的固定模型档位及内部预算仍用于旧CLI路径，不冒充原生Agent的资源实测。原生用量交由Codex宿主管理，此处记录未知，不填0消耗。

## 按需验证工具与原生兼容

主 Skill 已采用相对路径定位。完整本机系统继续使用原生 Codex 与现有队列，软件细节移至按需读取的 `references/software-delivery.md`。跨业务任务按目标使用专业标准，不进入软件队列。

可以导出不包含个人偏好、客户事实、原始聊天或 runtime 的独立技能包：

```sh
python3 scripts/export_skill.py /absolute/new-parent/solo-dev-delivery
```

输出符合 [Agent Skills 格式](https://agentskills.io/specification) 的目录和 zip，带相对引用和标准库一次性核验工具。独立包不包含旧session、协议状态机或契约资产，避免普通任务进入持久登记。格式与本地冷启动可验证；Cursor、Kimi Code、DeepSeek Harness等的真实加载和工具接入仍需各宿主验证，不能宣称所有平台即装即用。

完整系统的`portable-agent-kit/scripts/session.py`继续保留init/next/check/finish兼容入口，只有任务明确需要持久恢复且宿主记录不足时才使用。它逐项读回本地产物、冻结输入摘要、检查真实哈希并在交付前重验。失败历史保留，缺项、未知或产物变化阻断；它不执行外部动作、不调用模型、不创建定时器。现有任务的状态、门禁和锁不变。

AAP-0.1 信封保持，内核状态改为 version 2。自定义宿主必须提供受信 `verify_evidence` 回调；任意 PASS 标签不再足够。旧 portable state 必须保留原历史并由宿主在当前产物上重新验证/创建新状态，禁止热改现有运行。原 Codex SQLite 队列未改。

本地检查只证明契约中可机械验证的属性，不能认证业务语义、体验、身份、实际发布或“弱模型无敌”。真实项目的首次完成、漏检、返工和成本才用于评估收益。

主Agent从用户原始痛点找可实际走的因果链：前置事实变化→下游业务结果→合法恢复与读回。中间字段正确不能代替下游拒绝或恢复；决策样例要让相邻政策产生不同结果。只补会改变交付判断的观察，不因有状态或多条政策自动建表。局部小改直接读回，真实App、小程序、网站按宿主真实界面/集成工具验收。关键人用结论需对实际结果或原始依据核实；没有未关闭缺陷及影响交付的未知才停止。

`portable-agent-kit/scripts/challenge.py verify`把新本地检查压成一次命令：自动绑定声明输入/产物、实际运行主Agent选出的unittest、保存完整逐方法证据，平时只打印短摘要。相同候选/脚本的当前全套证据复用，已知失败优先；局部PASS、UNKNOWN和STALE不能冒充全套完成。控制文件在候选外，失败历史保留；脚本不认证业务语义、独立身份、完整环境或生产验收。

关键本地Python链/决策可在同一命令加`--probes`：只在临时副本替换明确声明的代码，运行主Agent选出的同一业务观察；合理错误行为也能通过时返回`INSUFFICIENT`，不能把普通测试绿色当交付依据。断言识别才算有效，语法错误、执行异常、跳过和缺观察不算；遇首个缺口停止额外探针，修复后核查，相同证据复用。反例通过仅证明所选错误被识别，原文理解、选错反例、完整因果链及文稿仍由主Agent判断；非Python/界面使用宿主等价观察，不伪造证明。未增加模型、默认角色或定时器。

0.6.0的反例机制已验证所选断言的识别能力，但未见任务未证明整体收益；0.8.0将其保持为按需诊断。原文条件未被选择时，更多反例也无法补齐漏检。

既有`brief/assess`继续支持分批补证、覆盖争议和持久交接，作为按需工具；不同必要场景不能因共享宽泛结果而顶替。原`freeze/run/status`、精确`json.aggregate`及`json.table`兼容保留，原有队列门禁不变。详细参数按需见Skill的`references/result-verification.md`。

验证更新后清除旧导出、回滚副本和临时迭代目录，只保留当前有效版本及其验收记录；Git提交履历保留用于追溯。

## 按需查看

- [0.8.0实际验证](VALIDATION.md)：并行成果合并、两阶段未见任务、首次失败、复核与返工消耗。
- [协作入口](skill/solo-dev-delivery/SKILL.md)：任务选择、产品判断与交付责任。
- [主动审核](主动审核执行说明.md)：队列状态、缺陷扩查、版本和证据阻断。
- [资源与原生回执](资源调度执行说明.md)：原生登记格式和旧CLI兼容操作。
- [客户业务闭环](业务闭环执行说明.md)：仅相关客户接手任务需要加载。
- [Git提交历史](https://github.com/lizi-product-studio/solo-dev-delivery/commits/main/)：保留来源，不当作新项目授权。

## 运行边界与验证

只使用Codex。定时任务继续暂停；不会因加载技能自行启用。上线、真实资金、新增费用与外部承诺依项目授权。`DELIVERED`仅表示约定本地交付，开发版上传与正式发布分别验证。

Python 3.9及以上；开项JSON导入另需Node。无需新增依赖。`runtime/`保留私有数据和历史回执，备份SQLite使用其备份机制，不直接覆盖正在写入的数据库。

```sh
python3 scripts/delivery.py --help
python3 -m unittest discover -s tests -v
```

本机配置不会自动同步到其他电脑或云任务；已打开的聊天可能持有旧上下文。更新本系统不向其他聊天发送指令，不修改客户项目的AGENTS或代码。
