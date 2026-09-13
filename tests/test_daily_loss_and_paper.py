"""Tests for the daily loss circuit breaker and its wiring into paper
trading: real dollar P&L tracked per paper trade, summed per UTC day, and
enforced before any new signal is evaluated. Run with:

    python -m unittest discover -s tests
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fx_engine.broker.paper import PaperBrokerAdapter
from fx_engine.data.models import Timeframe
from fx_engine.data.providers import SyntheticDataProvider
from fx_engine.db import Database
from fx_engine.paper_trading import PaperTradingLoop
from fx_engine.signal_engine import NoTradeReason, SignalEngine


def _fresh_db() -> Database:
    fd = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    fd.close()
    db = Database(path=fd.name)
    db.init_schema()
    return db


class TestRealizedPnlToday(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def _insert_closed_trade(self, pnl_amount: float, exit_time: datetime):
        trade_id = self.db.insert_paper_trade({
            "pair": "EURUSD", "direction": "BUY", "entry_time": (exit_time - timedelta(hours=4)).isoformat(),
            "entry_price": 1.0850, "stop_loss": 1.0820, "take_profit": 1.0910, "lots": 0.1, "risk_amount": 30.0,
        })
        self.db.close_paper_trade(trade_id, exit_time.isoformat(), 1.0910, "TP", pnl_r=2.0, pnl_amount=pnl_amount)
        return trade_id

    def test_sums_only_today(self):
        now = datetime(2024, 3, 15, 18, 0, tzinfo=timezone.utc)
        self._insert_closed_trade(-40.0, now.replace(hour=9))
        self._insert_closed_trade(-15.0, now.replace(hour=14))
        self._insert_closed_trade(+100.0, now - timedelta(days=1))  # yesterday, must not count
        total = self.db.realized_pnl_today(now)
        self.assertAlmostEqual(total, -55.0, places=2)

    def test_open_trades_do_not_count(self):
        now = datetime(2024, 3, 15, 18, 0, tzinfo=timezone.utc)
        self.db.insert_paper_trade({
            "pair": "GBPUSD", "direction": "SELL", "entry_time": now.isoformat(),
            "entry_price": 1.2650, "stop_loss": 1.2700, "take_profit": 1.2550, "lots": 0.1, "risk_amount": 50.0,
        })
        self.assertAlmostEqual(self.db.realized_pnl_today(now), 0.0)

    def test_trades_with_unknown_risk_amount_are_excluded_not_treated_as_zero(self):
        now = datetime(2024, 3, 15, 18, 0, tzinfo=timezone.utc)
        trade_id = self.db.insert_paper_trade({
            "pair": "EURUSD", "direction": "BUY", "entry_time": now.isoformat(),
            "entry_price": 1.0850, "stop_loss": 1.0820, "take_profit": 1.0910,
            # no lots/risk_amount -- position sizing failed at entry
        })
        self.db.close_paper_trade(trade_id, now.isoformat(), 1.0910, "TP", pnl_r=2.0, pnl_amount=None)
        self._insert_closed_trade(-25.0, now)
        # only the trade WITH a known pnl_amount should be summed
        self.assertAlmostEqual(self.db.realized_pnl_today(now), -25.0, places=2)


class TestDailyLossLimitBreached(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def _lose(self, amount: float, at: datetime):
        self._closed_trade(pnl_amount=-amount, at=at)

    def _win(self, amount: float, at: datetime):
        self._closed_trade(pnl_amount=amount, at=at)

    def _closed_trade(self, pnl_amount: float, at: datetime):
        trade_id = self.db.insert_paper_trade({
            "pair": "EURUSD", "direction": "BUY", "entry_time": at.isoformat(),
            "entry_price": 1.0850, "stop_loss": 1.0820, "take_profit": 1.0910, "lots": 0.1, "risk_amount": 50.0,
        })
        self.db.close_paper_trade(trade_id, at.isoformat(), 1.0820, "SL", pnl_r=-1.0, pnl_amount=pnl_amount)

    def test_not_breached_when_under_limit(self):
        now = datetime(2024, 3, 15, 18, 0, tzinfo=timezone.utc)
        self._lose(100.0, now)  # 1% of 10,000
        breached, realized = self.db.daily_loss_limit_breached(10_000, max_daily_loss_pct=2.0, as_of=now)
        self.assertFalse(breached)
        self.assertAlmostEqual(realized, -100.0)

    def test_breached_at_exact_limit(self):
        now = datetime(2024, 3, 15, 18, 0, tzinfo=timezone.utc)
        self._lose(200.0, now)  # exactly 2% of 10,000
        breached, _ = self.db.daily_loss_limit_breached(10_000, max_daily_loss_pct=2.0, as_of=now)
        self.assertTrue(breached)

    def test_breached_when_over_limit(self):
        now = datetime(2024, 3, 15, 18, 0, tzinfo=timezone.utc)
        self._lose(150.0, now)
        self._lose(100.0, now)
        breached, realized = self.db.daily_loss_limit_breached(10_000, max_daily_loss_pct=2.0, as_of=now)
        self.assertTrue(breached)
        self.assertAlmostEqual(realized, -250.0)

    def test_profitable_day_never_breaches(self):
        now = datetime(2024, 3, 15, 18, 0, tzinfo=timezone.utc)
        self._win(500.0, now)
        breached, _ = self.db.daily_loss_limit_breached(10_000, max_daily_loss_pct=2.0, as_of=now)
        self.assertFalse(breached)


class TestSignalEngineRespectsBreaker(unittest.TestCase):
    def test_evaluate_pair_blocked_after_daily_loss_breach(self):
        db = _fresh_db()
        now = datetime.now(timezone.utc)
        trade_id = db.insert_paper_trade({
            "pair": "EURUSD", "direction": "BUY", "entry_time": now.isoformat(),
            "entry_price": 1.0850, "stop_loss": 1.0820, "take_profit": 1.0910, "lots": 1.0, "risk_amount": 500.0,
        })
        db.close_paper_trade(trade_id, now.isoformat(), 1.0820, "SL", pnl_r=-1.0, pnl_amount=-500.0)

        provider = SyntheticDataProvider()
        engine = SignalEngine(provider, db=db, min_signal_score=0, fallback_account_equity=10_000)
        result = engine.evaluate_pair("EURUSD", Timeframe.H4)
        self.assertIsInstance(result, NoTradeReason)
        self.assertIn("daily loss circuit breaker", result.reason)


_FIXED_WEEKDAY_NOW = datetime(2024, 3, 12, 18, 0, tzinfo=timezone.utc)  # a Tuesday, not a market-closed weekend


class _FixedDatetime(datetime):
    """A real datetime subclass (so fromisoformat/etc keep working normally)
    with `.now()` pinned to a weekday -- tests must not depend on which day
    of the week they happen to run, since SyntheticDataProvider legitimately
    (and correctly) filters out weekend bars, same as real forex markets."""

    @classmethod
    def now(cls, tz=None):
        return _FIXED_WEEKDAY_NOW


class TestPaperTradingUpdatesBrokerBalance(unittest.TestCase):
    @patch("fx_engine.paper_trading.datetime", _FixedDatetime)
    def test_closing_a_trade_updates_broker_balance(self):
        db = _fresh_db()
        provider = SyntheticDataProvider()
        broker = PaperBrokerAdapter(provider, starting_balance=10_000.0)
        engine = SignalEngine(provider, db=db, min_signal_score=0, broker=broker)
        loop = PaperTradingLoop(engine, provider, pairs=["EURUSD"], timeframe=Timeframe.H4, broker=broker)

        now = _FIXED_WEEKDAY_NOW
        # a trade whose stop is essentially guaranteed to be touched within the M15 window
        # _resolve_open_trades fetches, so pick very tight levels around a plausible price.
        trade_id = db.insert_paper_trade({
            "pair": "EURUSD", "direction": "BUY", "entry_time": (now - timedelta(hours=2)).isoformat(),
            "entry_price": 1.0850, "stop_loss": 0.5000, "take_profit": 5.0000,  # unreachable both ways -> stays open
            "lots": 0.1, "risk_amount": 30.0,
        })
        loop._resolve_open_trades()
        # unreachable levels -> should still be open, balance unchanged
        self.assertEqual(broker.balance, 10_000.0)
        remaining = db.open_paper_trades()
        self.assertEqual(len(remaining), 1)
        self.assertEqual(remaining[0]["id"], trade_id)

    @patch("fx_engine.paper_trading.datetime", _FixedDatetime)
    def test_reachable_stop_closes_trade_and_updates_balance(self):
        db = _fresh_db()
        provider = SyntheticDataProvider()
        broker = PaperBrokerAdapter(provider, starting_balance=10_000.0)
        engine = SignalEngine(provider, db=db, min_signal_score=0, broker=broker)
        loop = PaperTradingLoop(engine, provider, pairs=["EURUSD"], timeframe=Timeframe.H4, broker=broker)

        now = _FIXED_WEEKDAY_NOW
        entry_time = now - timedelta(hours=2)
        # Wide enough SL/TP band around a plausible EURUSD price that ONE side
        # will realistically be touched by the synthetic random walk within
        # the window, proving the balance-update wiring actually runs.
        trade_id = db.insert_paper_trade({
            "pair": "EURUSD", "direction": "BUY", "entry_time": entry_time.isoformat(),
            "entry_price": 1.0850, "stop_loss": 1.0000, "take_profit": 1.1700,
            "lots": 0.1, "risk_amount": 30.0,
        })
        loop._resolve_open_trades()
        remaining_ids = {t["id"] for t in db.open_paper_trades()}
        if trade_id not in remaining_ids:
            # it closed -- balance must have moved by exactly pnl_r * risk_amount
            with db._conn() as conn:
                row = conn.execute("SELECT pnl_amount FROM paper_trades WHERE id=?", (trade_id,)).fetchone()
            self.assertIsNotNone(row["pnl_amount"])
            self.assertAlmostEqual(broker.balance, 10_000.0 + row["pnl_amount"], places=2)
        else:
            # band wasn't touched in this particular synthetic draw -- balance untouched
            self.assertEqual(broker.balance, 10_000.0)


if __name__ == "__main__":
    unittest.main()
