# Quality maturity candidate

This branch upgrades the five weakest areas identified in the 1.0 assessment without adding a default workflow:

- `quality-contracts.md` defines bounded evidence contracts for host adapters, UI quality, system design, autonomous cycles, and A/B cost/quality comparisons.
- `portable-agent-kit/scripts/quality_contract.py` validates those contracts and refuses to turn missing or unknown evidence into PASS.
- The same checker supports pairwise comparisons and returns `INCOMPLETE` when the valid sample is too small for a superiority claim.

The contracts are deliberately advisory. They do not create a scheduler, grant permissions, run a browser, benchmark production capacity, or decide product policy. They make those limits explicit and give an Agent a reproducible next check.

Run the focused tests:

```sh
python -X utf8 -m unittest portable-agent-kit/tests/test_quality_contract.py -v
```

## 这轮的实际提升

| 薄弱项 | 机制层 | 证据层 | 已落地的变化 | 仍需真实证据 |
| --- | ---: | ---: | --- | --- |
| 跨平台安装与适配 | 8.2 | 7.8 | Windows shebang CLI 适配、宿主五阶段与三态能力合同 | ZCode、Kimi、DeepSeek Harness 的实机回放 |
| 前端设计质量 | 8.0 | 7.0 | 双视口、真实内容、状态、无障碍和解释文字比例合同 | 真实产品截图与交互复核样本 |
| 后端/数据库/高并发 | 8.1 | 6.8 | 容量模型、RPS×延迟并发推导、幂等、背压、SLO、压测和恢复演练门槛 | 目标环境压测、故障演练和数据库实测 |
| 自主经营/长期运行 | 8.0 | 6.5 | 信号、授权、预算、单步下一动作、停止和未知恢复合同 | 宿主唤醒/持久状态下的连续多轮项目试点 |
| 成本与质量收益 | 8.0 | 5.5 | 只对有效配对计算差值，容量/连接失败自动 censored，样本不足返回 INCOMPLETE | 至少 3 对以上有效 A/B 配对和稳定模型容量 |

“机制层”表示 Skill 已经能要求和机械核对这些内容；“证据层”表示真实环境已经证明结果。后一列没有完成前，不把设计合同包装成生产保证。
