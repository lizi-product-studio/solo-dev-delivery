# Solo Dev Delivery 0.11.2 候选包使用说明

从[候选分支安装包](https://github.com/lizi-product-studio/solo-dev-delivery/blob/feat/customer-retro-self-review-2026-10-26/packages/solo-dev-delivery-0.11.2.zip)下载并解开 `solo-dev-delivery-0.11.2.zip`。保留整个 `solo-dev-delivery/`，不要只复制 SKILL.md。也可克隆该分支，直接使用 packages/ 中的同一ZIP。

包的SHA256与[校验文件](https://github.com/lizi-product-studio/solo-dev-delivery/blob/feat/customer-retro-self-review-2026-10-26/packages/solo-dev-delivery-0.11.2.zip.sha256)核对。包由规范Git源文件构建，CI在测试成功后重新导出、安装核验并逐字节比较，不依赖Actions制品存储配额；它仍是未合并main的候选。

先确认目标 Agent 实际使用的 Skill 搜索根目录，再执行（Python 3.9+，按本机使用 python 或 python3）：

```sh
python solo-dev-delivery/scripts/install_agent.py --destination /absolute/host/skills --dry-run
python solo-dev-delivery/scripts/install_agent.py --destination /absolute/host/skills
python solo-dev-delivery/scripts/install_agent.py --destination /absolute/host/skills --verify
```

安装到该根目录下的 `solo-dev-delivery/`。不同版本先把旧目录备份到搜索根目录外，再运行安装器；相同字节重复安装不改文件。不要同时保留多个可被发现的副本。`BUNDLE.json` 校验内容一致性，不认证作者身份，请从可信仓库获取。

ZCode 可使用 `--host zcode`，默认 `~/.zcode/skills`；Kimi CLI 可用 `--host kimi`，默认 `$KIMI_CODE_HOME/skills`（未设置为 `~/.kimi-code/skills`）。显式 `--destination` 优先。DeepSeek Harness、自建 Agent 或其他 CLI/IDE 用其已确认目录，不按模型名猜路径。

按宿主要求刷新或开始新会话。ZCode 在 Settings → Skills 刷新启用，可用 `$solo-dev-delivery`；Kimi 可用 `/skill:solo-dev-delivery`。其他宿主用其实际调用方式。支持自动选择的宿主需要真实加载元数据；仅支持提示词的宿主须显式加载 SKILL.md 并提供目录位置。

可以先给 Agent 一个可回退的小改，要求完成后读回；再给一个有可复现失败的修复，观察它是否实际检查、在授权内恢复，并继续原目标。不要用“文件校验通过”代替宿主发现、自动选择或真实交付验证。

低风险任务不强制建计划、队列或子 Agent。多步骤任务按风险使用工程方法、验证和恢复引用。包内 gate/challenge/能力预检均为可选辅助；已有门禁和失败不能绕过。没有工具或实际目标环境时，结果保留未验证。

安装不会启用 hooks、定时器或后台进程，不传播个人授权，也不增加账号权限。新文件不会自动替换所有活动聊天的旧上下文。完整说明及证据：[候选分支](https://github.com/lizi-product-studio/solo-dev-delivery/tree/feat/frontend-quality)、[宿主接入](https://github.com/lizi-product-studio/solo-dev-delivery/blob/feat/frontend-quality/skill/solo-dev-delivery/references/host-integration.md)、[验证记录](https://github.com/lizi-product-studio/solo-dev-delivery/blob/feat/frontend-quality/VALIDATION.md)。
