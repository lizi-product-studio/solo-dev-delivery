# 合成软件修复任务

修复本目录 `pricing.py` 中的 `quote_total(unit_price, quantity, discount=None)`，只修改该文件，使用 Python 标准库，不新增依赖。

函数返回两位小数的 `Decimal`。金额先按十进制计算 `unit_price * quantity * (1 - discount / 100)`，最后一次按 `ROUND_HALF_UP` 舍入至分。单价接受非负有限的 int、Decimal 或十进制字符串；不接受 float、bool、None 或容器。数量接受非负 int，但 bool 不是数量。折扣 None 表示 0；其他折扣接受有限 int、Decimal 或十进制字符串，含 0 和 100，不接受 float 或 bool。

不支持的类型抛出 TypeError；非法数字文本、非有限数、负单价/数量、超出 0..100 的折扣抛出 ValueError。数量为 0 时也应验证其他参数。不要固定返回示例答案。任务为合成的本地行为修复，不涉及生产报价。
