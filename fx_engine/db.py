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
    lots REAL,                 -- position size at entry (fx_engine.position_sizing), if it could be computed
    risk_amount REAL,          -- dollar amount risked at entry, lots * stop_distance * pip_value
    exit_time TEXT,
    exit_price REAL,
    exit_reason TEXT,
    pnl_r REAL,
    pnl_amount REAL,           -- real dollar P&L = pnl_r * risk_amount (NULL if risk_amount unknown)
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS system_health (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    component TEXT NOT NULL,
    status TEXT NOT NULL,       -- OK | WARN | ERROR
    message TEXT,
    checked_at TEXT NOT NULL
);

-- Small durable key/value store for cross-run state that isn't a first-class
-- table of its own -- e.g. the Telegram getUpdates offset (fx_engine/telegram_listener.py),
-- so a command from the user is never re-processed on the next scheduled check.
CREATE TABLE IF NOT EXISTS app_state (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
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
                    lots, risk_amount, created_at) VALUES (?,?,?,?,?,?,?,?,?,?)""",
                (row.get("signal_id"), row["pair"], row["direction"], row["entry_time"], row["entry_price"],
                 row["stop_loss"], row["take_profit"], row.get("lots"), row.get("risk_amount"),
                 datetime.now(timezone.utc).isoformat()),
            )
            return cur.lastrowid

    def close_paper_trade(self, trade_id: int, exit_time: str, exit_price: float, exit_reason: str,
                           pnl_r: float, pnl_amount: float | None = None) -> None:
        with self._conn() as conn:
            conn.execute(
                """UPDATE paper_trades SET exit_time=?, exit_price=?, exit_reason=?, pnl_r=?, pnl_amount=?
                   WHERE id=?""",
                (exit_time, exit_price, exit_reason, pnl_r, pnl_amount, trade_id),
            )

    def open_paper_trades(self) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute("SELECT * FROM paper_trades WHERE exit_time IS NULL").fetchall()
        return [dict(r) for r in rows]

    def realized_pnl_today(self, as_of: datetime | None = None) -> float:
        """Sum of pnl_amount for paper trades closed on the same UTC
        calendar day as `as_of` (default: now). Trades with no known
        pnl_amount (position sizing failed at entry) don't count toward
        this -- there's no honest dollar figure to sum for them."""
        as_of = as_of or datetime.now(timezone.utc)
        day = as_of.date().isoformat()
        with self._conn() as conn:
            row = conn.execute(
                """SELECT COALESCE(SUM(pnl_amount), 0.0) AS total FROM paper_trades
                   WHERE exit_time IS NOT NULL AND pnl_amount IS NOT NULL AND date(exit_time) = ?""",
                (day,),
            ).fetchone()
        return float(row["total"])

    def daily_loss_limit_breached(self, current_equity: float, max_daily_loss_pct: float,
                                   as_of: datetime | None = None) -> tuple[bool, float]:
        """Returns (breached, realized_pnl_today). Breached only on
        realized losses -- an unrealized open drawdown doesn't count,
        since paper trades resolve on their own SL/TP, not on a
        mark-to-market check."""
        realized = self.realized_pnl_today(as_of)
        if realized >= 0 or current_equity <= 0:
            return False, realized
        loss_limit = current_equity * (max_daily_loss_pct / 100.0)
        return abs(realized) >= loss_limit, realized

    def recent_signals(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT s.*, o.outcome, o.pnl_r AS outcome_pnl_r, o.exit_price AS outcome_exit_price
                   FROM signals s LEFT JOIN signal_outcomes o ON o.signal_id = s.id
                   ORDER BY s.created_at DESC LIMIT ?""",
                (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def latest_backtest_per_strategy_pair(self) -> list[dict]:
        """One row per (strategy, pair): whichever backtest was saved most
        recently for that combination, with metrics parsed out of JSON."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT b.* FROM backtests b
                   INNER JOIN (
                       SELECT strategy, pair, MAX(created_at) AS max_created
                       FROM backtests GROUP BY strategy, pair
                   ) latest ON b.strategy = latest.strategy AND b.pair = latest.pair
                              AND b.created_at = latest.max_created
                   ORDER BY b.strategy, b.pair"""
            ).fetchall()
        out = []
        for r in rows:
            d = dict(r)
            d["metrics"] = json.loads(d["metrics_json"]) if d.get("metrics_json") else {}
            d["walk_forward"] = json.loads(d["walk_forward_json"]) if d.get("walk_forward_json") else None
            out.append(d)
        return out

    def recent_paper_trades(self, limit: int = 100) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM paper_trades ORDER BY created_at DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def paper_equity_curve(self) -> list[dict]:
        """Cumulative realized P&L over time from closed paper trades, in
        chronological order -- the running equity curve the dashboard plots."""
        with self._conn() as conn:
            rows = conn.execute(
                """SELECT exit_time, pnl_amount FROM paper_trades
                   WHERE exit_time IS NOT NULL AND pnl_amount IS NOT NULL
                   ORDER BY exit_time ASC"""
            ).fetchall()
        cumulative = 0.0
        curve = []
        for r in rows:
            cumulative += r["pnl_amount"]
            curve.append({"exit_time": r["exit_time"], "pnl_amount": r["pnl_amount"], "cumulative": cumulative})
        return curve

    def recent_health(self, limit: int = 50) -> list[dict]:
        with self._conn() as conn:
            rows = conn.execute(
                "SELECT * FROM system_health ORDER BY id DESC LIMIT ?", (limit,),
            ).fetchall()
        return [dict(r) for r in rows]

    def log_health(self, component: str, status: str, message: str = "") -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO system_health (component, status, message, checked_at) VALUES (?,?,?,?)",
                (component, status, message, datetime.now(timezone.utc).isoformat()),
            )

    def get_state(self, key: str) -> str | None:
        with self._conn() as conn:
            row = conn.execute("SELECT value FROM app_state WHERE key = ?", (key,)).fetchone()
        return row["value"] if row else None

    def set_state(self, key: str, value: str) -> None:
        with self._conn() as conn:
            conn.execute(
                "INSERT INTO app_state (key, value) VALUES (?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
                (key, value),
            )
