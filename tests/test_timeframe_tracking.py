"""Tests for running more than one timeframe track (H4 and H1) against the
same database safely:

- an already-existing database (created before the `timeframe` column
  existed -- e.g. the committed data/paper_trading.sqlite3) must migrate
  cleanly, with old rows defaulting to 'H4' rather than losing data or
  raising on init_schema().
- the signal dedup window (Database.recent_signal_exists) must be scoped
  per timeframe, so an H4 signal on a pair/direction doesn't silently
  suppress a legitimate H1 signal on the same pair/direction shortly after.

    python -m unittest discover -s tests
"""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fx_engine.db import Database


def _pre_timeframe_column_db() -> str:
    """A DB with signals/paper_trades tables as they existed before the
    `timeframe` column was added, with one row in each -- simulating the
    already-committed data/paper_trading.sqlite3 this migration must handle.
    """
    path = tempfile.mktemp(suffix=".sqlite3")
    conn = sqlite3.connect(path)
    conn.execute("""CREATE TABLE signals (
        id TEXT PRIMARY KEY, pair TEXT NOT NULL, direction TEXT NOT NULL,
        overall_signal TEXT NOT NULL, score REAL NOT NULL, entry_low REAL, entry_high REAL,
        stop_loss REAL, take_profit_1 REAL, take_profit_2 REAL, spread_pips REAL, regime TEXT,
        strategies_agree_json TEXT, reason TEXT, status TEXT NOT NULL DEFAULT 'PENDING',
        created_at TEXT NOT NULL)""")
    conn.execute("""INSERT INTO signals (id, pair, direction, overall_signal, score, status, created_at)
        VALUES ('old1', 'EURUSD', 'BUY', 'BUY', 70, 'PENDING', '2026-01-01T00:00:00')""")
    conn.execute("""CREATE TABLE paper_trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT, signal_id TEXT, pair TEXT NOT NULL, direction TEXT NOT NULL,
        entry_time TEXT NOT NULL, entry_price REAL NOT NULL, stop_loss REAL NOT NULL, take_profit REAL NOT NULL,
        lots REAL, risk_amount REAL, exit_time TEXT, exit_price REAL, exit_reason TEXT, pnl_r REAL,
        pnl_amount REAL, created_at TEXT NOT NULL)""")
    conn.execute("""INSERT INTO paper_trades (signal_id, pair, direction, entry_time, entry_price,
        stop_loss, take_profit, created_at)
        VALUES ('old1', 'EURUSD', 'BUY', '2026-01-01T00:00:00', 1.05, 1.04, 1.06, '2026-01-01T00:00:00')""")
    conn.commit()
    conn.close()
    return path


class TestTimeframeColumnMigration(unittest.TestCase):
    def test_pre_existing_db_gets_timeframe_column_defaulted_to_h4(self):
        path = _pre_timeframe_column_db()
        db = Database(path=path)
        db.init_schema()  # must not raise, and must ALTER TABLE in the old rows

        conn = sqlite3.connect(path)
        conn.row_factory = sqlite3.Row
        signal_row = conn.execute("SELECT * FROM signals WHERE id='old1'").fetchone()
        trade_row = conn.execute("SELECT * FROM paper_trades WHERE signal_id='old1'").fetchone()
        self.assertEqual(signal_row["timeframe"], "H4")
        self.assertEqual(trade_row["timeframe"], "H4")

    def test_migration_is_idempotent_across_repeated_init_schema_calls(self):
        path = _pre_timeframe_column_db()
        db = Database(path=path)
        db.init_schema()
        db.init_schema()  # second call must not raise "duplicate column"

    def test_fresh_database_has_timeframe_column_without_migration_path(self):
        fd = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        fd.close()
        db = Database(path=fd.name)
        db.init_schema()
        signal_id = db.insert_signal({
            "pair": "EURUSD", "timeframe": "H1", "direction": "BUY", "overall_signal": "BUY", "score": 60,
        })
        conn = sqlite3.connect(fd.name)
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT timeframe FROM signals WHERE id=?", (signal_id,)).fetchone()
        self.assertEqual(row["timeframe"], "H1")


class TestRecentSignalExistsIsScopedPerTimeframe(unittest.TestCase):
    def setUp(self):
        fd = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
        fd.close()
        self.db = Database(path=fd.name)
        self.db.init_schema()

    def test_h4_signal_does_not_suppress_h1_signal_same_pair_and_direction(self):
        self.db.insert_signal({
            "pair": "EURUSD", "timeframe": "H4", "direction": "BUY", "overall_signal": "BUY", "score": 70,
        })
        self.assertTrue(self.db.recent_signal_exists("EURUSD", "BUY", timeframe="H4", minutes=480))
        self.assertFalse(self.db.recent_signal_exists("EURUSD", "BUY", timeframe="H1", minutes=120))

    def test_same_timeframe_duplicate_is_detected(self):
        self.db.insert_signal({
            "pair": "EURUSD", "timeframe": "H1", "direction": "SELL", "overall_signal": "SELL", "score": 65,
        })
        self.assertTrue(self.db.recent_signal_exists("EURUSD", "SELL", timeframe="H1", minutes=120))

    def test_default_timeframe_is_h4_when_not_specified(self):
        self.db.insert_signal({
            "pair": "GBPUSD", "direction": "BUY", "overall_signal": "BUY", "score": 55,
        })
        self.assertTrue(self.db.recent_signal_exists("GBPUSD", "BUY", timeframe="H4", minutes=480))


if __name__ == "__main__":
    unittest.main()
