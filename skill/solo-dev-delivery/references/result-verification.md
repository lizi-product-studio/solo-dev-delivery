# 场景选择、反例重放与关系核验

只对本次风险使用必要部分。脚本目录 S：独立Skill是SKILL_DIR/scripts；完整本机系统是ROOT/portable-agent-kit/scripts。Python标准库，不调用模型或创建定时器。原有队列的正式门禁继续使用，不另造一套发布系统。

## 主动选择最有价值的检查

复杂/高影响/反复返工任务在实现前读原始目标和有效政策，用brief把它们的段落与哈希保留下来。只选择业务依据文档，不把整仓库或所有CSV行变成登记表；原始数据依赖随后由freeze绑定。

```sh
python3 S/challenge.py brief --workspace /work/candidate \
  --input TASK.md --input POLICY.md --brief /work/review/basis.json
```

从这些原文独立推导本次关键可观察结果：成功会留下什么事实，什么边界或冲突会推翻它，结果未知/不可行时应怎样交付。没有具体风险的视角不增加；同一个段落可能有多个条件，不能只覆盖其中一句。保留一份轻量plan.json，将原文ID映射到结果与真正执行的unittest方法；非验收上下文给出具体理由，不将关键义务塞进context。示意字段如下，IDs和结果应来自本次brief而非照抄：

```json
{
  "test_file": "/work/review/checks.py",
  "outcomes": [
    {"id":"O1","sources":["POLICY.md:3"],"expected":"执行后重开状态仍满足原政策的关键约束","priority":3},
    {"id":"O2","sources":["POLICY.md:3"],"expected":"相反顺序或失败条件下约束仍成立，且有约定的恢复结果","priority":3}
  ],
  "context": [{"source":"TASK.md:3","reason":"仅定位原始资料；依赖已纳入候选快照，不另作业务断言"}],
  "checks": [
    {"id":"C1","test":"Business.test_roundtrip","covers":["O1"],"cost":1},
    {"id":"C2","test":"Business.test_conflicting_update","covers":["O2"],"cost":2}
  ]
}
```

priority为1..3，只改变顺序，所有已列结果和默认required的不同场景都要覆盖。一个方法可核多个结果，但不同方法默认不等价；只有明确非必要的补充检查才设required=false并给why_optional，仍由主Agent核实排除理由。cost是本地检查的相对工作量，不是token/额度，也不限制Agent运行时间。用下面既有freeze绑定当前候选后，一次调用选择并运行一小组检查：

```sh
python3 S/challenge.py assess --brief /work/review/basis.json \
  --plan /work/review/plan.json --packet /work/review/candidate.json \
  --budget 3 --run --report /work/review/coverage-1.json
```

assess按未覆盖结果的重要性/相对成本作贪心选择，一个方法可覆盖多个结果，只执行一次；不同必要场景不因共享结果标签而互相替代。实际回执包含Class.test_method与状态；声明covers但没执行、跳过/空测试、错误候选或失效回执均不能覆盖。未核对的原文段落、无检查的结果和预算不足列为缺口。真实失败优先修复，报告共享原文依据的相关结果供扩查，不机械全量重跑。不会自动新增Agent、换模型或终止工作。

plan和report放在本次隔离控制目录。assess自动读回这两个目录直属的challenge回执，复用有效结果；同候选/脚本的失败不会因漏写--evidence或从plan删掉失败方法而被另一个PASS抹掉。移到其他位置的回执用--evidence显式带入；工具不扫描全仓库或其他聊天。自动回执文件名为report去掉后缀再加-replay.json。每次用新report路径，保留失败。示例：

```sh
python3 S/challenge.py assess --brief /work/review/basis.json \
  --plan /work/review/plan.json --packet /work/review/candidate.json \
  --evidence /work/review/coverage-1-replay.json \
  --budget 0 --report /work/review/coverage-2.json
```

DECLARED_SCOPE_COVERED只证明已声明范围有当前可归属的执行证据，下一步仍是主Agent对照原文检查语义充分性：有没有将必需结果排成context、是否遗漏同段另一条件、断言是否真能推翻错误、人用说明是否一致、未测环境是否影响交付。原文段落登记不等于语义解析，逐方法PASS不证明断言有效。平台原生验收覆盖充分时复用；非Python/真实界面按宿主工具观察，不写假测试绕过缺证。必要独立审查仍由原生能力按风险提供，不能用本地元数据认证身份。

## 从原目标产生能推翻实现的场景

先用原始需求和有效政策写清关键结果：谁能做什么、何种事实会改变结果、发生冲突后哪些操作必须保持可恢复。实现完成后，由独立审查上下文把预期变成实际操作和持久读回。不要让开发自测的覆盖范围限定审核，也不要为测试编造经营政策。

涉及多个事实来源时，按真实可达情况交换更新顺序、重复操作、插入中断/恢复或矛盾证据，观察关键约束是否仍成立。一个去重命中不能替代核对后来信息是否与原事实冲突。发现反例后先界定同根因影响，再修复相关路径；不盲目全量扩测。

