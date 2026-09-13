"""Combine independent strategy opinions into one overall signal.

Section 9/10 of the plan: no single strategy overrides the rest, and
weighting must come from validated historical performance rather than
arbitrary preference. Weights come from Database.strategy_expectancy()
(the latest backtest's expectancy_r for that strategy) when available;
until a strategy has actually been backtested, it gets an equal default
weight and the result is explicitly flagged UNVALIDATED so the Telegram
message / dashboard never implies more rigor than currently exists.
"""
from __future__ import annotations

from dataclasses import dataclass, field

from fx_engine.strategies.base import Direction, StrategyResult

OVERALL_LEVELS = [
    "STRONG_SELL", "SELL", "WEAK_SELL", "NO_TRADE", "WEAK_BUY", "BUY", "STRONG_BUY",
]

MIN_AGREEING_STRATEGIES = 2


@dataclass
class EnsembleResult:
    overall_signal: str
    direction: Direction
    score: float  # -1 (all-weight sell) .. +1 (all-weight buy)
    buy_weight: float
    sell_weight: float
    total_weight: float
    agreeing: list[StrategyResult]
    all_results: dict[str, StrategyResult]
    weights_used: dict[str, float]
    unvalidated: bool
    primary: StrategyResult | None  # the agreeing, actionable result used for entry/SL/TP


def classify_score(score: float) -> str:
    if score >= 0.55:
        return "STRONG_BUY"
    if score >= 0.30:
        return "BUY"
    if score >= 0.12:
        return "WEAK_BUY"
    if score <= -0.55:
        return "STRONG_SELL"
    if score <= -0.30:
        return "SELL"
    if score <= -0.12:
        return "WEAK_SELL"
    return "NO_TRADE"


class StrategyEnsemble:
    def __init__(self, weight_lookup=None):
        """weight_lookup: callable(strategy_name) -> float|None, typically
        Database.strategy_expectancy. If None, all strategies get equal
        weight (fully unvalidated mode)."""
        self._weight_lookup = weight_lookup

    def _weights_for(self, strategy_names: list[str]) -> tuple[dict[str, float], bool]:
        weights = {}
        any_missing = False
        for name in strategy_names:
            raw = self._weight_lookup(name) if self._weight_lookup else None
            if raw is None:
                weights[name] = 1.0
                any_missing = True
            else:
                # only positive validated expectancy earns weight above the
                # equal-weight floor; a strategy with negative expectancy
                # should barely count at all
                weights[name] = max(0.05, 1.0 + raw * 3)
        return weights, any_missing

    def combine(self, results: dict[str, StrategyResult]) -> EnsembleResult:
        # multi_timeframe is confirmation-only (no SL/TP) -- it still votes
        # but can never be the `primary` result an entry is built from.
        weights, unvalidated = self._weights_for(list(results.keys()))
        total_weight = sum(weights.values())

        buy_weight = sum(weights[n] for n, r in results.items() if r.direction == Direction.BUY)
        sell_weight = sum(weights[n] for n, r in results.items() if r.direction == Direction.SELL)
        score = (buy_weight - sell_weight) / total_weight if total_weight > 0 else 0.0

        buy_count = sum(1 for r in results.values() if r.direction == Direction.BUY)
        sell_count = sum(1 for r in results.values() if r.direction == Direction.SELL)

        overall = classify_score(score)
        direction = Direction.NEUTRAL
        if overall in ("STRONG_BUY", "BUY", "WEAK_BUY") and buy_count >= MIN_AGREEING_STRATEGIES:
            direction = Direction.BUY
        elif overall in ("STRONG_SELL", "SELL", "WEAK_SELL") and sell_count >= MIN_AGREEING_STRATEGIES:
            direction = Direction.SELL
        else:
            overall = "NO_TRADE"

        agreeing = [r for r in results.values() if r.direction == direction] if direction != Direction.NEUTRAL else []
        actionable_agreeing = [r for r in agreeing if r.is_actionable]
        primary = None
        if actionable_agreeing:
            primary = max(actionable_agreeing, key=lambda r: weights.get(r.strategy, 0.0))

        return EnsembleResult(
            overall_signal=overall, direction=direction, score=score,
            buy_weight=buy_weight, sell_weight=sell_weight, total_weight=total_weight,
            agreeing=agreeing, all_results=results, weights_used=weights,
            unvalidated=unvalidated, primary=primary,
        )
