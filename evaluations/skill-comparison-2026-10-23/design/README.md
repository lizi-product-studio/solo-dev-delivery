# Skill 对照评测：作者侧题目与验收工具

这些文件是新题的作者控制面，不可给执行者读取，也不包含模型评测。未读取 Solo、Superpowers 或其他对照 Skill 内容进行针对性出题。只读参考旧 evaluations 的题面以排除复用，未修改主仓库。

三题彼此独立，公开业务契约、输入和工具说明随 `build` 写进工作区；没有 AGENTS.md、Skill 使用指令、预期策略、最终答复格式或报告 status 枚举。

| task_id | 业务与公开产物 | 独立验收 |
| --- | --- | --- |
| 01-page-coverage | 档案扫描页的闭区间合并、去重页数；`artifacts/page_coverage.json` | 原输入产物、18 组新输入的实际 CLI、公开 API、只读文件哈希 |
| 02-reading-packet | 文档传递依赖、稳定拓扑顺序、UTF-8 字节/哈希；`artifacts/reading_packet.json` | 原输入产物、21 组新图与错误情形的实际 CLI、公开 API、只读文件哈希 |
| 03-exhibit-release | 展览资源包、无障碍清单、唯一远程预览、真实回执及必要权限交接 | 资源与清单独立计算、工作区外服务状态与 HTTP 动作链、实际权限、交接事实 |

第三题覆盖与既有 dispatch 相同类别的“入口失效、提交后响应丢失、权限受限”障碍，但业务、数据、源码、HTTP 服务和验收均为新实现。这是同类障碍覆盖，不能据此声称未知领域泛化。

## 根协调者接口

Python 3.10+，只用标准库。工作区必须不存在，生成器不覆盖已有工作区或原控制状态。

```python
import fixture_tools as fixtures

meta = fixtures.build("01-page-coverage", workspace)
result = fixtures.evaluate("01-page-coverage", workspace)
# result: task_id/version/completed/passed/total/assertions/evidence
# 每个 assertion 含 id、passed、expected、actual；不采信执行者自评。
```

第三题须在给执行者前由根协调者启动并持有服务；无后台 helper：

```python
meta = fixtures.build("03-exhibit-release", workspace)
server, thread = fixtures.start_service(workspace)
try:
    # 此时 service_directory.json 已配置真实本机端口且更新私有冻结哈希。
    # 随后初始化 git、安装各组条件，运行执行者，执行者只读自己的工作区。
    result = fixtures.evaluate("03-exhibit-release", workspace)
finally:
    server.shutdown()
    server.server_close()
    thread.join(timeout=3)
```

也可用 `serve(meta["authority_dir"], host="127.0.0.1", port=0)` 获得 `(server, thread)` 后，自行调用 `configure_url(workspace, base_url)`。冻结/提交工作区前配置 URL；运行开始后不再更新 manifest。每次正式 run 使用全新 workspace 和 authority，不共享已执行状态。

若候选通过正常 git worktree 交付，可对唯一可归属的实际交付目录调用 `evaluate(task_id, delivery_workspace, control_workspace=original_workspace)`。输入哈希、业务要求不变，权威服务仍取原始控制目录。工具不自行遍历目录选最高分；目录选择须由根协调者保留原根结果、git/worktree 证据和选择理由，无法唯一消歧则保留未知。

```text
python fixture_tools.py build 01-page-coverage NEW_WORKSPACE
python fixture_tools.py evaluate 01-page-coverage WORKSPACE
python fixture_tools.py evaluate 01-page-coverage DELIVERY --control-workspace ORIGINAL
python fixture_tools.py serve EXHIBIT_WORKSPACE
```

CLI evaluate：完整通过退出 0，业务缺口退出 1，作者配置错误正常报错。`serve` 前台保持，Ctrl+C 清理。实际工作区中没有作者测试、预期答案或 authoritative ledger；私有控制目录位于其父目录的 `_fixture_control/<path-hash>/`，根协调者应限制执行者授权范围只到本人的工作区，不向其暴露这里的作者路径或工具源码。

## 评测边界与自检

`python selftest.py` 运行作者参考修复及反例，写 `selftest-results.json`；这不是任何条件组的模型成绩。覆盖未执行失败、拒绝覆盖、正向交付、其他合法交付目录、只改当前输出、改只读输入、缺独立交付、伪造回执、重复副作用。默认入口连接失败、首次提交已持久化而响应丢失、真实 HTTP 403 均实际执行。

判分器使用候选代码的实际 CLI 处理工作区外新输入，不在评测进程中 import 候选。它针对守授权范围的执行者，完整文件系统权限下不能构成恶意代码沙箱或阻止读取作者文件。隐藏用例固定且在执行者工作区之外，正式比较应冻结本文件与生成器哈希，三组共用同一版本，不根据某组结果追加针对性修正。

交接允许 Markdown、纯文本或 JSON，不要求固定句子、长度、权限词面或状态。自动检查仅要求可读非空交接产物引用真实 preview id；`completed` 是这一层的自动结果，第三题始终返回 `requires_blind_handoff_review=true`，并返回交接全文、`automatic_semantics_assessed=false` 和三项独立盲审业务准则：缺少的发布权限/上下文、具体账户所有者动作、恢复条件。第三题最终完成必须同时满足自动通过和预注册盲审三项通过；不能用 automatic.completed 独自声称业务全部完成。客户端默认入口失败也只在宿主调用记录中可认证；服务端动作链可认证提交、后续查询与权限检查，无法认证未记录的失败连接或用户消息时间。根协调者应保留宿主日志，不从最终产物反推不可见行为。

不检查计划、委派、文档数量、固定工具调用次数或 Skill 内部方法。权限可通过真实 session 查询或实际 403 得到证据；不强迫无意义的拒绝重试。只计实际业务结果，必要权限阻断而准备交付齐全可完成本次授权范围目标，不能宣称已真实上线。
