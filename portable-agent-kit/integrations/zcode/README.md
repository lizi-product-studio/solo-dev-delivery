# ZCode 接入

按[通用安装](../generic/README.md)导出一个自包含 `agi-like` 包后运行：

```sh
python /absolute/package/agi-like/scripts/install_agent.py --host zcode
python /absolute/package/agi-like/scripts/install_agent.py --host zcode --verify
```

默认安装到 `~/.zcode/skills/agi-like`。在Settings → Skills刷新并启用，再用 `$agi-like`或Skills菜单调用。使用自定义根目录时加 `--destination`并在校验时重复使用。

这里复用ZCode原生Skill发现机制，不把包含个人偏好和客户队列的完整仓库打包成插件。只复制导出包，辅助脚本和引用随包携带。文件校验不代表菜单加载或自动选择，仍需临时项目实际运行。完整步骤见[宿主安装说明](../../../skill/agi-like/references/host-integration.md)。

2026-10-22核对：[ZCode Skill文档](https://zcode.z.ai/en/docs/skill)。插件商店分发可另按宿主规范封装干净导出，本轮未创建或实测marketplace。
