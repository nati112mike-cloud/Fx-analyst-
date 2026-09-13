from __future__ import annotations

import pandas as pd

from fx_engine.strategies.base import BaseStrategy, Direction, StrategyResult, StrategySpec, compute_tp


class MomentumStrategy(BaseStrategy):
    spec = StrategySpec(
        name="momentum",
        version="1.0",
        market_conditions="Building directional momentum: MACD histogram crosses zero in the direction of RSI "
                           "confirmation, entered early in the move rather than at exhaustion.",
        pairs=["*"],
        timeframes=["H1", "H4"],
        indicators=["MACD(12,26,9)", "RSI14", "ATR14"],
        entry_conditions=[
            "MACD histogram crosses from negative to positive (bull) or positive to negative (bear) on the "
            "most recently closed bar",
            "RSI14 confirms direction: >50 and <70 for bull, <50 and >30 for bear (avoids buying/selling "
            "into an already-exhausted move)",
        ],
        exit_conditions=["Stop-loss or take-profit hit", "MACD histogram crosses back against the trade"],
        stop_loss_method="1.5x ATR from entry",
        take_profit_method="Risk multiplied by MIN_RR_AFTER_COSTS",
        min_risk_reward=1.8,
        max_holding_bars=25,
        invalidation_conditions=["MACD histogram re-crosses zero against the trade before target hit"],
        excluded_regimes=["range"],
        required_data=["OHLC H1 or H4"],
        known_weaknesses=[
            "MACD is a lagging derivative of price -- signals confirm after part of the move already happened",
            "Prone to false crosses in choppy weak-trend conditions",
        ],
    )

    def generate(self, df: pd.DataFrame, pair: str, regime: str, features: pd.DataFrame | None = None) -> StrategyResult:
        if len(df) < 60:
            return self.neutral(df, regime, "insufficient history")
        if regime not in {"strong_bull_trend", "strong_bear_trend", "weak_trend", "breakout"}:
            return self.neutral(df, regime, f"regime '{regime}' excluded for momentum")

        if features is None:
            from fx_engine.features import compute_feature_frame
            features = compute_feature_frame(df)

        hist = features["macd_hist"]
        h_now, h_prev = hist.iloc[-1], hist.iloc[-2]
        r = features["rsi14"].iloc[-1]
        a = features["atr14"].iloc[-1]
        c = df["close"].iloc[-1]
        ts = df.index[-1]

        if pd.isna(h_now) or pd.isna(h_prev) or pd.isna(a):
            return self.neutral(df, regime, "indicators warming up")

        crossed_up = h_prev <= 0 < h_now
        crossed_down = h_prev >= 0 > h_now

        if crossed_up and 50 < r < 70:
            sl = c - 1.5 * a
            tp = compute_tp(c, sl, Direction.BUY, self.spec.min_risk_reward)
            return StrategyResult(
                strategy=self.spec.name, version=self.spec.version, direction=Direction.BUY,
                confidence=0.55, entry_low=c - 0.1 * a, entry_high=c + 0.1 * a,
                stop_loss=sl, take_profit=tp,
                reason=f"MACD histogram crossed up, RSI={r:.0f} confirms bullish momentum",
                regime=regime, timestamp=ts,
            )
        if crossed_down and 30 < r < 50:
            sl = c + 1.5 * a
            tp = compute_tp(c, sl, Direction.SELL, self.spec.min_risk_reward)
            return StrategyResult(
                strategy=self.spec.name, version=self.spec.version, direction=Direction.SELL,
                confidence=0.55, entry_low=c - 0.1 * a, entry_high=c + 0.1 * a,
                stop_loss=sl, take_profit=tp,
                reason=f"MACD histogram crossed down, RSI={r:.0f} confirms bearish momentum",
                regime=regime, timestamp=ts,
            )
        return self.neutral(df, regime, "no fresh MACD/RSI momentum alignment")
