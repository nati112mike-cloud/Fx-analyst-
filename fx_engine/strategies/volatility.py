from __future__ import annotations

import pandas as pd

from fx_engine.strategies.base import BaseStrategy, Direction, StrategyResult, StrategySpec, compute_tp


class VolatilityStrategy(BaseStrategy):
    spec = StrategySpec(
        name="volatility",
        version="1.0",
        market_conditions="Volatility squeeze: Bollinger Band width sits at a multi-bar low (compression), "
                           "then price breaks decisively out of the bands -- a direct, testable version of "
                           "'compression precedes expansion.'",
        pairs=["*"],
        timeframes=["H1", "H4"],
        indicators=["BollingerBands(20,2)", "ATR14", "ATR percentile(100)"],
        entry_conditions=[
            "Bollinger Band width was in its lowest 20th percentile over the last 100 bars within the last "
            "5 bars (the squeeze)",
            "Close breaks outside the band in either direction on the current bar",
        ],
        exit_conditions=["Stop-loss or take-profit hit"],
        stop_loss_method="Opposite Bollinger Band, or 1x ATR, whichever is tighter",
        take_profit_method="2x the pre-breakout band width projected from the breakout point, floor of "
                            "MIN_RR_AFTER_COSTS",
        min_risk_reward=1.6,
        max_holding_bars=25,
        invalidation_conditions=["Price closes back inside the bands within 2 bars"],
        excluded_regimes=["strong_bull_trend", "strong_bear_trend"],
        required_data=["OHLC H1 or H4"],
        known_weaknesses=[
            "Squeeze breakouts can be false starts (whipsaw back into the range) more often than genuine "
            "expansions -- this is the single riskiest module in the ensemble and is weighted accordingly",
        ],
    )

    def generate(self, df: pd.DataFrame, pair: str, regime: str, features: pd.DataFrame | None = None) -> StrategyResult:
        if len(df) < 110:
            return self.neutral(df, regime, "insufficient history")
        if regime in {"strong_bull_trend", "strong_bear_trend"}:
            return self.neutral(df, regime, f"regime '{regime}' excluded for volatility")

        if features is None:
            from fx_engine.features import compute_feature_frame
            features = compute_feature_frame(df)

        width_pct_rank = features["bb_width_pctrank"]
        c = df["close"].iloc[-1]
        upper, lower = features["bb_upper"].iloc[-1], features["bb_lower"].iloc[-1]
        width = features["bb_width"].iloc[-1]
        a = features["atr14"].iloc[-1]
        ts = df.index[-1]

        if pd.isna(upper) or pd.isna(a) or width_pct_rank.iloc[-6:-1].isna().any():
            return self.neutral(df, regime, "indicators warming up")

        was_squeezed = (width_pct_rank.iloc[-6:-1] <= 0.20).any()
        if not was_squeezed:
            return self.neutral(df, regime, "no recent volatility squeeze")

        pre_squeeze_width = width * c  # approx band width in price units

        if c > upper:
            sl = lower if (c - lower) < 1.0 * a else c - 1.0 * a
            entry = c
            tp_measured = entry + 2 * pre_squeeze_width
            tp = max(tp_measured, compute_tp(entry, sl, Direction.BUY, self.spec.min_risk_reward))
            return StrategyResult(
                strategy=self.spec.name, version=self.spec.version, direction=Direction.BUY,
                confidence=0.45, entry_low=entry - 0.1 * a, entry_high=entry + 0.15 * a,
                stop_loss=sl, take_profit=tp,
                reason="Bollinger Band squeeze (low width percentile) followed by upside breakout",
                regime=regime, timestamp=ts,
            )
        if c < lower:
            sl = upper if (upper - c) < 1.0 * a else c + 1.0 * a
            entry = c
            tp_measured = entry - 2 * pre_squeeze_width
            tp = min(tp_measured, compute_tp(entry, sl, Direction.SELL, self.spec.min_risk_reward))
            return StrategyResult(
                strategy=self.spec.name, version=self.spec.version, direction=Direction.SELL,
                confidence=0.45, entry_low=entry - 0.15 * a, entry_high=entry + 0.1 * a,
                stop_loss=sl, take_profit=tp,
                reason="Bollinger Band squeeze (low width percentile) followed by downside breakout",
                regime=regime, timestamp=ts,
            )
        return self.neutral(df, regime, "squeeze present but no breakout yet")
