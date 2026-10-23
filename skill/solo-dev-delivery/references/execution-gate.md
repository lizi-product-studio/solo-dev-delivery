# 从源政策生成验收并阻止过早交付

多步骤、有业务条件的任务使用；简单问答与纯文案样式不登记。已有队列与gate/1任务保持原行为。新任务用gate/2：原文按行为语句分开核对，实际检查绑定候选副本；原生链路验收充分时直接复用，不为使用模型再造产品状态机。

完整系统工具为`portable-agent-kit/scripts/gate.py`，独立包为`scripts/gate.py`。控制目录在候选工作区外：

```sh
python3 -X utf8 "$GATE" start --workspace "$WORKSPACE" --input REQUEST.md --output app.py --task "$CONTROL/task.json"
# 从原始需求写 adapters.py 与 plan.json，尚不修改实现。
python3 -X utf8 "$GATE" seal --task "$CONTROL/task.json" --plan "$CONTROL/plan.json"
# 按返回的区分场景实现/修复，实际验证；非零退出即不能交付。
python3 -X utf8 "$GATE" check --task "$CONTROL/task.json" --run
```

Windows可将`python3`换为`python`；`-X utf8`明确本次Python进程的文本编码，子进程也应按其实际编码配置，不修改用户全局环境。

## 让既有测试与数据进入候选副本

`--input`声明提供业务要求的原文，`--output`声明本轮产物。既有测试、CSV样本及其他运行所需但本轮不修改的文件，用可重复的`--support-file`明确列出项目内相对路径：

```sh
python3 -X utf8 "$GATE" start --workspace "$WORKSPACE" --input REQUEST.md --output src/report.py --support-file tests/test_report.py --support-file data/orders.csv --task "$CONTROL/task.json"
```

这些只读依赖进入冻结候选副本，供原有测试与真实CLI直接使用；不解析为待覆盖的业务语句，也不能代替`--input`中的验收义务。不自动复制整个目录或猜测依赖。开始时绑定文件哈希，依赖变动或缺失会阻止放行，旧PASS不能覆盖新内容；确需修改的依赖应作为产物声明并重新建立验收范围。旧任务不需迁移。

## 先复用链路检查，不重复填需求

普通验收用一份Python unittest和如下plan；sources直接引用start返回的原文语句ID，工具自动生成覆盖关系，无需另填outcomes/expected/covers：

```json
{
  "test_file": "/absolute/control/checks.py",
  "checks": [{"id":"chain", "test":"Business.test_roundtrip", "sources":["REQUEST.md:3.1", "REQUEST.md:3.2"]}],
  "context": []
}
```

一个检查可对应多个真实观察；不能仅因写了多个sources就认为断言存在。非行为内容放context并写具体理由。旧outcomes/covers格式保持兼容。

## 决策模型替代手工枚举

先复用已有真实业务链、持久读回和恢复检查。只为仍缺少区分观察的关键允许条件、数值边界或优先级建立模型；不要按原文每段各建一张表。模型只返回允许/拒绝、选中对象或结果类别，复杂状态与已有链路断言不再重写。`test_file`是受信Python适配器，可含已有unittest链路检查，也可仅定义观察函数。可选`decisions`只为尚缺的区分生成检查，不需再写对应outcomes/checks；没有此类缺口时用既有outcomes/checks直接执行。示例仅展示格式，不是通用业务政策：

```json
{
  "test_file": "/absolute/control/adapters.py",
  "decisions": [{
    "id": "admission", "sources": ["REQUEST.md:3"], "observe": "admit",
    "facts": {"enabled": [true, false], "score": {"step": 1}},
    "rules": [{"when": {"all": [
      {"fact": "enabled", "op": "==", "value": true},
      {"fact": "score", "op": ">=", "value": 5}
    ]}, "then": true}],
    "default": false
  }],
  "outcomes": [], "checks": [], "context": []
}
```

适配器`admit(facts)`建立真实前置状态，经公开入口操作并返回实际允许/拒绝。程序路径用`Path.cwd()/"app.py"`或环境变量`SOLO_CANDIDATE_ROOT`，不要写死工作区原文件；门禁在副本执行，并拒绝适配器读/调用原工作区。持久读回和恢复用既有链路检查复用，不把每个状态字段重复写成模型分支。预期只放在政策中，工具仅向适配器传facts；不能写`return enabled and score>=5`冒充产品调用。状态流可将原文中的前置状态或操作顺序作枚举事实，由适配器走真实到达路径；不能直接伪造不可到达状态。故障/重试/人用交接等已有链路用普通unittest保留，在outcomes/checks中映射来源，和生成检查一次执行。

事实枚举写原文允许的有代表性类别；数值等量用原始量或差值而非matches布尔；连续数值用最小业务整数单位的step（金额用分等），工具自动从比较常数选择阈值减一步、等于阈值、加一步。条件支持all/any/not；原子为fact/op/value，op为==、!=、>、>=、<、<=、in、not in。rules按顺序优先，then/default是允许、选中对象或结果类别的JSON标量；完整状态与读回放在已有链路断言。不从实现取预期、不发明未定政策。

工具在封存时计算有限可达输入，自动选取每条结果分支、每个能独立改变结果的事实/相邻阈值对，以及结果不同的优先规则重叠。保持其他事实相同，避免一个拒绝掩盖另一个；选择用有限集合覆盖减少实际调用，不穷举全部组合。不设最低测试数。不影响结果的事实、永不获胜的规则需回到原文核对，不静默通过。单决策最多4096候选组合，超出要求按独立动作拆开，不因开销上限删义务。

seal返回候选组合数、区分义务及选中检查数；scope-N/selection.json保存实际选例。主Agent对照原文确认事实/规则和关键链，不另写自评评分表。原文按独立语句切分，命令内的分号保留；每个单元须关联决策、链路结果或具体非行为说明，不能一个正常场景笼统覆盖同段的排序或恢复要求。来源段落齐全不能证明段内所有条件均已建模。

## 继续、停止与恢复

check复用已有执行器，只运行缺失且仍需要的检查；所有生成场景都是required，不能用同一outcome的一个PASS代替其他独立场景。模型、适配器、生成测试、原文和候选均绑定哈希。失败返回共享来源的相关结果；修改验收须seal --reason说明原文依据，旧验收和失败保留。真实UI/外部服务没有观察时保留缺口，不把本地替身PASS说成完成。

`install-hook`合并用户级Stop定义，保留其他钩子。按Codex官方要求在`/hooks`审核信任当前定义；安装不是启用。start默认用CODEX_THREAD_ID登记当前会话，子Agent隔离测试可传`--session ''`。无登记不拦聊天；有缺口则返回最小补查项，不因stop_hook_active豁免、不按时限杀Agent。通过且证据新鲜才结束登记。其他宿主需自己的等价结束回调。

确实需要用户信息/外部环境时，保留实际失败证据，用`blocked --task ... --reason ... --evidence /absolute/error.txt`如实交接BLOCKED，不标完成。恢复后重跑check。

这是让主Agent的业务推理接受自动区分检查的执行工具。它不能从自然语言保证无限语义完整，也不能防止拥有本机写权限的Agent蓄意伪造适配器；断言仍须对应真实业务入口。验证结论限当前模型、实际环境与已发生观察。
