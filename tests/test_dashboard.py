"""Tests for the local dashboard: every route renders successfully and
contains data pulled from the database, using Flask's test client (no
real server, no network). Run with: python -m unittest discover -s tests
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fx_engine.dashboard import create_app
from fx_engine.db import Database


def _populated_db() -> Database:
    fd = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    fd.close()
    db = Database(path=fd.name)
    db.init_schema()

    signal_id = db.insert_signal({
        "pair": "EURUSD", "direction": "BUY", "overall_signal": "BUY", "score": 62.0,
        "entry_low": 1.0840, "entry_high": 1.0860, "stop_loss": 1.0800, "take_profit_1": 1.0940,
        "take_profit_2": 1.1000, "spread_pips": 1.1, "regime": "strong_bull_trend",
        "strategies_agree": {"trend_pullback": "BUY", "momentum": "BUY"}, "reason": "test reason",
    })
    db.resolve_signal(signal_id, "TP", 1.0940, 2.0)

    db.save_strategy_version("trend_pullback", "1.0", {"name": "trend_pullback"})
    db.save_backtest(
        "trend_pullback", "1.0", "EURUSD", "H4", "2022-01-01", "2023-01-01",
        {"total_trades": 40, "win_rate": 0.55, "expectancy_r": 0.3, "profit_factor": 1.8, "max_drawdown_r": 4.2},
        walk_forward_verdict="CONSISTENT", data_provider="synthetic",
    )
    db.save_backtest(
        "momentum", "1.0", "GBPUSD", "H4", "2022-01-01", "2023-01-01",
        {"total_trades": 12, "win_rate": 0.4, "expectancy_r": -0.1, "profit_factor": 0.8, "max_drawdown_r": 6.0},
        walk_forward_verdict="REJECTED", data_provider="synthetic",
    )

    now = datetime.now(timezone.utc)
    trade_id = db.insert_paper_trade({
        "signal_id": signal_id, "pair": "EURUSD", "direction": "BUY", "entry_time": (now - timedelta(hours=8)).isoformat(),
        "entry_price": 1.0850, "stop_loss": 1.0800, "take_profit": 1.0940, "lots": 0.15, "risk_amount": 75.0,
    })
    db.close_paper_trade(trade_id, now.isoformat(), 1.0940, "TP", pnl_r=1.8, pnl_amount=135.0)
    db.insert_paper_trade({
        "pair": "USDJPY", "direction": "SELL", "entry_time": now.isoformat(),
        "entry_price": 149.50, "stop_loss": 149.90, "take_profit": 148.70, "lots": 0.1, "risk_amount": 40.0,
    })

    db.log_health("paper_trading_loop", "OK", "cycle complete, 9 pairs")
    db.log_health("signal_engine", "ERROR", "EURJPY: simulated failure")

    return db


class TestDashboardRoutes(unittest.TestCase):
    def setUp(self):
        self.db = _populated_db()
        self.app = create_app(self.db)
        self.app.testing = True
        self.client = self.app.test_client()

    def test_overview_renders_and_shows_data(self):
        resp = self.client.get("/")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("EURUSD", body)
        self.assertIn("USDJPY", body)  # open trade
        self.assertIn("paper_trading_loop", body)

    def test_signals_page_shows_outcome(self):
        resp = self.client.get("/signals")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("EURUSD", body)
        self.assertIn("test reason", body)
        self.assertIn("TP", body)

    def test_strategies_page_shows_both_verdicts(self):
        resp = self.client.get("/strategies")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("trend_pullback", body)
        self.assertIn("CONSISTENT", body)
        self.assertIn("momentum", body)
        self.assertIn("REJECTED", body)

    def test_paper_trades_page_shows_closed_and_open(self):
        resp = self.client.get("/paper-trades")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("EURUSD", body)
        self.assertIn("USDJPY", body)
        self.assertIn("OPEN", body)  # the still-open USDJPY trade's reason column
        self.assertIn("135.00", body)  # the closed trade's dollar pnl

    def test_health_page_shows_both_statuses(self):
        resp = self.client.get("/health")
        self.assertEqual(resp.status_code, 200)
        body = resp.get_data(as_text=True)
        self.assertIn("paper_trading_loop", body)
        self.assertIn("simulated failure", body)


class TestDashboardEmptyDatabase(unittest.TestCase):
    """Every page must render gracefully with nothing in the DB yet --
    this is the state a brand new install starts in."""

    def setUp(self):
        fd = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        fd.close()
        db = Database(path=fd.name)
        db.init_schema()
        self.app = create_app(db)
        self.app.testing = True
        self.client = self.app.test_client()

    def test_all_routes_render_without_data(self):
        for route in ("/", "/signals", "/strategies", "/paper-trades", "/health"):
            resp = self.client.get(route)
            self.assertEqual(resp.status_code, 200, f"{route} did not return 200 on an empty database")


if __name__ == "__main__":
    unittest.main()
