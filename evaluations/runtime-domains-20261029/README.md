# Runtime 三领域合成验收资产

此目录只提供三类小型任务与受信判分器，不包含真实模型通过记录。`fixtures/manifest.json` 是 runner 入口；每个 task 的 workspace 是可复制输入，inputs/outputs 是工作区相对路径，review 和 contract 是 fixtures 相对路径。

- software：修复 Decimal 报价计算、类型、空值和边界行为；9 个 unittest 方法。
- data：区分销售、退货、取消与未知金额，用精确十进制口径汇总；7 个 unittest 方法。
- procurement：依据合成政策生成待人工审核的询价草稿；核对对象、问题、未知事实、未发送状态及两份文稿一致性；8 个 unittest 方法。

运行 `python evaluations/runtime-domains-20261029/verify_fixture_oracles.py` 做判分器自检。脚本在临时副本中生成正确候选和明显错误/变异候选；正确候选不写入交付 workspace。15 个自检用例的 PASS 表示“正例被接受、反例被拒绝”符合预期，不表示 Agent 已完成 15 个业务任务。

模型只应收到复制后的 workspace 和任务要求，不应收到 review.py、自检参考实现、其他任务结果或外部判分数据。判分器从 Path.cwd() 读取候选，不能读取兄弟目录的评测结果；它不修改业务文件。合同断言只核对结构及基础属性，领域行为必须由 review.py 附加验证。

这些任务仅覆盖声明范围；采购判分器检查确定性业务约束和问询标识，不评价所有自由文本语义或真实供应商政策。三次小任务成功不证明跨领域迁移、相对基线优势、生产能力或 AGI/ASI。
