"""Trusted deterministic data oracle; no sibling results or model evaluation."""
import json
from pathlib import Path
import unittest


class OrderSummary(unittest.TestCase):
    def setUp(self):
        self.value=json.loads((Path.cwd()/'summary.json').read_text(encoding='utf-8-sig'))

    def test_counts_exclude_cancelled(self):
        self.assertEqual(6,self.value['included_count'])
        self.assertEqual(1,self.value['cancelled_count'])

    def test_unknown_is_not_zero(self):
        self.assertEqual(2,self.value['unknown_amount_rows'])
        self.assertIsNone(self.value['net_amount'])

    def test_sales_returns_and_net_quantity(self):
        self.assertEqual(7,self.value['sale_units'])
        self.assertEqual(3,self.value['return_units'])
        self.assertEqual(4,self.value['net_units'])

    def test_gross_decimal_sum_rounds_at_end(self):
        self.assertEqual('4.81',self.value['gross_known_amount'])

    def test_return_amount_is_positive_subtrahend(self):
        self.assertEqual('1.25',self.value['return_known_amount'])

    def test_known_subtotal_does_not_claim_complete_net(self):
        self.assertEqual('3.56',self.value['known_amount_subtotal'])
        self.assertIsNone(self.value['net_amount'])

    def test_counter_types_are_integers_not_booleans(self):
        for key in ['included_count','cancelled_count','unknown_amount_rows','sale_units','return_units','net_units']:
            with self.subTest(key=key):self.assertIs(type(self.value[key]),int)


if __name__=='__main__':unittest.main()
