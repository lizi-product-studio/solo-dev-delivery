# Portable Solo Delivery Kit

AAP-0.1是完整系统的兼容请求信封；0.11.0候选保留version 2状态门禁并提供有来源的场景选择、逐方法执行覆盖、候选反例重放和跨产物关系核验。独立Skill导出gate、challenge、本地关系检查、无状态能力预检和通用安装器，不携带旧session/状态机/契约示例；以下持久入口仅供完整系统在真正需要恢复/交接时使用。包装层不会自动增加模型判断、业务知识、账号权限或真实平台能力。

## 文件与入口

- `core/state_machine.py`：无模型、无网络依赖的 0.2 状态机；逐项验收要求宿主 `verify_evidence` 回调确认实际证据。旧 `state_version` 状态会以 `STATE_UPGRADE_REQUIRED` 拒绝，不自动迁移。
- `scripts/session.py`：仅做受限本地产物检查的 CLI，不调用模型、任意 shell、浏览器或外部服务。用 `init`、`next`、`check --check ID`、`finish` 推进；仅支持契约内的文本、JSON、CSV 检查，其他验收必须交给真实宿主验证。
- `core/local_checks.py`：上述 CLI 的只读检查器；只证明明确声明的机械属性，不证明语义、体验、授权或平台状态。
- `scripts/challenge.py`：不新建队列即可冻结候选、在临时副本运行审查者unittest、精确核对汇总及生成表格；改动依赖使旧结果失效。它运行受信Python代码，不是安全沙箱或独立身份认证。`--help`与Skill的`references/result-verification.md`给出范围和调用。
- `core/coverage.py`：把原文段落、声明结果和真实逐方法回执联系起来，优先补缺口、复用有效证据；未知/预算不足不放行，覆盖齐全后仍需宿主判断语义充分性。
- `core/json_relations.py`：精确的未知汇总与权威数据表格生成/比对；只证明声明的关系，不证明源数据、自由文本或业务口径正确。
- `schemas/aap-0.1.json`：AAP-0.1 请求信封 schema；0.2 状态门禁及新增操作见 `PROTOCOL.md`。
- `adapters/example_adapter.py`：内存 JSONL 演示，不执行工具、不持久化 state，也不是可直接接入的平台适配器。
- `scripts/export_skill.py`（系统源码包入口）：从完整系统目录生成自包含 Skill 文件夹和 ZIP；导出内容不带个人偏好或本机运行时。
- `scripts/capability_probe.py`：可选无状态预检；只检查本次显式需要的能力，输出READY/BLOCKED/NEEDS_CONTEXT，不创建任务状态、不执行工具。与持久核心共享三态归一化。
- `scripts/host_replay.py`：校验宿主安装、加载、调用、分流和读回回执是否绑定当前候选版本，并重算原始证据哈希；不执行宿主、不解释截图，也不把模型自报当作兼容性证明。
- `scripts/blind_review.py`：为冻结评测生成不泄露臂信息的证据包，并校验独立判分回执、候选版本和证据绑定；未知保持未知。
- `scripts/eval_contract.py`：校验冻结的0.13评测合同和JSONL运行回执，并输出保留unknown的描述性指标；不调用模型、不授予权限、不替代交付门禁。
- `../scripts/install_agent.py`：安装已导出的Skill目录；generic模式显式指定任意宿主的真实Skill根目录，ZCode/Kimi CLI支持目录快捷方式。

普通任务的核验入口是`python3 portable-agent-kit/scripts/challenge.py --help`。本机完整系统确需持久恢复时的兼容示例：

```sh
python3 portable-agent-kit/scripts/session.py init --session /tmp/demo-session --workspace /path/to/project --contract portable-agent-kit/examples/contracts/software.json
python3 portable-agent-kit/scripts/session.py next --session /tmp/demo-session
python3 portable-agent-kit/scripts/session.py check --session /tmp/demo-session --check AC1
python3 portable-agent-kit/scripts/session.py check --session /tmp/demo-session --check AC2
python3 portable-agent-kit/scripts/session.py finish --session /tmp/demo-session
python3 scripts/export_skill.py /path/to/new-parent/solo-dev-delivery
```

导出目录须为尚不存在的 `solo-dev-delivery` 目录。运行器将 session 保存在工作区外；不要删除或覆盖已有 session。Python 3.9+，仅标准库。

## 宿主接入

通用安装入口适用于任何提供兼容Skill目录的宿主，包括DeepSeek Harness、自建Agent及各种CLI/IDE；实际目录和注册机制由宿主文档/配置确认。ZCode/Kimi有已核对目录的快捷方式。安装后脚本和引用自包含，不需要额外安装Lite或带入个人系统。详见[宿主安装](../skill/solo-dev-delivery/references/host-integration.md)。

**没有完成这些平台的实机加载或自动分流验证**。宿主提供真实工具、权限和证据；需要持久恢复时才额外实现存储和AAP验证回调。文件安装通过不代表客户端加载，能力清单只是声明，参考适配器只是内存演示。

完整系统的本机兼容入口默认使用Codex；独立包可由用户选择的兼容宿主加载。外部写入、身份操作、付款、发布和不可逆操作仍需具体授权及真实宿主能力。非软件业务只复用目标澄清、证据判断、验证与失败恢复方法；不得无端套用软件队列或软件验收清单。

此包提供本地Skill目录安装与校验，不提供HTTP服务、消息队列、后台scheduler或自动授权。实际交付范围以当前项目验收和目标宿主的真实证据为准。
