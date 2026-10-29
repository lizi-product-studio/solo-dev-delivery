# Kimi Code CLI 接入

默认使用自包含Skill，按[通用安装](../generic/README.md)导出后运行：

```sh
python /absolute/package/agi-like/scripts/install_agent.py --host kimi
python /absolute/package/agi-like/scripts/install_agent.py --host kimi --verify
```

当前CLI的用户目录是 `$KIMI_CODE_HOME/skills`，未设置时为 `~/.kimi-code/skills`。`--destination`可指定项目级或其他真实Skill根目录，安装器不改Kimi配置。启动新会话，用 `/skill:agi-like`调用；菜单中实际发现和加载需要本机验证。

也可 `kimi --skills-dir /absolute/package-parent` 做本次会话加载：该目录下面应直接包含 `agi-like/SKILL.md`。这个参数替换本次自动扫描的用户/项目目录，不是永久追加；旧kimi-cli和其他产品界面可能不同，不能套用路径。

完整的安装保护、能力预检与实际调用验证见[宿主安装说明](../../../skill/agi-like/references/host-integration.md)。普通任务直接使用Kimi工具；只有明确需要持久恢复时才按完整系统PROTOCOL接入AAP-0.1，不为每个任务登记契约。

2026-10-22核对：[Agent Skills](https://www.kimi.com/code/docs/en/kimi-code-cli/customization/skills.html)、[CLI参数](https://www.kimi.com/code/docs/en/kimi-code-cli/reference/kimi-command)。当前测试不代表Kimi客户端实机加载已通过。
