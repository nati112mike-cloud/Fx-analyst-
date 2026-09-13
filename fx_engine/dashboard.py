"""Local read-only dashboard over the SQLite DB (Section 22).

A small Flask app -- no external network calls, no CDN assets, nothing
that needs internet access to render. Entirely offline-testable via
Flask's test client (see tests/test_dashboard.py), which is how this was
verified in a sandbox with no outbound access.

Read-only by design: no route here writes to the database or talks to a
broker. Binds to 127.0.0.1 by default -- see docs/SECURITY.md before
exposing it any wider than that.
"""
from __future__ import annotations

from flask import Flask, render_template

from fx_engine.db import Database


def _equity_curve_svg_points(curve: list[dict], width: int = 640, height: int = 160, pad: int = 8) -> str:
    if len(curve) < 2:
        return ""
    values = [p["cumulative"] for p in curve]
    lo, hi = min(values), max(values)
    span = (hi - lo) or 1.0
    n = len(values)
    points = []
    for i, v in enumerate(values):
        x = pad + (i / (n - 1)) * (width - 2 * pad)
        y = pad + (1 - (v - lo) / span) * (height - 2 * pad)
        points.append(f"{x:.1f},{y:.1f}")
    return " ".join(points)


def create_app(db: Database | None = None) -> Flask:
    app = Flask(__name__)
    app.config["FX_DB"] = db or Database()

    def get_db() -> Database:
        return app.config["FX_DB"]

    @app.route("/")
    def overview():
        d = get_db()
        signals = d.recent_signals(limit=10)
        health = d.recent_health(limit=10)
        open_trades = d.open_paper_trades()
        curve = d.paper_equity_curve()
        latest_equity = curve[-1]["cumulative"] if curve else 0.0
        return render_template(
            "overview.html", signals=signals, health=health, open_trades=open_trades,
            latest_equity=latest_equity, total_closed=len(curve),
        )

    @app.route("/signals")
    def signals_page():
        return render_template("signals.html", signals=get_db().recent_signals(limit=200))

    @app.route("/strategies")
    def strategies_page():
        return render_template("strategies.html", backtests=get_db().latest_backtest_per_strategy_pair())

    @app.route("/paper-trades")
    def paper_trades_page():
        d = get_db()
        curve = d.paper_equity_curve()
        return render_template(
            "paper_trades.html", trades=d.recent_paper_trades(limit=200), curve=curve,
            svg_points=_equity_curve_svg_points(curve),
            curve_min=min((p["cumulative"] for p in curve), default=0.0),
            curve_max=max((p["cumulative"] for p in curve), default=0.0),
        )

    @app.route("/health")
    def health_page():
        return render_template("health.html", health=get_db().recent_health(limit=100))

    return app
