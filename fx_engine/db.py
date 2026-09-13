"""SQLite persistence layer.

Scoped for personal, single-user use (Section 21 of the original plan asked
for a much larger multi-tenant schema with a Users table and full
version-control history; that's unnecessary complexity for one person
running their own engine, so this keeps the essential tables -- strategy
versions, backtests, the signal lifecycle, paper trades, and system health
-- and drops multi-user plumbing. Nothing here is ever overwritten
destructively: strategy_versions and backtests are append-only, matching
the "never overwrite historical strategy definitions" principle.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone

from fx_engine import config

SCHEMA = """
CREATE TABLE IF NOT EXISTS strategy_versions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    spec_json TEXT NOT NULL,
    created_at TEXT NOT NULL,
    UNIQUE(name, version)
);

CREATE TABLE IF NOT EXISTS backtests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    strategy TEXT NOT NULL,
    version TEXT NOT NULL,
    pair TEXT NOT NULL,
    timeframe TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    metrics_json TEXT NOT NULL,
    walk_forward_verdict TEXT,
    walk_forward_json TEXT,
    data_provider TEXT,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signals (
    id TEXT PRIMARY KEY,
    pair TEXT NOT NULL,
    direction TEXT NOT NULL,
    overall_signal TEXT NOT NULL,
    score REAL NOT NULL,
    entry_low REAL, entry_high REAL, stop_loss REAL,
    take_profit_1 REAL, take_profit_2 REAL,
    spread_pips REAL,
    regime TEXT,
    strategies_agree_json TEXT,
    reason TEXT,
    status TEXT NOT NULL DEFAULT 'PENDING',
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS signal_outcomes (
    signal_id TEXT PRIMARY KEY REFERENCES signals(id),
    resolved_at TEXT,
    outcome TEXT,          -- TP | SL | EXPIRED | INVALIDATED
    exit_price REAL,
    pnl_r REAL
);

CREATE TABLE IF NOT EXISTS paper_trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id TEXT REFERENCES signals(id),
    pair TEXT NOT NULL,
    direction TEXT NOT NULL,
    entry_time TEXT NOT NULL,
    entry_price REAL NOT NULL,
    stop_loss REAL NOT NULL,
    take_profit REAL NOT NULL,
    exit_time TEXT,
    exit_price REAL,
    exit_reason TEXT,
    pnl_r REAL,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    component TEXT NOT NULL,
    status TEXT NOT NULL,       -- OK | WARN | ERROR
    message TEXT,
    checked_at TEXT NOT NULL
);
"""


@dataclass
class Database:
    path: str = config.DB_PATH

    @contextmanager
    def _conn(self):
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self._conn() as conn:
            conn.executescript(SCHEMA)

    def save_strategy_version(self, name: str, version: str, spec_dict: dict) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO strategy_versions (name, version, spec_json, created_at) VALUES (?,?,?,?)",
                (name, version, json.dumps(spec_dict), datetime.now(timezone.utc).isoformat()),
            )

    def save_backtest(self, strategy: str, version: str, pair: str, timeframe: str,
                       period_start: str, period_end: str, metrics: dict,
                       walk_forward_verdict: str | None = None, walk_forward_json: dict | None = None,
                       data_provider: str = "") -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO backtests
                   (strategy, version, pair, timeframe, period_start, period_end, metrics_json,
                    walk_forward_verdict, walk_forward_json, data_provider, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
                (strategy, version, pair, timeframe, period_start, period_end, json.dumps(metrics),
                 walk_forward_verdict, json.dumps(walk_forward_json) if walk_forward_json else None,
                 data_provider, datetime.now(timezone.utc).isoformat()),
            )
            return cur.lastrowid

    def latest_backtest_metrics(self, strategy: str, pair: str | None = None) -> list[dict]:
        q = "SELECT * FROM backtests WHERE strategy = ?"
        params: list = [strategy]
        if pair:
            q += " AND pair = ?"
            params.append(pair)
        q += " ORDER BY created_at DESC"
        with self._conn() as conn:
            rows = conn.execute(q, params).fetchall()
        return [dict(r) for r in rows]

    def strategy_expectancy(self, strategy: str) -> float | None:
        """Latest known out-of-sample-ish expectancy for a strategy, used by
        the ensemble to weight votes. Returns None if never backtested."""
        rows = self.latest_backtest_metrics(strategy)
        if not rows:
            return None
        metrics = json.loads(rows[0]["metrics_json"])
        return metrics.get("expectancy_r")

    def insert_signal(self, signal_row: dict) -> str:
        signal_id = signal_row.get("id") or str(uuid.uuid4())
        with self._conn() as conn:
            conn.execute(
                """INSERT INTO signals
                   (id, pair, direction, overall_signal, score, entry_low, entry_high, stop_loss,
                    take_profit_1, take_profit_2, spread_pips, regime, strategies_agree_json, reason,
                    status, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (signal_id, signal_row["pair"], signal_row["direction"], signal_row["overall_signal"],
                 signal_row["score"], signal_row.get("entry_low"), signal_row.get("entry_high"),
                 signal_row.get("stop_loss"), signal_row.get("take_profit_1"), signal_row.get("take_profit_2"),
                 signal_row.get("spread_pips"), signal_row.get("regime"),
                 json.dumps(signal_row.get("strategies_agree", {})), signal_row.get("reason", ""),
                 signal_row.get("status", "PENDING"), datetime.now(timezone.utc).isoformat()),
            )
        return signal_id

    def recent_signal_exists(self, pair: str, direction: str, minutes: int = 240) -> bool:
        with self._conn() as conn:
            row = conn.execute(
                """SELECT id FROM signals WHERE pair=? AND direction=?
                   AND created_at >= datetime('now', ?) ORDER BY created_at DESC LIMIT 1""",
                (pair, direction, f"-{minutes} minutes"),
            ).fetchone()
        return row is not None

    def resolve_signal(self, signal_id: str, outcome: str, exit_price: float | None, pnl_r: float | None) -> None:
        with self._conn() as conn:
            conn.execute(
                "UPDATE signals SET status = ? WHERE id = ?",
                (outcome, signal_id),
            )
            conn.execute(
                """INSERT INTO signal_outcomes (signal_id, resolved_at, outcome, exit_price, pnl_r)
                   VALUES (?,?,?,?,?)
                   ON CONFLICT(signal_id) DO UPDATE SET resolved_at=excluded.resolved_at,
                       outcome=excluded.outcome, exit_price=excluded.exit_price, pnl_r=excluded.pnl_r""",
                (signal_id, datetime.now(timezone.utc).isoformat(), outcome, exit_price, pnl_r),
            )

    def insert_paper_trade(self, row: dict) -> int:
        with self._conn() as conn:
            cur = conn.execute(
                """INSERT INTO paper_trades
                   (signal_id, pair, direction, entry_time, entry_price, stop_loss, take_profit,
                    created_at) VALUES (?,?,?,?,?,?,?,?)""",
                (row.get("signal_id"), row["pair"], row["direction"], row["entry_time"], row["entry_price"],
                 row["stop_loss"], row["take_profit"], datetime.now(timezone.utc).isoformat()),
            )
            return cur.lastrowid

    def close_paper_trade(self, trade_id: int, exit_time: str, exit_price: float, exit_reason: str, pnl_r: float) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE paper_trades SET exit_time=?, exit_price=?, exit_reason=?, pnl_r=?
                   WHERE id=?""",
                (exit_time, exit_price, exit_reason, pnl_r, trade_id),
            )

    def open_paper_trades(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM paper_trades WHERE exit_time IS NULL").fetchall()
        return [dict(r) for r in rows]

    def log_health(self, component: str, status: str, message: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO system_health (component, status, message, checked_at) VALUES (?,?,?,?)",
                (component, status, message, datetime.now(timezone.utc).isoformat()),
            )
