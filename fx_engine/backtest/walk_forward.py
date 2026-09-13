"""Walk-forward / out-of-sample / holdout validation.

Per Section 13 of the plan: never trust a single backtest number computed
over the whole dataset. This module runs ONE continuous, no-lookahead
backtest (fx_engine.backtest.engine.BacktestEngine already guarantees no
future leakage bar-by-bar), then partitions the resulting trades by entry
time into N sequential folds plus a final HOLDOUT slice that is reported
separately and is the closest thing this system has to "data the strategy
was never tuned against" -- because these strategies use fixed, hand-specified
rules (not fitted parameters), the walk-forward question here is really
"is the edge stable across sequential time periods, or did it only exist in
one lucky chunk of history" -- which is exactly what fold-vs-fold and
fold-vs-holdout comparison answers.

If you DO start tuning strategy parameters (Section 14, e.g. EMA length),
re-run this per candidate parameter set and compare -- a parameter that
only looks good on the full-sample number but falls apart in later folds
is the overfitting pattern Section 14 warns about explicitly.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd

from fx_engine import config
from fx_engine.backtest.engine import BacktestEngine, BacktestResult, Trade
from fx_engine.backtest.metrics import Metrics, compute_metrics
from fx_engine.strategies.base import BaseStrategy


@dataclass
class FoldReport:
    label: str
    start: pd.Timestamp
    end: pd.Timestamp
    metrics: Metrics


@dataclass
class WalkForwardReport:
    pair: str
    strategy: str
    folds: list[FoldReport] = field(default_factory=list)
    holdout: FoldReport | None = None
    verdict: str = "INCONCLUSIVE"
    notes: list[str] = field(default_factory=list)

    def summary_lines(self) -> list[str]:
        lines = [f"Walk-forward report: {self.strategy} on {self.pair}", f"Verdict: {self.verdict}"]
        for f in self.folds:
            lines.append(
                f"  fold[{f.label}] {f.start.date()}..{f.end.date()}: "
                f"n={f.metrics.total_trades} win_rate={f.metrics.win_rate:.2f} "
                f"expectancy_r={f.metrics.expectancy_r:.3f} PF={f.metrics.profit_factor:.2f}"
            )
        if self.holdout:
            h = self.holdout
            lines.append(
                f"  HOLDOUT {h.start.date()}..{h.end.date()}: n={h.metrics.total_trades} "
                f"win_rate={h.metrics.win_rate:.2f} expectancy_r={h.metrics.expectancy_r:.3f} "
                f"PF={h.metrics.profit_factor:.2f}"
            )
        lines.extend(f"  note: {n}" for n in self.notes)
        return lines


def _partition_by_time(trades: list[Trade], boundaries: list[tuple[pd.Timestamp, pd.Timestamp]]) -> list[list[Trade]]:
    buckets: list[list[Trade]] = [[] for _ in boundaries]
    for t in trades:
        for i, (s, e) in enumerate(boundaries):
            if s <= t.entry_time < e:
                buckets[i].append(t)
                break
    return buckets


def run_walk_forward(
    strategy: BaseStrategy,
    df: pd.DataFrame,
    pair: str,
    n_folds: int = config.WALK_FORWARD_FOLDS,
    holdout_fraction: float = config.HOLDOUT_FRACTION,
    warmup_bars: int = 210,
) -> WalkForwardReport:
    engine = BacktestEngine(warmup_bars=warmup_bars)
    result: BacktestResult = engine.run(strategy, df, pair)
    trades = result.trades

    total_span_start, total_span_end = df.index[warmup_bars], df.index[-1]
    total_seconds = (total_span_end - total_span_start).total_seconds()
    holdout_seconds = total_seconds * holdout_fraction
    fold_seconds = (total_seconds - holdout_seconds) / n_folds

    fold_bounds = []
    cursor = total_span_start
    for i in range(n_folds):
        end = cursor + pd.Timedelta(seconds=fold_seconds)
        fold_bounds.append((cursor, end))
        cursor = end
    holdout_bounds = (cursor, total_span_end + pd.Timedelta(seconds=1))

    buckets = _partition_by_time(trades, fold_bounds + [holdout_bounds])
    fold_buckets, holdout_bucket = buckets[:-1], buckets[-1]

    report = WalkForwardReport(pair=pair, strategy=strategy.spec.name)
    for i, (bucket, (s, e)) in enumerate(zip(fold_buckets, fold_bounds)):
        report.folds.append(FoldReport(label=f"{i + 1}/{n_folds}", start=s, end=e, metrics=compute_metrics(bucket)))
    hs, he = holdout_bounds
    report.holdout = FoldReport(label="holdout", start=hs, end=min(he, total_span_end), metrics=compute_metrics(holdout_bucket))

    _assess(report)
    return report


def _assess(report: WalkForwardReport) -> None:
    fold_expectancies = [f.metrics.expectancy_r for f in report.folds if f.metrics.total_trades >= 5]
    fold_trade_counts = [f.metrics.total_trades for f in report.folds]

    if not fold_expectancies or sum(fold_trade_counts) < 15:
        report.verdict = "INCONCLUSIVE"
        report.notes.append("Too few trades across folds to draw a statistically meaningful conclusion. "
                             "This strategy/pair/period combination needs a longer history or more pairs "
                             "before it can be trusted either way.")
        return

    positive_folds = sum(1 for e in fold_expectancies if e > 0)
    negative_folds = sum(1 for e in fold_expectancies if e <= 0)
    avg_fold_expectancy = sum(fold_expectancies) / len(fold_expectancies)

    holdout = report.holdout
    holdout_trades = holdout.metrics.total_trades if holdout else 0
    holdout_expectancy = holdout.metrics.expectancy_r if holdout else 0.0

    if negative_folds > positive_folds:
        report.verdict = "REJECTED"
        report.notes.append(f"Expectancy was negative in {negative_folds}/{len(fold_expectancies)} folds -- "
                             "no consistent edge across time, do not trade this as-is.")
        return

    if holdout_trades < 5:
        report.verdict = "INCONCLUSIVE"
        report.notes.append("Holdout window produced too few trades to validate against; widen the date "
                             "range or pair universe before trusting the fold results alone.")
        return

    if avg_fold_expectancy > 0 and holdout_expectancy <= 0:
        report.verdict = "OVERFIT_OR_DEGRADED"
        report.notes.append(f"Positive average fold expectancy ({avg_fold_expectancy:.3f}R) did not carry "
                             f"into the untouched holdout period ({holdout_expectancy:.3f}R). Treat this as "
                             "a strategy that stopped working, not one that's about to.")
        return

    if avg_fold_expectancy > 0 and holdout_expectancy < avg_fold_expectancy * 0.3:
        report.verdict = "WEAK_OUT_OF_SAMPLE"
        report.notes.append(f"Holdout expectancy ({holdout_expectancy:.3f}R) is far below the average fold "
                             f"expectancy ({avg_fold_expectancy:.3f}R). Edge may be decaying -- monitor closely "
                             "in paper mode before increasing size.")
        return

    report.verdict = "CONSISTENT"
    report.notes.append(f"Expectancy positive in {positive_folds}/{len(fold_expectancies)} folds and held up "
                         f"in the holdout period ({holdout_expectancy:.3f}R). Proceed to paper trading -- this "
                         "is still not a guarantee of future performance.")
