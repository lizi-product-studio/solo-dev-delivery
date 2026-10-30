# AGI-like 1.9.2 安装说明

先了解[现有功能](README.md)；安装后的首笔任务、管理命令和API配置见[使用指南](USAGE.md)，后续缺口及放行标准见[迭代方案](NEXT_ITERATION.md)。当前为1.9.2阶段版，包含本地Codex原生MCP/hooks接入；其余待办按实际证据保留。

从[main分支安装包](https://github.com/lizi-product-studio/solo-dev-delivery/blob/main/packages/agi-like-1.9.2.zip)下载并解开 `agi-like-1.9.2.zip`。保留整个 `agi-like/`，不要只复制 SKILL.md。也可克隆该分支，直接使用 packages/ 中的同一ZIP。

包的SHA256与[校验文件](https://github.com/lizi-product-studio/solo-dev-delivery/blob/main/packages/agi-like-1.9.2.zip.sha256)核对。包由规范Git源文件构建，CI在测试成功后重新导出、安装核验和发布自检并逐字节比较，不依赖Actions制品存储配额；当前版本位于默认分支main。

先确认目标 Agent 实际使用的 Skill 搜索根目录，再执行（Python 3.9+，按本机使用 python 或 python3）：

```sh
python agi-like/scripts/install_agent.py --destination /absolute/host/skills --dry-run
python agi-like/scripts/install_agent.py --destination /absolute/host/skills
python agi-like/scripts/install_agent.py --destination /absolute/host/skills --verify
```

安装到该根目录下的 `agi-like/`。不同版本先把旧目录备份到搜索根目录外，再运行安装器；相同字节重复安装不改文件。不要同时保留多个可被发现的副本。`BUNDLE.json` 校验内容一致性，不认证作者身份，请从可信仓库获取。

ZCode 可使用 `--host zcode`，默认 `~/.zcode/skills`；Kimi CLI 可用 `--host kimi`，默认 `$KIMI_CODE_HOME/skills`（未设置为 `~/.kimi-code/skills`）。显式 `--destination` 优先。DeepSeek Harness、自建 Agent 或其他 CLI/IDE 用其已确认目录，不按模型名猜路径。

## Codex 原生接入的一次性配置

由Agent在本次授权内代办配置，保留原有Codex设置和其他hooks，并先备份config.toml。注册本地MCP服务，然后安装原生hooks：

```sh
codex mcp add agi_like_runtime -- /absolute/python /absolute/installed/agi-like/scripts/native_mcp.py
python /absolute/installed/agi-like/scripts/native_task.py install-hooks
```

把上面的解释器和脚本替换为本机实际绝对路径。为已经授权的这三个本地Runtime管理操作配置例行许可；没有设置时，在approval_policy=never的会话中可能被拒绝，即使服务已注册。其他工具和全局批准策略保持原有设置：

```toml
# 合并到已经由CLI创建的同一个配置段，不要重复添加同名表。
[mcp_servers.agi_like_runtime]
# 保留现有的command和args（指向已安装的Python及native_mcp.py）。
enabled_tools = ["native_start", "native_status", "native_verify"]
tools.native_start.approval_mode = "approve"
tools.native_status.approval_mode = "approve"
tools.native_verify.approval_mode = "approve"
```

如果现有配置已经使用每工具子表，应合并对应approval_mode，不能再重复定义。Codex要求审阅并信任准确的hook定义，可在 `/hooks` 核对本适配器；随后按宿主需要新开会话，使新增工具可见。用户日常只需提出目标，Agent负责确定范围、接入、验收和返修。

1.9.2已补齐阻断状态下的只读恢复入口，见[中断后的恢复](README.md#中断后的恢复)。现有hook命令不变，完整替换安装目录后，下一次hook进程即加载修复；无需全局关闭hook。已运行的MCP服务需按宿主方式重新加载才能使用新版代码。

按宿主要求刷新或开始新会话。ZCode 在 Settings → Skills 刷新启用，可用 `$agi-like`；Kimi 可用 `/skill:agi-like`。其他宿主用其实际调用方式。支持自动选择的宿主需要真实加载元数据；仅支持提示词的宿主须显式加载 SKILL.md 并提供目录位置。

可以先给 Agent 一个可回退的小改，要求完成后读回；再给一个有可复现失败的修复，观察它是否实际检查、在授权内恢复，并继续原目标。不要用“文件校验通过”代替宿主发现、自动选择或真实交付验证。

低风险任务不强制建计划、队列或子 Agent。多步骤任务按风险使用工程方法、验证和恢复引用。包内 gate/challenge/能力预检均为可选辅助；已有门禁和失败不能绕过。没有工具或实际目标环境时，结果保留未验证。

文件安装器本身不会启用 hooks、定时器或后台进程；上述原生接入由宿主另行授权配置，不传播个人授权，也不增加账号权限。新文件不会自动替换所有活动聊天的旧上下文。完整说明及证据：[main分支](https://github.com/lizi-product-studio/solo-dev-delivery/tree/main)、[宿主接入](https://github.com/lizi-product-studio/solo-dev-delivery/blob/main/skill/agi-like/references/host-integration.md)、[验证记录](https://github.com/lizi-product-studio/solo-dev-delivery/blob/main/VALIDATION.md)。
