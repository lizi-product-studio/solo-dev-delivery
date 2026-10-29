# DeepSeek Harness 和其他自定义 Harness

先区分模型与宿主：使用DeepSeek模型不说明Harness的Skill目录、插件API、浏览器、终端或会话能力。不同Harness版本和自建实现可用同一份Skill，具体注册机制须查当前宿主文档/配置；本仓库没有认证任何特定DeepSeek官方插件接口。

默认按[通用安装](../generic/README.md)导出自包含Skill，再指定Harness真实的Skill搜索根目录：

```sh
python /absolute/package/agi-like/scripts/install_agent.py --destination /absolute/harness/skills --dry-run
python /absolute/package/agi-like/scripts/install_agent.py --destination /absolute/harness/skills
python /absolute/package/agi-like/scripts/install_agent.py --destination /absolute/harness/skills --verify
```

上述路径是占位符，不是DeepSeek统一默认路径。Harness只有规则/提示词入口时，显式加载包内SKILL.md，并把实际包路径提供给模型以读取按需引用；本地脚本只在Harness可用文件与Python工具时运行。单纯聊天也可复用判断方法，但执行保持未验证。

宿主用实际工具观察文件读写、shell、测试、浏览器、独立复核或外部动作权限，只报告当前任务相关部分；用包内 `capability_probe.py`检查明确要求。unknown不推断为支持，不从DeepSeek模型名猜能力或成本。

如确需持久恢复才使用完整系统AAP-0.1：将工具结果映射到真实请求，保存state/checkpoint，并实现可信证据核验回调。模型只能提交事件；原状态机仍核验候选、缺陷、未知副作用和验收绑定。默认Skill安装不需要这一步，也不附会话调度器或MCP服务。

实测须分别确认文件安装、客户端加载、自动选择及临时项目的真实修改/检查。当前自动化测试验证本地包和脚本，不代表DeepSeek Harness实机认证。
