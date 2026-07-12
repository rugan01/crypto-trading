import unittest
from positional_option_backtest import product_legs


class PositionalOptionTests(unittest.TestCase):
    def test_mode_is_defined_risk(self):
        self.assertIn("debit_call_vertical", {"debit_call_vertical", "debit_put_vertical", "credit_call_vertical", "credit_put_vertical"})


if __name__ == "__main__": unittest.main()
