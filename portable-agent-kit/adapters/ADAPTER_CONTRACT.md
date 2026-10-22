# 宿主适配器契约（AAP-0.1 / 状态门禁 0.2）

平台接入说明不等于已实现或实机验证。宿主按本协议映射真实工具、保存状态并提供证据校验；不得把 manifest、模型自报或工具名称当作实际能力证明。

## 能力与执行

能力值为 `supported`、`unsupported` 或 `unknown`，报告须来自当前宿主可复核的探测或实际调用。`core.state_machine.probe()` 只归一化输入，不探测宿主。适配器应记录来源和观察时间；必要能力不是 `supported` 时阻断。实际工具调用由宿主授权并执行，核心不是权限沙箱。

建议逐项映射 `fs.read`、`fs.write`、`shell.exec`、`test.run`、`git.read`、`git.write`、浏览器/设备控制、checkpoint 持久化、证据校验和独立复核。缺能力时如实限制任务：纯文本只能分析和准备材料；无浏览器/设备时界面验收未验证；无持久化时不得声称可恢复；无外部授权和工具时不得实施外部动作。

## 证据校验回调

每项验收的 evidence 必须绑定 `check_id`、环境、task、revision、contract hash 和候选版本。接入 `apply(..., verify_evidence=callback)`，由可信宿主读取证据、重算 SHA-256、核对来源与契约预期，只有确实通过才返回 Python 布尔 `True`。缺失回调、异常或任何不匹配均须失败关闭。核心会清除模型提供的 `verified` 等标志，并在记录证据和 `finish` 时调用 verifier；只有当前逐项证据均验证通过才可交付。

工具结果 `UNKNOWN` 必须记录副作用并先取得后续可验证读回证据，再调用 `reconcile`。Finding 需关联受影响验收项；修复后提交新的当前证据并调用 `resolve_finding`。失败项重新执行检查，不可只重发旧 PASS。幂等键复用不同请求内容必须报冲突。

## 持久化和现有入口

生产宿主须原子保存 state、checkpoint、证据及幂等记录，恢复时核对 owner、revision、契约哈希、候选和活动写入者。`adapters/example_adapter.py` 仅为内存 JSONL 演示，不执行工具、没有宿主 verifier，也不持久化；不得用于宣称能力或交付。

`scripts/session.py` 是可选的本地产物 CLI：支持 `init`、`next`、按验收 ID 执行限定的文本/JSON/CSV 检查及 `finish`，通过本机回调复核证据。它不执行任意代码或外部动作；无法机械验证的语义、UX、业务判断和平台结果仍须由真实宿主完成。失败两次后应诊断、拆小或升级复核，不盲目重复。

模型档位与名称仅是宿主元数据。按任务风险选单 Agent 或独立复核；遇到环境、认证、权限、网络或事实问题先修复条件，仍无法证明则保持 `BLOCKED` / `NEEDS_INPUT`。平台规则、能力探测、持久恢复和真实任务链路都须在目标平台另行验证。
