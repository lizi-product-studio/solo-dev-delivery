# 通用宿主接入

默认导出一个自包含的 `solo-dev-delivery` Skill，复用宿主已有工具。适用于DeepSeek Harness、自建Agent、CLI、IDE或其他能读取Skill/规则的环境；不要求先接入状态机或保存JSON会话。

```sh
python scripts/export_skill.py /absolute/new-parent/solo-dev-delivery
python /absolute/new-parent/solo-dev-delivery/scripts/install_agent.py --destination /absolute/host/skills
python /absolute/new-parent/solo-dev-delivery/scripts/install_agent.py --destination /absolute/host/skills --verify
```

在宿主当前文档/配置中确认真实搜索目录，按其机制刷新、注册或显式加载。只有提示词注入的环境可读SKILL.md并提供实际目录，按需加载引用。只有聊天输入输出的宿主可讨论目标与方案，代码执行、浏览器、部署等结果保持未验证。

需要未知能力时调用包内 `scripts/capability_probe.py --capabilities host.json --require CAPABILITY`，只检查实际任务需要的部分。它不实际探测、不授予权限；READY不证明业务完成。完整步骤和冷启动验证见[宿主安装与预检](../../../skill/solo-dev-delivery/references/host-integration.md)。

仅当任务明确需要跨进程恢复、既有宿主记录不足时，完整系统才使用 `SYSTEM_PROMPT.md`、`PROTOCOL.md`、受信 `verify_evidence` 回调与 state/checkpoint。已有队列和持久任务继续原状态及门禁，不能通过轻量入口绕过失败或锁。
