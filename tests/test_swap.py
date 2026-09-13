"""Tests for swap/rollover cost modeling: rollover-crossing counting
(including triple-Wednesday), pip->price conversion, and that it actually
flows into Trade.pnl_price()/pnl_r() in the backtest engine.

Run with: python -m unittest discover -s tests
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fx_engine import config
from fx_engine.backtest.engine import Trade
from fx_engine.swap import count_rollover_charges, swap_pips


def _utc(*args) -> datetime:
    return datetime(*args, tzinfo=timezone.utc)


class TestCountRolloverCharges(unittest.TestCase):
    # 2024-03-11 is a Monday, 2024-03-13 is a Wednesday (TRIPLE_SWAP_WEEKDAY)
    def test_same_day_before_rollover_is_zero(self):
        self.assertEqual(count_rollover_charges(_utc(2024, 3, 11, 10, 0), _utc(2024, 3, 11, 20, 0)), 0)

    def test_one_ordinary_rollover(self):
        self.assertEqual(count_rollover_charges(_utc(2024, 3, 11, 10, 0), _utc(2024, 3, 12, 10, 0)), 1)

    def test_spans_wednesday_triple_charge(self):
        # Tue 10:00 -> Wed 22:00: crosses Tue 21:00 (x1) and Wed 21:00 (x3) = 4
        self.assertEqual(count_rollover_charges(_utc(2024, 3, 12, 10, 0), _utc(2024, 3, 13, 22, 0)), 4)

    def test_four_night_span_including_wednesday(self):
        # Mon 10:00 -> Thu 10:00: Mon(1) + Tue(1) + Wed(3) = 5
        self.assertEqual(count_rollover_charges(_utc(2024, 3, 11, 10, 0), _utc(2024, 3, 14, 10, 0)), 5)

    def test_exit_before_entry_is_zero(self):
        self.assertEqual(count_rollover_charges(_utc(2024, 3, 12, 10, 0), _utc(2024, 3, 11, 10, 0)), 0)

    def test_exit_exactly_at_rollover_counts_that_night(self):
        self.assertEqual(count_rollover_charges(_utc(2024, 3, 11, 10, 0), _utc(2024, 3, 11, 21, 0)), 1)

    def test_entry_exactly_at_rollover_does_not_double_count_that_moment(self):
        # entering exactly at the rollover snapshot doesn't get charged for
        # that instant -- only rollovers strictly after entry count
        self.assertEqual(count_rollover_charges(_utc(2024, 3, 11, 21, 0), _utc(2024, 3, 11, 21, 0)), 0)


class TestSwapPips(unittest.TestCase):
    def test_zero_by_default(self):
        self.assertEqual(swap_pips("EURUSD", "BUY", _utc(2024, 3, 11, 10, 0), _utc(2024, 3, 14, 10, 0)), 0.0)

    def test_long_rate_applied_for_buy(self):
        with patch.dict(config.SWAP_LONG_PIPS_PER_NIGHT, {"EURUSD": -0.7}):
            result = swap_pips("EURUSD", "BUY", _utc(2024, 3, 11, 10, 0), _utc(2024, 3, 12, 10, 0))
            self.assertAlmostEqual(result, -0.7)  # 1 ordinary night

    def test_short_rate_applied_for_sell_and_wednesday_tripled(self):
        with patch.dict(config.SWAP_SHORT_PIPS_PER_NIGHT, {"EURUSD": 0.3}):
            result = swap_pips("EURUSD", "SELL", _utc(2024, 3, 12, 10, 0), _utc(2024, 3, 13, 22, 0))
            self.assertAlmostEqual(result, 0.3 * 4)  # 1 ordinary + 1 triple = 4 units

    def test_buy_does_not_use_short_table(self):
        with patch.dict(config.SWAP_SHORT_PIPS_PER_NIGHT, {"EURUSD": -5.0}), \
             patch.dict(config.SWAP_LONG_PIPS_PER_NIGHT, {"EURUSD": 0.0}):
            result = swap_pips("EURUSD", "BUY", _utc(2024, 3, 11, 10, 0), _utc(2024, 3, 12, 10, 0))
            self.assertEqual(result, 0.0)


class TestSwapFlowsIntoTradePnl(unittest.TestCase):
    def test_pnl_price_includes_swap_cost(self):
        import pandas as pd
        with patch.dict(config.SWAP_LONG_PIPS_PER_NIGHT, {"EURUSD": -1.0}):
            trade = Trade(
                pair="EURUSD", strategy="test", direction="BUY",
                entry_time=pd.Timestamp(_utc(2024, 3, 11, 10, 0)), entry_price=1.0850,
                stop_loss=1.0820, take_profit=1.0910,
                exit_time=pd.Timestamp(_utc(2024, 3, 14, 10, 0)), exit_price=1.0910,
                exit_reason="TP", risk_distance=0.0030, pip=0.0001,
            )
            # price move: +0.0060; swap: 5 charge-units * -1.0 pips * 0.0001 = -0.0005
            expected_pnl_price = 0.0060 - 0.0005
            self.assertAlmostEqual(trade.pnl_price(), expected_pnl_price, places=6)
            self.assertAlmostEqual(trade.pnl_r(), expected_pnl_price / 0.0030, places=4)

    def test_zero_swap_rate_does_not_change_pnl(self):
        import pandas as pd
        trade = Trade(
            pair="EURUSD", strategy="test", direction="BUY",
            entry_time=pd.Timestamp(_utc(2024, 3, 11, 10, 0)), entry_price=1.0850,
            stop_loss=1.0820, take_profit=1.0910,
            exit_time=pd.Timestamp(_utc(2024, 3, 14, 10, 0)), exit_price=1.0910,
            exit_reason="TP", risk_distance=0.0030, pip=0.0001,
        )
        self.assertAlmostEqual(trade.pnl_price(), 0.0060, places=6)


if __name__ == "__main__":
    unittest.main()
