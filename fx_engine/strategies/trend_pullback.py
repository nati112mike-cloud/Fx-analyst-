from __future__ import annotations

import pandas as pd

from fx_engine.strategies.base import BaseStrategy, Direction, StrategyResult, StrategySpec, compute_tp


class TrendPullbackStrategy(BaseStrategy):
    spec = StrategySpec(
        name="trend_pullback",
        version="1.0",
        market_conditions="Established trend (EMA50 vs EMA200 aligned) with price pulling back to the EMA50 "
                           "before continuation -- the classic 'buy the dip in an uptrend / sell the rip in a "
                           "downtrend' setup, entered only after a reversal candle confirms the pullback is over.",
        pairs=["*"],
        timeframes=["H4", "D1"],
        indicators=["EMA50", "EMA200", "ATR14", "RSI14"],
        entry_conditions=[
            "EMA50 vs EMA200 defines trend direction (50>200 bull, 50<200 bear)",
            "Price within 0.6x ATR of EMA50 (the pullback zone)",
            "Most recent closed candle is a reversal candle in the trend direction "
            "(bullish candle for uptrend, bearish for downtrend)",
            "RSI14 turning back toward 50 from the pullback extreme (not already exhausted >70 / <30)",
        ],
        exit_conditions=["Stop-loss or take-profit hit", "Max holding bars elapsed", "Trend EMA cross reverses"],
        stop_loss_method="1.2x ATR beyond the pullback low/high",
        take_profit_method="Risk multiplied by MIN_RR_AFTER_COSTS (configurable, default >=1.5)",
        min_risk_reward=2.0,
        max_holding_bars=40,
        invalidation_conditions=["EMA50/EMA200 cross against the trade before entry triggers"],
        excluded_regimes=["range", "high_volatility", "unclear"],
        required_data=["OHLC H4 or D1"],
        known_weaknesses=[
            "Whipsaws in choppy/weak trends where EMA50 is repeatedly retested without real momentum",
            "Lagging: EMA-based trend definition confirms late relative to the actual turn",
        ],
    )

    def generate(self, df: pd.DataFrame, pair: str, regime: str, features: pd.DataFrame | None = None) -> StrategyResult:
        if len(df) < 210:
            return self.neutral(df, regime, "insufficient history for EMA200")
        if regime not in {"strong_bull_trend", "strong_bear_trend", "weak_trend"}:
            return self.neutral(df, regime, f"regime '{regime}' excluded for trend_pullback")

        if features is None:
            from fx_engine.features import compute_feature_frame
            features = compute_feature_frame(df)

        c = df["close"].iloc[-1]
        o = df["open"].iloc[-1]
        e50, e200, a = features["ema50"].iloc[-1], features["ema200"].iloc[-1], features["atr14"].iloc[-1]
        r = features["rsi14"].iloc[-1]
        ts = df.index[-1]

        if pd.isna(e50) or pd.isna(e200) or pd.isna(a):
            return self.neutral(df, regime, "indicators warming up")

        bullish_trend = e50 > e200
        near_ema50 = abs(c - e50) <= 0.6 * a

        if not near_ema50:
            return self.neutral(df, regime, "price not in EMA50 pullback zone")

        if bullish_trend:
            reversal_candle = c > o
            rsi_ok = 35 < r < 65
            if reversal_candle and rsi_ok:
                sl = min(df["low"].iloc[-3:]) - 0.2 * a
                entry = c
                tp = compute_tp(entry, sl, Direction.BUY, self.spec.min_risk_reward)
                return StrategyResult(
                    strategy=self.spec.name, version=self.spec.version, direction=Direction.BUY,
                    confidence=0.6, entry_low=entry - 0.1 * a, entry_high=entry + 0.1 * a,
                    stop_loss=sl, take_profit=tp,
                    reason=f"Uptrend (EMA50>EMA200), pullback to EMA50, bullish reversal candle, RSI={r:.0f}",
                    regime=regime, timestamp=ts,
                )
        else:
            reversal_candle = c < o
            rsi_ok = 35 < r < 65
            if reversal_candle and rsi_ok:
                sl = max(df["high"].iloc[-3:]) + 0.2 * a
                entry = c
                tp = compute_tp(entry, sl, Direction.SELL, self.spec.min_risk_reward)
                return StrategyResult(
                    strategy=self.spec.name, version=self.spec.version, direction=Direction.SELL,
                    confidence=0.6, entry_low=entry - 0.1 * a, entry_high=entry + 0.1 * a,
                    stop_loss=sl, take_profit=tp,
                    reason=f"Downtrend (EMA50<EMA200), pullback to EMA50, bearish reversal candle, RSI={r:.0f}",
                    regime=regime, timestamp=ts,
                )

        return self.neutral(df, regime, "pullback present but no confirming reversal candle yet")
