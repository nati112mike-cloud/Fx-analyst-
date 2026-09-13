from __future__ import annotations

import pandas as pd

from fx_engine.strategies.base import BaseStrategy, Direction, StrategyResult, StrategySpec


class MultiTimeframeStrategy(BaseStrategy):
    """Confirmation-only overlay: does not generate its own entries/SL/TP,
    it reports whether trend direction agrees across three horizons, used
    by the ensemble as a corroborating vote (section 9/10), never as a
    standalone trigger (min_risk_reward/stop_loss_method are 'n/a' by design).

    IMPLEMENTATION NOTE: rather than resampling the OHLC series to true
    higher timeframes on every evaluation (correct in principle, but far
    too slow to recompute on every bar of a backtest -- resampling+EMA on
    a growing window at every step is the classic accidental O(n^2)), this
    uses three EMA-pair horizons (~20/50, ~60/150, ~180/450) on the SAME
    base-timeframe series as a fast proxy for base/4x/16x trend agreement.
    This is a legitimate multi-horizon trend technique (related to Guppy
    multiple moving averages), but it is NOT identical to fetching true
    independent H4/D1 candles from the broker -- see known_weaknesses.
    """

    spec = StrategySpec(
        name="multi_timeframe",
        version="1.1",
        market_conditions="Any -- reports whether EMA trend direction agrees across three EMA-pair horizons "
                           "computed on the base-timeframe series (proxy for base/mid/higher timeframe trend).",
        pairs=["*"],
        timeframes=["any"],
        indicators=["EMA20/50 (base)", "EMA60/150 (mid proxy)", "EMA180/450 (high proxy)"],
        entry_conditions=["n/a -- confirmation overlay only, see StrategyEnsemble"],
        exit_conditions=["n/a"],
        stop_loss_method="n/a",
        take_profit_method="n/a",
        min_risk_reward=0.0,
        max_holding_bars=0,
        invalidation_conditions=["n/a"],
        excluded_regimes=[],
        required_data=["OHLC at base timeframe with enough history for the EMA450 to warm up "
                        "(>=~460 bars)"],
        known_weaknesses=[
            "Uses EMA-pair multipliers on the base-timeframe series as a fast proxy for higher-timeframe "
            "trend, not true independently-fetched H4/D1 candles -- correlated with, but not identical to, "
            "a real multi-timeframe read",
            "Needs a long warm-up (450+ bars) before the 'high' horizon is meaningful; returns NEUTRAL "
            "(not a fabricated opinion) until then",
        ],
    )

    def _direction(self, fast: float, slow: float) -> Direction:
        if pd.isna(fast) or pd.isna(slow):
            return Direction.NEUTRAL
        if fast > slow * 1.0002:
            return Direction.BUY
        if fast < slow * 0.9998:
            return Direction.SELL
        return Direction.NEUTRAL

    def generate(self, df: pd.DataFrame, pair: str, regime: str, features: pd.DataFrame | None = None) -> StrategyResult:
        if len(df) < 20:
            return self.neutral(df, regime, "insufficient history")

        if features is None:
            from fx_engine.features import compute_feature_frame
            features = compute_feature_frame(df)

        row = features.iloc[-1]
        base_dir = self._direction(row["mtf_base_fast"], row["mtf_base_slow"])
        mid_dir = self._direction(row["mtf_mid_fast"], row["mtf_mid_slow"])
        high_dir = self._direction(row["mtf_high_fast"], row["mtf_high_slow"])

        votes = [base_dir, mid_dir, high_dir]
        buy_votes = votes.count(Direction.BUY)
        sell_votes = votes.count(Direction.SELL)
        ts = df.index[-1]

        if buy_votes >= 2:
            return StrategyResult(
                strategy=self.spec.name, version=self.spec.version, direction=Direction.BUY,
                confidence=buy_votes / 3, entry_low=None, entry_high=None, stop_loss=None, take_profit=None,
                reason=f"{buy_votes}/3 EMA horizons (base/mid/high proxy) show bullish trend",
                regime=regime, timestamp=ts,
            )
        if sell_votes >= 2:
            return StrategyResult(
                strategy=self.spec.name, version=self.spec.version, direction=Direction.SELL,
                confidence=sell_votes / 3, entry_low=None, entry_high=None, stop_loss=None, take_profit=None,
                reason=f"{sell_votes}/3 EMA horizons (base/mid/high proxy) show bearish trend",
                regime=regime, timestamp=ts,
            )
        return self.neutral(df, regime, "EMA horizons disagree on trend direction")
