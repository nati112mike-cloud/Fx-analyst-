"""Market regime classification.

Section 8 of the plan: classify the market BEFORE asking any strategy for
an opinion, and weight/gate strategies by regime rather than trusting every
strategy in every condition.
"""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from fx_engine import indicators as ind


REGIMES = [
    "strong_bull_trend", "strong_bear_trend", "weak_trend",
    "range", "high_volatility", "low_volatility", "breakout", "unclear",
]


@dataclass
class RegimeSnapshot:
    regime: str
    adx: float
    atr_percentile: float
    ema_slope: float
    confidence: float  # 0-1, how clearly this regime is established


def compute_regime_series(df: pd.DataFrame, feats: pd.DataFrame | None = None) -> pd.DataFrame:
    """Given an OHLC frame (and optionally a precomputed feature frame from
    fx_engine.features.compute_feature_frame, to avoid recomputing ADX/ATR/
    Bollinger from scratch), return regime labels aligned to the same index.
    Call this ONCE per full series -- every indicator here is causal, so
    computing it once and indexing by position is equivalent to, and far
    cheaper than, recomputing it on every growing truncation of df.
    """
    if feats is None:
        from fx_engine.features import compute_feature_frame
        feats = compute_feature_frame(df)

    a = feats["adx"]
    pdi = feats["plus_di"]
    mdi = feats["minus_di"]
    ap = feats["atr_percentile"]
    s = feats["ema_slope5"]
    width = feats["bb_width"]
    width_median = feats["bb_width_median100"]

    regime = pd.Series("unclear", index=df.index)
    confidence = pd.Series(0.3, index=df.index)

    warming_up = a.isna() | ap.isna()
    confidence = confidence.mask(warming_up, 0.0)

    is_high_vol = (~warming_up) & (ap >= 0.85)
    regime = regime.mask(is_high_vol, "high_volatility")
    confidence = confidence.mask(is_high_vol, ((ap - 0.85) / 0.15).clip(upper=1.0))

    is_low_vol = (~warming_up) & (~is_high_vol) & (ap <= 0.15)
    regime = regime.mask(is_low_vol, "low_volatility")
    confidence = confidence.mask(is_low_vol, ((0.15 - ap) / 0.15).clip(upper=1.0))

    remaining = ~warming_up & ~is_high_vol & ~is_low_vol
    is_bull = remaining & (a >= 30) & (pdi > mdi) & (s > 0)
    regime = regime.mask(is_bull, "strong_bull_trend")
    confidence = confidence.mask(is_bull, (a / 60).clip(upper=1.0))

    is_bear = remaining & (a >= 30) & (mdi > pdi) & (s < 0)
    regime = regime.mask(is_bear, "strong_bear_trend")
    confidence = confidence.mask(is_bear, (a / 60).clip(upper=1.0))

    is_weak_trend = remaining & (a >= 18) & (a < 30)
    regime = regime.mask(is_weak_trend, "weak_trend")
    confidence = confidence.mask(is_weak_trend, 0.5)

    is_range = remaining & (a < 18) & width.notna() & (width < width_median)
    regime = regime.mask(is_range, "range")
    confidence = confidence.mask(is_range, 0.6)

    regime = regime.mask(warming_up, "unclear")

    out = pd.DataFrame({
        "regime": regime, "confidence": confidence,
        "adx": a.fillna(0.0), "atr_percentile": ap.fillna(0.0),
    }, index=df.index)
    return out


def latest_regime(df: pd.DataFrame, feats: pd.DataFrame | None = None) -> RegimeSnapshot:
    series = compute_regime_series(df, feats)
    last = series.iloc[-1]
    if feats is not None:
        slope = float(feats["ema_slope5"].iloc[-1]) if not pd.isna(feats["ema_slope5"].iloc[-1]) else 0.0
    else:
        ema_fast = ind.ema(df["close"], 20)
        slope = float((ema_fast.diff(5) / df["close"]).iloc[-1]) if len(df) > 25 else 0.0
    return RegimeSnapshot(
        regime=str(last["regime"]),
        adx=float(last["adx"]),
        atr_percentile=float(last["atr_percentile"]) if not pd.isna(last["atr_percentile"]) else 0.0,
        ema_slope=slope,
        confidence=float(last["confidence"]),
    )


# Which regimes each strategy family is expected to work in. Used to GATE
# (not just weight) signals -- a strategy firing well outside its intended
# regime is suspicious, per section 8 ("if unclear -> NO TRADE").
STRATEGY_ALLOWED_REGIMES = {
    "trend_pullback": {"strong_bull_trend", "strong_bear_trend", "weak_trend"},
    "momentum": {"strong_bull_trend", "strong_bear_trend", "weak_trend", "breakout"},
    "breakout": {"low_volatility", "range", "breakout", "weak_trend"},
    "mean_reversion": {"range", "low_volatility"},
    "price_action": {"strong_bull_trend", "strong_bear_trend", "weak_trend", "range"},
    "volatility": {"low_volatility", "high_volatility", "breakout"},
    "multi_timeframe": set(REGIMES),  # confirmation-only overlay, regime-agnostic
}
