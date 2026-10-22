# 可执行交付门禁

用于已有明确原始需求、需要防止漏检与过早交付的多步骤改动。简单问答不登记，局部无业务变化的文案/样式修改按定向验证。已有持久队列继续原门禁，勿另建一套队列。

主Agent在修改产物前，从原始输入选择完整业务链和会改变决策的条件，写少量验收。使用当前项目测试；需要本工具时，Python unittest 可直接调用已有程序、接口或命令，不为门禁改写产品。真实UI、外部系统或生产环境没有实际观察时，不能用本地测试替代；缺环境记录为阻塞。

工具在完整系统的 `portable-agent-kit/scripts/gate.py`，独立包为 `scripts/gate.py`。以下 `GATE` 表示该路径，`CONTROL` 是工作区外的当前验收目录：

```sh
python3 "$GATE" start --workspace "$WORKSPACE" --input REQUEST.md --output app.py --task "$CONTROL/task.json"
# 主Agent阅读返回的全部原文单元，写 checks.py 和 plan.json，再封存；尚未做实现。
python3 "$GATE" seal --task "$CONTROL/task.json" --plan "$CONTROL/plan.json"
# 实现后执行；返回非零即不能交付。按 next_action 只处理剩余缺口。
python3 "$GATE" check --task "$CONTROL/task.json" --run
```

`start` 自动切分原文段落及列表条目；它不能替代语义理解，一个条目内的多个条件仍须主Agent识别。`plan.json`沿用已有brief/assess格式，不另建业务登记系统：

```json
{
  "test_file": "/absolute/control/checks.py",
  "outcomes": [{"id":"saved", "sources":["REQUEST.md:3"], "expected":"真实操作后新进程能读回业务结果"}],
  "checks": [{"id":"journey", "test":"Business.test_journey", "covers":["saved"]}],
  "context": []
}
```

每个原文单元都必须关联结果或具体非行为说明，不能把业务条件塞进context逃避检查。一个测试可覆盖多个结果；独立场景不能因covers相同就互相替代。拒绝、恢复、临界两侧及未知结果重试，按实际源条件选择，不设最低场景数。封存前尚未改变实现；后改验收须 `seal --reason "原文依据与修正原因"`，保留旧验收和失败。源需求变化重新开范围，不能静默替换原文。

`check` 执行缺失场景、复用仍有效回执，失败列出相关结果。修改候选后旧证据失效；相同候选的失败不会被后来导入的PASS覆盖。停止判断不调用模型，不另起Agent，也不按时间杀工作者。

## Codex自动结束拦截

`python3 "$GATE" install-hook`合并用户级Stop钩子，保留其他钩子。**安装不是启用**：按Codex官方要求，在`/hooks`审核并信任当前定义，修改定义后需重新信任。未完成宿主信任/加载，只能声称命令门禁生效，不能声称已强制拦截聊天结束。

`start`默认用`CODEX_THREAD_ID`绑定当前会话。其他宿主仅有CLI门禁，需宿主支持同等结束回调才自动拦截。无登记任务时不拦普通聊天；已登记任务缺证时Stop返回最小补查项并继续，`stop_hook_active`不会豁免。通过且证据仍新鲜才关闭登记，后续普通聊天不反复重查旧项目。不要给子Agent设置Stop强制续跑或固定结束时限。

确实需要用户信息或外部环境时，先保留失败/缺失证据，再用 `blocked --task ... --reason ... --evidence /absolute/error.txt`交接为BLOCKED。此状态允许如实说明阻塞，绝不等于交付完成；恢复后重新执行check。

边界：此机制强制的是当前已声明业务范围的实际验收，不能自动证明原文理解无遗漏，也不能防止拥有本机写权限的Agent蓄意绕过。主Agent仍负责选择正确场景和判断断言是否充分。不要把本地通过说成生产就绪。