本地Python服务/计算可以用challenge.py执行审查者的unittest。其他技术栈、浏览器和真实外部系统用宿主实际工具，保留候选、动作、观察和证据；本地重放不代替它们。

```sh
python3 S/challenge.py freeze --workspace /work/candidate \
  --input TASK.md --input POLICY.md --output service.py --output notes.md \
  --packet /work/review/candidate.json
python3 S/challenge.py run --packet /work/review/candidate.json \
  --test /work/review/review_checks.py --receipt /work/review/attempt-1.json
python3 S/challenge.py status --receipt /work/review/attempt-1.json
```

上述文件名仅是示意。声明测试需要的全部输入和候选依赖，最多200个文件，每个最多5MiB；不复制整仓库、凭据或无关数据。控制目录和审查脚本放在候选工作区外。freeze不覆盖现有packet，修复后冻结新候选；run不覆盖已有回执，失败证据保留。

审查脚本用unittest.TestCase，调用候选真实方法或实际计算，验证动作后的业务状态与持久读回；导入的业务模块从临时候选副本解析。不要用固定返回值冒充实际业务操作。runner要求真实执行至少一项，无测试、跳过或expectedFailure返回UNKNOWN；失败返回FAIL。候选、原始输入、测试脚本或packet变化后，旧结论STALE。回执保存真实测试数量、退出码及输出尾部，超出64KiB明确截断。每项结论都仅覆盖这些测试。

run默认60秒限制的是一次可抛弃的本地测试进程，可按具体测试调整1–300秒；超时保留UNKNOWN并只清理该测试进程组，不停止Agent、不夺项目写锁。该脚本运行受信审查Python代码，临时副本不构成安全沙箱，也不阻止测试自行联网；外部行为仍受任务授权约束。CHALLENGE_PASS不认证独立身份、完整业务覆盖、真实发布或生产可用；宿主核对审查者身份和原始工具观察，不能导入模型自写PASS冒充审核。

## 一次性关系检查，不必新建session

在工作区外准备relations.json，scope声明要核对的文件，checks放所需probe。运行后同时核对候选依赖版本，不能拼接不同版本各自通过的结果：

```sh
python3 S/challenge.py check --workspace /work/candidate \
  --spec /work/review/relations.json --receipt /work/review/relations-1.json
python3 S/challenge.py status --receipt /work/review/relations-1.json
```

例如完整总额与已知小计不同，选择匹配业务含义的字段。下例不是任何行业的默认口径；源行、单位和覆盖范围必须先按原始依据核对。

```json
{
  "scope": ["result.json"],
  "checks": [{
    "id": "amount-completeness",
    "probe": {
      "type": "json.aggregate", "path": "result.json", "pointer": ["total"],
      "rows": ["items"], "value": ["amount"],
      "known_sum": ["known_subtotal"], "unknown_count": ["unknown_count"]
    }
  }]
}
```

json.aggregate对选中行精确相加，一个值null则完整total必须null；known_sum仅加已知值。known_count、unknown_count可选。缺键失败，空集默认失败，只有业务定义明确时使用empty="zero"。数字字符串默认拒绝，需要时显式numeric_strings=true；不接受bool或非有限数。可用source选择另一JSON，来源与目标都须列入scope并绑定哈希。它不验证源行是否选对、缺失是否真的未知或单位是否合理；这些由原数据核对与业务审查负责。

## 一份权威数据生成交接表格

先确定真正用于执行的数据，生成表格给人阅读；不要分别写两份人员、金额、状态再碰运气对齐。render只创建新的Markdown文件，便于嵌入现有说明；不会覆盖已有说明：

```sh
python3 S/challenge.py render --workspace /work/candidate --source result.json \
  --rows '["assignments"]' --columns '[{"label":"事项","pointer":["item"]},{"label":"负责人","pointer":["owner"]}]' \
  --block assignments --output assignment-table.md
```

将生成的具名区块放入需要交付的说明后，以json.table核对：

```json
{
  "scope": ["result.json", "notes.md"],
  "checks": [{
    "id": "handoff-consistency",
    "probe": {
      "type": "json.table", "path": "notes.md", "source": "result.json", "rows": ["assignments"],
      "columns": [{"label":"事项","pointer":["item"]},{"label":"负责人","pointer":["owner"]}],
      "block": "assignments"
    }
  }]
}
```

区块边界为独占一行的<!-- generated:assignments:start -->和<!-- generated:assignments:end -->，正文使用renderer结果。只验证该区块：块外标题、布局和其他文字允许自由编辑，仍需检查是否与执行数据矛盾。None显示“未知”，不会转0。换人、改顺序或改值失败；此类失败应回到权威数据核实，不为迎合检查器修改真实业务事实。

## 放行与资源

有真实阻断、必要检查未完成、候选失效或结果未知时，不把局部PASS宣布成完成。先保留现场并定位理解、事实、工具或环境，再做一次有依据的修复与同根因复验；反复相同失败时拆小或升级，不无限重试。一次通过且覆盖仍有效就复用，避免重复写报告、角色扮演或不相关检查。用首次质量、漏检、返工与宿主实际用量评价改进，不能只看工具自己的测试。
