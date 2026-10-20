# Solo Dev Delivery

面向一人软件业务的 Agent 交付协作 Skill。按任务影响安排资源，主动审核，并以实际用户操作和业务结果验收。

此仓库是当前个人系统的**私有源文件快照**，包含 Skill 及其运行依赖，不含正在进行的项目和业务数据。当前默认使用 Codex 原生子 Agent；跨平台部分是协议、示例和接入说明，不代表已在所有平台实机验证。

## 内容

- [Skill 入口](skill/solo-dev-delivery/SKILL.md)：需求接手、执行、主动审核与交付责任。
- `knowledge/preferences.json`、`knowledge/methods.json`：个人偏好和候选方法，保留来源和适用范围。
- `scripts/`、`templates/`、`vendor/`：本地队列、审核证据门禁、业务接手和兼容调度。
- [主动审核](主动审核执行说明.md)、[资源调度](资源调度执行说明.md)、[业务闭环](业务闭环执行说明.md)：按需使用的流程。
- [portable-agent-kit](portable-agent-kit/README.md)：AAP-0.1 状态机、参考适配器和 Cursor、Kimi Code、DeepSeek Harness 等接入说明。
- `tests/`：现有回归测试。

## 恢复与使用

Python 3.9 及以上；开项 JSON 导入另需 Node.js。该快照保留现有代码和个人配置，**Skill 中的 ROOT 仍指向原机器的固定目录**：`/Users/lizi/Documents/Codex/Systems/solo-delivery`。

在全新环境恢复时，将完整仓库放到该目录，再将 `skill/solo-dev-delivery` 链接到 Codex 的用户技能目录。其他用户名或目录需要先调整 Skill 和说明中的 ROOT。不要只复制 SKILL.md，也不要把克隆操作覆盖到当前正在运行的系统目录。仓库不包含全局 AGENTS.md；新机器默认自动加载的入口需要单独配置。

```sh
python3 scripts/delivery.py --help
python3 -m unittest discover -s tests -v
python3 -m unittest discover -s portable-agent-kit/tests -v
```

`SOURCE_SNAPSHOT.json` 记录本次直接复制文件的 SHA-256，可用于核对源文件快照。

## 备份范围与边界

本次没有上传 `runtime/`、数据库、运行日志、原始聊天、项目源码、客户业务记录、`knowledge/feedback/` 或历史验收证据。偏好和方法中的历史来源引用仅用于追溯，不表示原始证据也在仓库里。Skill 引用的本地候选实验记录没有随包导出。

定时任务默认不启用。生产发布、真实交易、新增费用和外部承诺遵从项目授权；队列中的 DELIVERED 只代表约定的本地交付。状态机和测试不能保证零缺陷或自动经营公司。

## 许可

当前为个人私有备份，未为仓库整体授予开源许可。`vendor/` 中的组件保留各自 MIT 许可证及 `vendor/SOURCES.json` 来源信息。
