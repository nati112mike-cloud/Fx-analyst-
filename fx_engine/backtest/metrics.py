"""Performance metrics computed from a list of closed Trade objects.

Every metric the plan's Section 12 asks for, computed in R-multiples
(pnl / initial risk) so results are comparable across pairs with very
different pip values.
"""
from __future__ import annotations

import math
from dataclasses import dataclass, field

from fx_engine.backtest.engine import Trade


@dataclass
class Metrics:
    total_trades: int = 0
    win_rate: float = 0.0
    loss_rate: float = 0.0
    profit_factor: float = 0.0
    expectancy_r: float = 0.0
    avg_win_r: float = 0.0
    avg_loss_r: float = 0.0
    net_r: float = 0.0
    max_drawdown_r: float = 0.0
    sharpe: float = 0.0
    sortino: float = 0.0
    max_consecutive_losses: int = 0
    recovery_factor: float = 0.0
    by_pair: dict = field(default_factory=dict)
    by_regime: dict = field(default_factory=dict)
    by_exit_reason: dict = field(default_factory=dict)

    def as_dict(self) -> dict:
        return {
            "total_trades": self.total_trades, "win_rate": round(self.win_rate, 4),
            "loss_rate": round(self.loss_rate, 4), "profit_factor": round(self.profit_factor, 3),
            "expectancy_r": round(self.expectancy_r, 4), "avg_win_r": round(self.avg_win_r, 3),
            "avg_loss_r": round(self.avg_loss_r, 3), "net_r": round(self.net_r, 3),
            "max_drawdown_r": round(self.max_drawdown_r, 3), "sharpe": round(self.sharpe, 3),
            "sortino": round(self.sortino, 3), "max_consecutive_losses": self.max_consecutive_losses,
            "recovery_factor": round(self.recovery_factor, 3),
            "by_pair": self.by_pair, "by_regime": self.by_regime, "by_exit_reason": self.by_exit_reason,
        }


def _equity_curve(r_values: list[float]) -> list[float]:
    curve = [0.0]
    for r in r_values:
        curve.append(curve[-1] + r)
    return curve


def _max_drawdown(curve: list[float]) -> float:
    peak = curve[0]
    max_dd = 0.0
    for v in curve:
        peak = max(peak, v)
        max_dd = max(max_dd, peak - v)
    return max_dd


def _sharpe_like(r_values: list[float], downside_only: bool = False) -> float:
    if len(r_values) < 2:
        return 0.0
    mean = sum(r_values) / len(r_values)
    if downside_only:
        neg = [min(0.0, r) for r in r_values]
        variance = sum(x ** 2 for x in neg) / len(neg)
    else:
        variance = sum((r - mean) ** 2 for r in r_values) / len(r_values)
    std = math.sqrt(variance)
    if std == 0:
        return 0.0
    return (mean / std) * math.sqrt(len(r_values))


def _group_by(trades: list[Trade], key_fn) -> dict:
    groups: dict = {}
    for t in trades:
        k = key_fn(t)
        groups.setdefault(k, []).append(t)
    out = {}
    for k, group_trades in groups.items():
        r_values = [t.pnl_r() for t in group_trades]
        wins = [r for r in r_values if r > 0]
        out[k] = {
            "trades": len(group_trades),
            "win_rate": round(len(wins) / len(group_trades), 3) if group_trades else 0.0,
            "expectancy_r": round(sum(r_values) / len(r_values), 4) if r_values else 0.0,
            "net_r": round(sum(r_values), 3),
        }
    return out


def compute_metrics(trades: list[Trade]) -> Metrics:
    closed = [t for t in trades if t.is_closed]
    m = Metrics(total_trades=len(closed))
    if not closed:
        return m

    r_values = [t.pnl_r() for t in closed]
    wins = [r for r in r_values if r > 0]
    losses = [r for r in r_values if r <= 0]

    m.win_rate = len(wins) / len(closed)
    m.loss_rate = len(losses) / len(closed)
    m.avg_win_r = sum(wins) / len(wins) if wins else 0.0
    m.avg_loss_r = sum(losses) / len(losses) if losses else 0.0
    gross_win = sum(wins)
    gross_loss = abs(sum(losses))
    m.profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    m.expectancy_r = sum(r_values) / len(r_values)
    m.net_r = sum(r_values)

    curve = _equity_curve(r_values)
    m.max_drawdown_r = _max_drawdown(curve)
    m.recovery_factor = (m.net_r / m.max_drawdown_r) if m.max_drawdown_r > 0 else 0.0

    m.sharpe = _sharpe_like(r_values, downside_only=False)
    m.sortino = _sharpe_like(r_values, downside_only=True)

    max_streak = streak = 0
    for r in r_values:
        if r <= 0:
            streak += 1
            max_streak = max(max_streak, streak)
        else:
            streak = 0
    m.max_consecutive_losses = max_streak

    m.by_pair = _group_by(closed, lambda t: t.pair)
    m.by_regime = _group_by(closed, lambda t: t.regime_at_entry)
    m.by_exit_reason = _group_by(closed, lambda t: t.exit_reason)

    return m
