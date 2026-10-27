import unittest

from mt5_forex.risk import (
    RiskQuote, VolumeSpec, calculate_volume, volume_floor,
    within_daily_loss_limit,
)


class RiskTests(unittest.TestCase):
    def setUp(self):
        self.spec = VolumeSpec(minimum=0.01, maximum=100.0, step=0.01)

    def test_rounds_volume_down(self):
        self.assertEqual(volume_floor(0.019, self.spec), 0.01)

    def test_below_minimum_is_refused(self):
        self.assertEqual(volume_floor(0.009, self.spec), 0.0)

    def test_risk_budget_not_exceeded(self):
        volume, loss = calculate_volume(RiskQuote(
            account_equity=10000, risk_percent=0.5,
            loss_per_lot=1000, volume_spec=self.spec,
        ))
        self.assertEqual(volume, 0.05)
        self.assertLessEqual(loss, 50.0)

    def test_minimum_lot_not_forced_up(self):
        volume, loss = calculate_volume(RiskQuote(
            account_equity=1000, risk_percent=0.5,
            loss_per_lot=10000, volume_spec=self.spec,
        ))
        self.assertEqual((volume, loss), (0.0, 0.0))

    def test_risk_cap_refuses_more_than_half_percent(self):
        with self.assertRaises(ValueError):
            calculate_volume(RiskQuote(
                account_equity=10000, risk_percent=0.51,
                loss_per_lot=100, volume_spec=self.spec,
            ))

    def test_daily_loss_limit(self):
        self.assertTrue(within_daily_loss_limit(10000, 9900, 2.0))
        self.assertFalse(within_daily_loss_limit(10000, 9799, 2.0))
        self.assertFalse(within_daily_loss_limit(10000, 9000, 0.0))


if __name__ == "__main__":
    unittest.main()
