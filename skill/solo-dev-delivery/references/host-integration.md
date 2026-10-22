# 在不同 Agent 上安装和核对能力

一个平台无关的 `solo-dev-delivery` Skill，按宿主自己的 Agent Skills/规则机制加载。模型供应商不决定工具和权限；DeepSeek Harness、自建Agent、CLI或IDE需要由其实际宿主提供文件、终端、浏览器与身份能力。没有统一可验证的“所有平台一键生效”承诺。

## 先取自包含包

从完整源码目录运行（Python 3.9+，按系统使用 python 或 python3）：

```sh
python scripts/export_skill.py /absolute/new-parent/solo-dev-delivery
```

导出文件夹及zip包含Skill、按需引用、challenge、无状态能力预检、通用安装器和第三方许可。没有个人偏好、客户事实、runtime、持久session或状态机。解压后目录可移动；下文 S 表示这个目录的 scripts。普通纯Markdown加载不需要Python，辅助脚本需要。

## 通用安装：DeepSeek Harness、自建Agent及其他宿主

从宿主官方说明/当前配置找出真实 Skill 搜索根目录，再执行：

```sh
python S/install_agent.py --destination /absolute/host/skills --dry-run
python S/install_agent.py --destination /absolute/host/skills
python S/install_agent.py --destination /absolute/host/skills --verify
```

generic 是默认模式，必须指定目录；最终安装到其下的 `solo-dev-delivery/`。不猜 DeepSeek Harness 的路径、SDK或命令。使用不同模型的Harness可以复用同一包，工具调用与权限由Harness决定。宿主只支持提示词注入时，显式加载SKILL.md并提供实际目录供按需引用；只能聊天的宿主可讨论方案，执行结果保持未验证。

ZCode/Kimi Code CLI有目录快捷入口：

```sh
python S/install_agent.py --host zcode
python S/install_agent.py --host zcode --verify
python S/install_agent.py --host kimi
python S/install_agent.py --host kimi --verify
```

选择已安装的宿主执行。ZCode默认 `~/.zcode/skills`；当前Kimi CLI默认 `$KIMI_CODE_HOME/skills`，未设置时为 `~/.kimi-code/skills`。`--destination`优先于默认路径；不改全局配置、hooks或宿主权限。

安装器读取BUNDLE.json并校验全部已列文件哈希，拒绝额外文件、源/目标重叠及符号链接/junction。哈希只证明包内一致性，不认证来源；从可信仓库获取包。同字节重复安装不重写；已有不同文件时停止，先把旧Skill备份到搜索根目录外再人工替换。Python运行生成的__pycache__/pyc不属于安装内容，校验时忽略。--verify只读安装文件，不代表客户端发现或模型行为。

ZCode在Settings → Skills刷新并启用，使用 `$solo-dev-delivery`；Kimi新会话用 `/skill:solo-dev-delivery`。其他Agent用自己的注册/调用方式。以当前版本真实菜单为准；不要同时安装重复Skill副本。

## 可选无状态能力预检

任务确实依赖未知能力时，让宿主报告观察到的能力；不为了使用Skill先登记全部工具。示例文件 host.json：

```json
{"host_id":"your-actual-host","capabilities":{"fs.read":"supported","shell.exec":"unknown","browser.control":"unsupported"}}
```

```sh
python S/capability_probe.py --capabilities host.json --require fs.read --format text
```

只检查重复 `--require` 明确选择的能力。也可用已有 `--contract contract.json` 的 required_capabilities 或显式policy标志提出要求；不会新建契约。完整系统在 `portable-agent-kit/scripts` 下提供同一命令。

全部必要能力supported返回READY/退出0；显式unsupported返回BLOCKED/退出2；缺失、unknown、非法输入或没有声明要求返回NEEDS_CONTEXT/退出2。支持bool和 `{"supported":true/false/"unknown"}`，其他未知值保留unknown。它不执行探测、不证明任务完成、不授予权限、不默认要求checkpoint，也不创建session。需要持久恢复时仍使用完整系统原门禁，不能以这里READY绕过它。

## 在目标宿主做一次真实验证

在临时项目做文案小改并读回，再做一个有可复现失败的修复并跑相关回归。分别记录Skill是否在菜单出现、是否显式加载、自动选择是否发生、实际检查结果和环境版本；小改不应强制设计审批、worktree或子Agent。再确认包内challenge能在移动后的目录运行，能力缺失仍保持未知/阻断。

文件安装测试、客户端加载、自动分流和实际交付是不同结论。没有执行的宿主检查标为未测；这套说明不将本地PASS变成所有Agent已兼容。

目录快捷方式核对于2026-10-22：[ZCode Skills](https://zcode.z.ai/en/docs/skill)、[Kimi Code Skills](https://www.kimi.com/code/docs/en/kimi-code-cli/customization/skills.html)。具体Harness加载机制由该宿主当前文档/配置确认。
