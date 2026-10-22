# Portable Solo Delivery Kit

AAP-0.1是完整系统的兼容请求信封；本包0.3.1保留version 2状态门禁并提供候选反例重放和跨产物关系核验。独立Skill只导出challenge及本地关系检查，不携带旧session/状态机/契约示例；以下持久入口仅供完整系统在真正需要恢复/交接时使用。包装层不会自动增加模型判断、业务知识、账号权限或真实平台能力。

## 文件与入口

- `core/state_machine.py`：无模型、无网络依赖的 0.2 状态机；逐项验收要求宿主 `verify_evidence` 回调确认实际证据。旧 `state_version` 状态会以 `STATE_UPGRADE_REQUIRED` 拒绝，不自动迁移。
- `scripts/session.py`：仅做受限本地产物检查的 CLI，不调用模型、任意 shell、浏览器或外部服务。用 `init`、`next`、`check --check ID`、`finish` 推进；仅支持契约内的文本、JSON、CSV 检查，其他验收必须交给真实宿主验证。
- `core/local_checks.py`：上述 CLI 的只读检查器；只证明明确声明的机械属性，不证明语义、体验、授权或平台状态。
- `scripts/challenge.py`：不新建队列即可冻结候选、在临时副本运行审查者unittest、精确核对汇总及生成表格；改动依赖使旧结果失效。它运行受信Python代码，不是安全沙箱或独立身份认证。`--help`与Skill的`references/result-verification.md`给出范围和调用。
- `core/json_relations.py`：精确的未知汇总与权威数据表格生成/比对；只证明声明的关系，不证明源数据、自由文本或业务口径正确。
- `schemas/aap-0.1.json`：AAP-0.1 请求信封 schema；0.2 状态门禁及新增操作见 `PROTOCOL.md`。
- `adapters/example_adapter.py`：内存 JSONL 演示，不执行工具、不持久化 state，也不是可直接接入的平台适配器。
- `scripts/export_skill.py`（系统源码包入口）：从完整系统目录生成自包含 Skill 文件夹和 ZIP；导出内容不带个人偏好或本机运行时。

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

DeepSeek Harness、zcode、Kimi Code、Cursor 和其他 Agent 的目录仅提供接入说明，**没有完成这些平台的实机加载或一致性验证**。宿主必须自行实现工具调用、持久化、权限及证据校验；逐项执行当前平台加载验证前，不得声称 Skill 已生效或平台接入已完成。能力清单是待核对的声明，不是工具探测结果或证据；不得仅凭 manifest 将能力标为已支持。参考适配器只是内存演示。

Codex 仍是本机默认执行体系；只有用户要求导出时才使用该包。外部写入、身份操作、付款、发布和不可逆操作仍需具体授权及真实宿主能力。非软件业务只复用目标澄清、证据判断、验证与失败恢复方法；不得无端套用软件队列或软件验收清单。

此包不提供 HTTP 服务、消息队列、后台 scheduler 或自动跨平台安装。实际交付范围以当前项目验收和目标宿主的真实证据为准。
