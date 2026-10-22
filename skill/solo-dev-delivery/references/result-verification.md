# 反例重放与关系核验

只对本次风险使用必要部分。脚本目录 S：独立Skill是SKILL_DIR/scripts；完整本机系统是ROOT/portable-agent-kit/scripts。Python标准库，不调用模型或创建定时器。原有队列的正式门禁继续使用，不另造一套发布系统。

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
