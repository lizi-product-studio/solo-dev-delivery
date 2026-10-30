"""Trusted behavioral oracle; reads only the candidate workspace."""
from decimal import Decimal
import importlib.util
from pathlib import Path
import unittest


class PricingBehavior(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        spec = importlib.util.spec_from_file_location('candidate_pricing', Path.cwd()/'pricing.py')
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        cls.quote = staticmethod(module.quote_total)

    def test_regular_decimal_result_and_scale(self):
        result=self.quote('12.50',3,'10')
        self.assertIsInstance(result,Decimal)
        self.assertEqual(Decimal('33.75'),result)
        self.assertEqual(-2,result.as_tuple().exponent)

    def test_none_discount_is_zero(self):
        self.assertEqual(Decimal('0.30'),self.quote('0.10',3))

    def test_zero_quantity_and_zero_unit_price(self):
        self.assertEqual(Decimal('0.00'),self.quote('8.25',0))
        self.assertEqual(Decimal('0.00'),self.quote(0,100,0))

    def test_discount_endpoints(self):
        self.assertEqual(Decimal('5.00'),self.quote(5,1,0))
        self.assertEqual(Decimal('0.00'),self.quote(5,1,100))

    def test_round_once_after_quantity_and_discount(self):
        self.assertEqual(Decimal('0.02'),self.quote('0.005',3,0))
        self.assertEqual(Decimal('1.01'),self.quote('1.005',1,0))

    def test_decimal_inputs(self):
        self.assertEqual(Decimal('8.55'),self.quote(Decimal('3'),3,Decimal('5')))

    def test_invalid_types(self):
        for price,quantity,discount in [(True,1,0),(1.5,1,0),(None,1,0),([],1,0),('1',True,0),('1','2',0),('1',2,False),('1',2,1.5)]:
            with self.subTest(values=(price,quantity,discount)),self.assertRaises(TypeError):
                self.quote(price,quantity,discount)

    def test_negative_and_discount_range(self):
        for price,quantity,discount in [('-1',1,0),('1',-1,0),('1',1,-1),('1',1,101),('1',0,101)]:
            with self.subTest(values=(price,quantity,discount)),self.assertRaises(ValueError):
                self.quote(price,quantity,discount)

    def test_nonfinite_and_bad_numeric_text(self):
        for price,discount in [('NaN',0),('Infinity',0),('bad',0),('1','NaN'),('1','Infinity'),('1','invalid')]:
            with self.subTest(values=(price,discount)),self.assertRaises(ValueError):
                self.quote(price,1,discount)


if __name__=='__main__':unittest.main()
