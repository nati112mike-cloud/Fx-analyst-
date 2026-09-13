from __future__ import annotations

import pandas as pd

from fx_engine.strategies.base import BaseStrategy, Direction, StrategyResult, StrategySpec, compute_tp


class MeanReversionStrategy(BaseStrategy):
    spec = StrategySpec(
        name="mean_reversion",
        version="1.0",
        market_conditions="Range-bound / low-volatility market where price has stretched to a statistical "
                           "extreme (outside Bollinger Bands, RSI extreme) and is expected to revert toward the "
                           "mean rather than continue -- explicitly gated OUT of trending regimes.",
        pairs=["*"],
        timeframes=["H1", "H4"],
        indicators=["BollingerBands(20,2)", "RSI14", "ATR14"],
        entry_conditions=[
            "Regime is range or low_volatility (never trend regimes)",
            "Close is outside the Bollinger Band (below lower for BUY, above upper for SELL)",
            "RSI14 is at an extreme: <30 for BUY, >70 for SELL",
            "Most recent bar shows a reversal wick back inside the band",
        ],
        exit_conditions=["Stop-loss or take-profit hit", "Price reaches the Bollinger mid-band (primary target)"],
        stop_loss_method="Beyond the extreme bar's high/low plus 0.3x ATR",
        take_profit_method="Bollinger mid-band, floor of MIN_RR_AFTER_COSTS",
        min_risk_reward=1.5,
        max_holding_bars=20,
        invalidation_conditions=["Close beyond the stop before reversion confirms (regime was misread as trend)"],
        excluded_regimes=["strong_bull_trend", "strong_bear_trend", "weak_trend", "breakout", "high_volatility"],
        required_data=["OHLC H1 or H4"],
        known_weaknesses=[
            "Dangerous in a market that is actually trending (catching a falling knife) -- regime gate is the "
            "only real protection here",
            "Win rate can look high while a single trend day wipes out many small wins",
        ],
    )

    def generate(self, df: pd.DataFrame, pair: str, regime: str, features: pd.DataFrame | None = None) -> StrategyResult:
        if len(df) < 30:
            return self.neutral(df, regime, "insufficient history")
        if regime not in {"range", "low_volatility"}:
            return self.neutral(df, regime, f"regime '{regime}' excluded for mean_reversion")

        if features is None:
            from fx_engine.features import compute_feature_frame
            features = compute_feature_frame(df)

        c = df["close"].iloc[-1]
        o = df["open"].iloc[-1]
        upper, mid, lower = features["bb_upper"].iloc[-1], features["bb_mid"].iloc[-1], features["bb_lower"].iloc[-1]
        r = features["rsi14"].iloc[-1]
        a = features["atr14"].iloc[-1]
        ts = df.index[-1]

        if pd.isna(upper) or pd.isna(a):
            return self.neutral(df, regime, "indicators warming up")

        if c <= lower and r < 30 and c > o:
            sl = min(df["low"].iloc[-3:]) - 0.3 * a
            entry = c
            tp_target = mid
            tp_floor = compute_tp(entry, sl, Direction.BUY, self.spec.min_risk_reward)
            tp = min(tp_target, tp_floor) if tp_target > entry else tp_floor
            tp = max(tp, tp_floor) if tp_target <= entry else tp_target
            return StrategyResult(
                strategy=self.spec.name, version=self.spec.version, direction=Direction.BUY,
                confidence=0.5, entry_low=entry - 0.1 * a, entry_high=entry + 0.1 * a,
                stop_loss=sl, take_profit=tp,
                reason=f"Price below lower Bollinger Band, RSI={r:.0f} oversold, reversal candle forming",
                regime=regime, timestamp=ts,
            )
        if c >= upper and r > 70 and c < o:
            sl = max(df["high"].iloc[-3:]) + 0.3 * a
            entry = c
            tp_target = mid
            tp_floor = compute_tp(entry, sl, Direction.SELL, self.spec.min_risk_reward)
            tp = tp_target if tp_target < entry else tp_floor
            return StrategyResult(
                strategy=self.spec.name, version=self.spec.version, direction=Direction.SELL,
                confidence=0.5, entry_low=entry - 0.1 * a, entry_high=entry + 0.1 * a,
                stop_loss=sl, take_profit=tp,
                reason=f"Price above upper Bollinger Band, RSI={r:.0f} overbought, reversal candle forming",
                regime=regime, timestamp=ts,
            )
        return self.neutral(df, regime, "no Bollinger/RSI extreme with reversal confirmation")
