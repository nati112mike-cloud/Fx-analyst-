"""Tests for the position size calculator: direct, inverse, and cross pair
pip-value math, rounding to lot steps, and the auto-resolved conversion
rate path. Run with: python -m unittest discover -s tests
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fx_engine.position_sizing import PositionSizingError, calculate_position_size, pip_value_per_lot


class TestPipValue(unittest.TestCase):
    def test_direct_pair_eurusd_usd_account(self):
        # EURUSD: quote=USD=account -> pip value fixed at pip*contract_size = 0.0001*100000 = $10/lot
        pv = pip_value_per_lot("EURUSD", current_price=1.0850, account_currency="USD")
        self.assertAlmostEqual(pv, 10.0, places=2)

    def test_inverse_pair_usdjpy_usd_account(self):
        # USDJPY: base=USD=account -> pip value = (0.01*100000)/price = 1000/149.50
        pv = pip_value_per_lot("USDJPY", current_price=149.50, account_currency="USD")
        self.assertAlmostEqual(pv, 1000 / 149.50, places=4)

    def test_cross_pair_requires_conversion_rate(self):
        with self.assertRaises(PositionSizingError) as ctx:
            pip_value_per_lot("EURJPY", current_price=162.20, account_currency="USD")
        self.assertIn("cross pair", str(ctx.exception))

    def test_cross_pair_with_explicit_conversion_rate(self):
        # EURJPY: quote=JPY, account=USD -> pip value = (0.01*100000) * (JPY->USD rate)
        # JPY->USD rate = 1/USDJPY = 1/149.50
        conv = 1 / 149.50
        pv = pip_value_per_lot("EURJPY", current_price=162.20, account_currency="USD", conversion_rate=conv)
        self.assertAlmostEqual(pv, 1000 * conv, places=4)

    def test_bad_pair_format_raises(self):
        with self.assertRaises(PositionSizingError):
            pip_value_per_lot("EUR", current_price=1.0)


class TestCalculatePositionSize(unittest.TestCase):
    def test_direct_pair_matches_hand_calculation(self):
        # 10,000 USD equity, 0.5% risk = $50. EURUSD stop = 30 pips, pip value $10/lot.
        # raw lots = 50 / (30*10) = 0.1667 -> floor to 0.01 step = 0.16
        result = calculate_position_size(
            pair="EURUSD", direction="BUY", entry_price=1.0850, stop_loss=1.0820,
            account_equity=10_000, risk_pct=0.5, account_currency="USD", leverage=100,
        )
        self.assertAlmostEqual(result.stop_distance_pips, 30.0, places=1)
        self.assertEqual(result.lots, 0.16)
        self.assertLessEqual(result.risk_amount, 50.0)  # never rounds UP past the risk budget
        self.assertGreater(result.risk_amount, 45.0)
        self.assertIsNotNone(result.required_margin)

    def test_inverse_pair_usdjpy(self):
        result = calculate_position_size(
            pair="USDJPY", direction="SELL", entry_price=149.50, stop_loss=149.80,
            account_equity=10_000, risk_pct=1.0, account_currency="USD",
        )
        self.assertAlmostEqual(result.stop_distance_pips, 30.0, places=1)
        self.assertGreater(result.lots, 0)
        self.assertLessEqual(result.risk_amount, 100.0)

    def test_cross_pair_with_price_lookup_auto_resolves(self):
        prices = {"USDJPY": 149.50}

        def lookup(pair: str) -> float:
            return prices[pair]

        result = calculate_position_size(
            pair="EURJPY", direction="BUY", entry_price=162.20, stop_loss=161.80,
            account_equity=10_000, risk_pct=0.5, account_currency="USD", price_lookup=lookup,
        )
        self.assertGreater(result.lots, 0)
        self.assertEqual(result.pair, "EURJPY")

    def test_cross_pair_without_conversion_or_lookup_raises(self):
        with self.assertRaises(PositionSizingError):
            calculate_position_size(
                pair="GBPJPY", direction="BUY", entry_price=189.0, stop_loss=188.5,
                account_equity=10_000, risk_pct=0.5, account_currency="USD",
            )

    def test_stop_on_wrong_side_adds_warning_but_still_computes(self):
        result = calculate_position_size(
            pair="EURUSD", direction="BUY", entry_price=1.0850, stop_loss=1.0900,  # stop ABOVE entry for a BUY
            account_equity=10_000, risk_pct=0.5,
        )
        self.assertTrue(any("WARNING" in n for n in result.notes))

    def test_tiny_risk_rounds_to_zero_lots_with_explanation(self):
        result = calculate_position_size(
            pair="EURUSD", direction="BUY", entry_price=1.0850, stop_loss=1.0000,  # huge 850-pip stop
            account_equity=100, risk_pct=0.1,  # tiny equity and risk
        )
        self.assertEqual(result.lots, 0.0)
        self.assertTrue(any("rounds down to 0" in n for n in result.notes))

    def test_never_exceeds_risk_budget_after_rounding(self):
        for equity in (500, 1_000, 10_000, 137_000):
            result = calculate_position_size(
                pair="GBPUSD", direction="SELL", entry_price=1.2650, stop_loss=1.2700,
                account_equity=equity, risk_pct=1.0,
            )
            self.assertLessEqual(result.risk_amount, equity * 0.01 + 1e-6)

    def test_invalid_inputs_raise_positionsizingerror(self):
        base_kwargs = dict(pair="EURUSD", direction="BUY", entry_price=1.0850, stop_loss=1.0820, account_equity=10_000)
        with self.assertRaises(PositionSizingError):
            calculate_position_size(**{**base_kwargs, "account_equity": -1})
        with self.assertRaises(PositionSizingError):
            calculate_position_size(**{**base_kwargs, "risk_pct": 0})
        with self.assertRaises(PositionSizingError):
            calculate_position_size(**{**base_kwargs, "direction": "HOLD"})
        with self.assertRaises(PositionSizingError):
            calculate_position_size(**{**base_kwargs, "stop_loss": 1.0850})  # equal to entry

    def test_required_margin_scales_with_leverage(self):
        low_leverage = calculate_position_size(
            pair="EURUSD", direction="BUY", entry_price=1.0850, stop_loss=1.0820,
            account_equity=10_000, risk_pct=1.0, leverage=10,
        )
        high_leverage = calculate_position_size(
            pair="EURUSD", direction="BUY", entry_price=1.0850, stop_loss=1.0820,
            account_equity=10_000, risk_pct=1.0, leverage=500,
        )
        self.assertGreater(low_leverage.required_margin, high_leverage.required_margin)

    def test_summary_line_is_human_readable(self):
        result = calculate_position_size(
            pair="EURUSD", direction="BUY", entry_price=1.0850, stop_loss=1.0820,
            account_equity=10_000, risk_pct=0.5,
        )
        line = result.summary_line()
        self.assertIn("EURUSD", line)
        self.assertIn("lots", line)


if __name__ == "__main__":
    unittest.main()
