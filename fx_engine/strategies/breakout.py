from __future__ import annotations

import pandas as pd

from fx_engine.strategies.base import BaseStrategy, Direction, StrategyResult, StrategySpec, compute_tp


class BreakoutStrategy(BaseStrategy):
    spec = StrategySpec(
        name="breakout",
        version="1.0",
        market_conditions="Price closes outside a 20-bar Donchian channel (a defined range) with volatility "
                           "(ATR) expanding, consistent with a genuine breakout rather than a range-bound spike.",
        pairs=["*"],
        timeframes=["H1", "H4"],
        indicators=["Donchian(20)", "ATR14", "ATR percentile(100)"],
        entry_conditions=[
            "Close breaks above the prior 20-bar Donchian upper band (bull) or below the lower band (bear)",
            "ATR percentile has risen at least 15 percentile points over the last 5 bars (volatility expansion, "
            "not a low-volatility fake-out)",
        ],
        exit_conditions=["Stop-loss or take-profit hit", "Price closes back inside the pre-breakout channel"],
        stop_loss_method="Opposite side of the breakout bar's range, or 1x ATR, whichever is tighter",
        take_profit_method="Measured move: channel height projected from the breakout point, floor of "
                            "MIN_RR_AFTER_COSTS",
        min_risk_reward=1.8,
        max_holding_bars=30,
        invalidation_conditions=["Close back inside the channel within 3 bars of the breakout (failed auction)"],
        excluded_regimes=["strong_bull_trend", "strong_bear_trend"],  # channel likely already broken/extended
        required_data=["OHLC H1 or H4"],
        known_weaknesses=[
            "False breakouts ('fakeouts') are common, especially outside the New York session",
            "Whipsaw risk right at market open / thin liquidity windows",
        ],
    )

    def generate(self, df: pd.DataFrame, pair: str, regime: str, features: pd.DataFrame | None = None) -> StrategyResult:
        if len(df) < 130:
            return self.neutral(df, regime, "insufficient history")
        if regime not in {"low_volatility", "range", "breakout", "weak_trend"}:
            return self.neutral(df, regime, f"regime '{regime}' excluded for breakout")

        if features is None:
            from fx_engine.features import compute_feature_frame
            features = compute_feature_frame(df)

        atr_pct = features["atr_percentile"]
        c = df["close"].iloc[-1]
        h = df["high"].iloc[-1]
        l = df["low"].iloc[-1]
        upper, lower = features["donchian_upper"].iloc[-1], features["donchian_lower"].iloc[-1]
        a = features["atr14"].iloc[-1]
        ts = df.index[-1]

        if pd.isna(upper) or pd.isna(lower) or pd.isna(a) or len(atr_pct.dropna()) < 6:
            return self.neutral(df, regime, "indicators warming up")

        vol_expanding = atr_pct.iloc[-1] - atr_pct.iloc[-6] >= 0.15 if not pd.isna(atr_pct.iloc[-6]) else False

        if c > upper and vol_expanding:
            sl = min(l, upper) - 0.1 * a
            range_height = upper - lower
            entry = c
            tp_measured = entry + range_height
            tp_floor = compute_tp(entry, sl, Direction.BUY, self.spec.min_risk_reward)
            tp = max(tp_measured, tp_floor)
            return StrategyResult(
                strategy=self.spec.name, version=self.spec.version, direction=Direction.BUY,
                confidence=0.5, entry_low=entry - 0.1 * a, entry_high=entry + 0.15 * a,
                stop_loss=sl, take_profit=tp,
                reason=f"Close broke above 20-bar Donchian high ({upper:.5f}) with expanding ATR percentile",
                regime=regime, timestamp=ts,
            )
        if c < lower and vol_expanding:
            sl = max(h, lower) + 0.1 * a
            range_height = upper - lower
            entry = c
            tp_measured = entry - range_height
            tp_floor = compute_tp(entry, sl, Direction.SELL, self.spec.min_risk_reward)
            tp = min(tp_measured, tp_floor)
            return StrategyResult(
                strategy=self.spec.name, version=self.spec.version, direction=Direction.SELL,
                confidence=0.5, entry_low=entry - 0.15 * a, entry_high=entry + 0.1 * a,
                stop_loss=sl, take_profit=tp,
                reason=f"Close broke below 20-bar Donchian low ({lower:.5f}) with expanding ATR percentile",
                regime=regime, timestamp=ts,
            )
        return self.neutral(df, regime, "no confirmed Donchian breakout with volatility expansion")
