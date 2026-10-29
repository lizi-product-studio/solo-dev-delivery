# 合成订单统计任务

读取 `orders.json`，生成 UTF-8 `summary.json`。不能修改输入。使用十进制精确口径，所有金额累计后最后一次以 ROUND_HALF_UP 舍入为两位小数字符串。

规则：status=cancelled 的整条排除；非取消 sale 的 quantity 计入 sale_units，非取消 return 的 quantity 计入 return_units；net_units=sale_units-return_units。amount=quantity*unit_price，return 金额为扣减。unit_price 为 null 表示金额未知，不能变成 0。未知行仍计数量和 included_count，但不计已知金额小计；只要有未知行，net_amount 必须为 JSON null。

输出字段：included_count、cancelled_count、unknown_amount_rows、sale_units、return_units、net_units（均整数）；gross_known_amount（已知销售金额）、return_known_amount（已知退货金额）、known_amount_subtotal（已知销售减退货）为两位小数字符串；net_amount 为完整净金额，存在未知时为 null。这是一份合成数据口径核对，不代表生产财务验收。
