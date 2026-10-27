# 三组 Skill 对照观察汇总

生成时间：2026-10-23T08:38:44.225096+00:00

状态：**complete**；注册 18，已结束 18，运行中 0，待开始 0，收据暂不可读 0。

本表只描述本次已观测差异，不作统计显著性判断或普遍排名。

自动检查与盲审分列；盲审缺失保留未知。token 总量仅为输入加输出，缓存输入和推理输出均为其中子项。金额和配额成本未观测。

## 组内业务检查

| 组 | 已结束 / 注册 | 首轮自动检查 | 协议后自动检查 | 预算截断 | 执行错误 |
|---|---:|---|---|---:|---:|
| baseline | 6 / 6 | 6 通过 / 0 未通过 / 0 未知 | 6 通过 / 0 未通过 / 0 未知 | 0 是 / 6 否 / 0 未知 | 0 是 / 6 否 / 0 未知 |
| solo | 6 / 6 | 6 通过 / 0 未通过 / 0 未知 | 6 通过 / 0 未通过 / 0 未知 | 0 是 / 6 否 / 0 未知 | 0 是 / 6 否 / 0 未知 |
| superpowers | 6 / 6 | 6 通过 / 0 未通过 / 0 未知 | 6 通过 / 0 未通过 / 0 未知 | 0 是 / 6 否 / 0 未知 | 0 是 / 6 否 / 0 未知 |

## 组内用量与耗时

以下各格为已观测样本的 sum / mean / median（已知数 / 注册数）。完整样本统计另存于 JSON；有未知时其 sum/mean/median 为 null。

主+子完整值要求子用量有观测；“观测到的主+子”仅覆盖已发现的原生线程，可能遗漏未观测子线程。未发现子线程不会被计为零。

| 组 | 主原生 total token | 子原生 total token | 主+子完整原生 token | 观测到的主+子原生 token | 模型执行秒数 | 合成用户回复数 |
|---|---|---|---|---|---|---|
| baseline | 2134011 / 355,668.5 / 281,066 (6/6) | 669264 / 111544 / 90,065.5 (6/6) | 2803275 / 467,212.5 / 362,026.5 (6/6) | 2803275 / 467,212.5 / 362,026.5 (6/6) | 1,262.06 / 210.34 / 205.97 (6/6) | 0 / 0 / 0 (6/6) |
| solo | 2836395 / 472,732.5 / 441,547.5 (6/6) | 910941 / 151,823.5 / 131,611.5 (6/6) | 3747336 / 624556 / 573,159 (6/6) | 3747336 / 624556 / 573,159 (6/6) | 1,282.09 / 213.68 / 180.98 (6/6) | 0 / 0 / 0 (6/6) |
| superpowers | 5382326 / 897,054.33 / 777,241 (6/6) | 1322852 / 220,475.33 / 189,541.5 (6/6) | 6705178 / 1,117,529.67 / 966,782.5 (6/6) | 6705178 / 1,117,529.67 / 966,782.5 (6/6) | 2,015.52 / 335.92 / 301.76 (6/6) | 0 / 0 / 0 (6/6) |

## 逐题逐次观测

| 题目 | 次数 | 组 | 收据状态 | 首轮自动 | 协议后自动 | 首轮盲审 | 协议后盲审 | 执行秒数 | 合成回复 | 预算截断 | 执行错误 |
|---|---:|---|---|---|---|---|---|---:|---:|---|---|
| 01-page-coverage | 1 | superpowers | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 252.45 | 0 | 否 | 否 |
| 01-page-coverage | 1 | solo | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 158.52 | 0 | 否 | 否 |
| 01-page-coverage | 1 | baseline | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 216.69 | 0 | 否 | 否 |
| 02-reading-packet | 2 | superpowers | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 432.28 | 0 | 否 | 否 |
| 02-reading-packet | 2 | solo | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 298.94 | 0 | 否 | 否 |
| 02-reading-packet | 2 | baseline | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 225.45 | 0 | 否 | 否 |
| 03-exhibit-release | 1 | superpowers | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 322.86 | 0 | 否 | 否 |
| 03-exhibit-release | 1 | solo | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 166.92 | 0 | 否 | 否 |
| 03-exhibit-release | 1 | baseline | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 195.25 | 0 | 否 | 否 |
| 01-page-coverage | 2 | superpowers | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 280.66 | 0 | 否 | 否 |
| 01-page-coverage | 2 | solo | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 195.05 | 0 | 否 | 否 |
| 01-page-coverage | 2 | baseline | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 177 | 0 | 否 | 否 |
| 03-exhibit-release | 2 | baseline | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 153.03 | 0 | 否 | 否 |
| 03-exhibit-release | 2 | superpowers | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 235.33 | 0 | 否 | 否 |
| 03-exhibit-release | 2 | solo | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 154.14 | 0 | 否 | 否 |
| 02-reading-packet | 1 | baseline | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 294.64 | 0 | 否 | 否 |
| 02-reading-packet | 1 | solo | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 308.53 | 0 | 否 | 否 |
| 02-reading-packet | 1 | superpowers | finished / business_complete | 通过 | 通过 | 通过 | 通过 | 491.94 | 0 | 否 | 否 |

## 读取与解释边界

- 读取 registration.json、runs/*/receipt.json 和可选 blind-review-results.json；不读取认证、配置、会话或模型事件原文。
- 原生主线程与子线程逐次累计值由 metrics.py 提供；本工具不重新叠加轮次累计值。exec 回退用量只保留在 JSON 的 reported_usage 中。
- 模型执行秒数来自 harness 的模型进程墙钟时间，不含独立检查耗时。
- 预算截断和执行错误单独列出；自动检查通过不等同于外部生产发布成功。
- 原始盲审字段保留于 JSON；本工具不从备注猜测业务结论。
