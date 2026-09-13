from __future__ import annotations

import pandas as pd

from fx_engine.strategies.base import BaseStrategy, Direction, StrategyResult, StrategySpec, compute_tp


class PriceActionStrategy(BaseStrategy):
    spec = StrategySpec(
        name="price_action",
        version="1.0",
        market_conditions="Objective break-of-structure: price closes beyond the most recent CONFIRMED swing "
                           "high/low (a fractal pivot, not a subjective 'obvious' level), in the direction of a "
                           "rising/falling sequence of prior swings.",
        pairs=["*"],
        timeframes=["H4", "D1"],
        indicators=["Fractal swing points (lookback=5)", "ATR14"],
        entry_conditions=[
            "At least 2 confirmed swing lows are rising (bull structure) or 2 confirmed swing highs are "
            "falling (bear structure)",
            "Close breaks beyond the most recent confirmed swing high (bull) / swing low (bear)",
        ],
        exit_conditions=["Stop-loss or take-profit hit"],
        stop_loss_method="Beyond the swing point that defines the structure, plus 0.2x ATR buffer",
        take_profit_method="Risk multiplied by MIN_RR_AFTER_COSTS",
        min_risk_reward=2.0,
        max_holding_bars=35,
        invalidation_conditions=["Price closes back below/above the broken swing point (failed break)"],
        excluded_regimes=["high_volatility"],
        required_data=["OHLC H4 or D1"],
        known_weaknesses=[
            "Swing points require `lookback` bars of confirmation AFTER the pivot, so entries are inherently "
            "a bit late relative to the actual turn",
            "Struggles in choppy markets that produce many small, contradictory swings",
        ],
    )

    def __init__(self, lookback: int = 5):
        self.lookback = lookback

    def generate(self, df: pd.DataFrame, pair: str, regime: str, features: pd.DataFrame | None = None) -> StrategyResult:
        if len(df) < 5 * self.lookback + 20:
            return self.neutral(df, regime, "insufficient history")
        if regime == "high_volatility":
            return self.neutral(df, regime, f"regime '{regime}' excluded for price_action")

        if features is None:
            from fx_engine.features import compute_feature_frame
            features = compute_feature_frame(df)

        a = features["atr14"].iloc[-1]
        c = df["close"].iloc[-1]
        ts = df.index[-1]
        if pd.isna(a):
            return self.neutral(df, regime, "indicators warming up")

        # only swings confirmed at least `lookback` bars ago are real (no lookahead)
        sw = features[["swing_high", "swing_low"]]
        confirmed = sw.iloc[:-self.lookback] if self.lookback > 0 else sw
        highs_aligned = df.loc[confirmed.index, "high"]
        lows_aligned = df.loc[confirmed.index, "low"]
        swing_highs = highs_aligned[confirmed["swing_high"]]
        swing_lows = lows_aligned[confirmed["swing_low"]]

        if len(swing_highs) >= 2 and len(swing_lows) >= 2:
            last_high = swing_highs.iloc[-1]
            last_two_lows = swing_lows.iloc[-2:]
            rising_lows = last_two_lows.iloc[-1] > last_two_lows.iloc[-2]

            last_low = swing_lows.iloc[-1]
            last_two_highs = swing_highs.iloc[-2:]
            falling_highs = last_two_highs.iloc[-1] < last_two_highs.iloc[-2]

            if rising_lows and c > last_high:
                sl = last_two_lows.iloc[-1] - 0.2 * a
                entry = c
                tp = compute_tp(entry, sl, Direction.BUY, self.spec.min_risk_reward)
                return StrategyResult(
                    strategy=self.spec.name, version=self.spec.version, direction=Direction.BUY,
                    confidence=0.55, entry_low=entry - 0.1 * a, entry_high=entry + 0.15 * a,
                    stop_loss=sl, take_profit=tp,
                    reason=f"Rising swing lows + close broke confirmed swing high at {last_high:.5f}",
                    regime=regime, timestamp=ts,
                )
            if falling_highs and c < last_low:
                sl = last_two_highs.iloc[-1] + 0.2 * a
                entry = c
                tp = compute_tp(entry, sl, Direction.SELL, self.spec.min_risk_reward)
                return StrategyResult(
                    strategy=self.spec.name, version=self.spec.version, direction=Direction.SELL,
                    confidence=0.55, entry_low=entry - 0.15 * a, entry_high=entry + 0.1 * a,
                    stop_loss=sl, take_profit=tp,
                    reason=f"Falling swing highs + close broke confirmed swing low at {last_low:.5f}",
                    regime=regime, timestamp=ts,
                )
        return self.neutral(df, regime, "no confirmed break-of-structure")
