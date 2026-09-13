"""Precompute every indicator used by any strategy, once, vectorized.

All indicators in fx_engine.indicators are causal (rolling/ewm windows that
only look backward). That means computing them once on a full OHLC series
and then reading the value at row i is mathematically identical to
recomputing them from scratch on df.iloc[:i+1] for every i -- but is O(n)
instead of O(n^2). This module is the fix for that: strategies read
precomputed columns instead of calling fx_engine.indicators themselves on
a growing window.
"""
from __future__ import annotations

import pandas as pd

from fx_engine import indicators as ind


def compute_feature_frame(df: pd.DataFrame) -> pd.DataFrame:
    f = pd.DataFrame(index=df.index)
    f["ema20"] = ind.ema(df["close"], 20)
    f["ema50"] = ind.ema(df["close"], 50)
    f["ema200"] = ind.ema(df["close"], 200)
    f["rsi14"] = ind.rsi(df["close"], 14)

    macd_df = ind.macd(df["close"])
    f["macd"] = macd_df["macd"]
    f["macd_signal"] = macd_df["signal"]
    f["macd_hist"] = macd_df["hist"]

    f["atr14"] = ind.atr(df["high"], df["low"], df["close"], 14)
    f["atr_percentile"] = ind.atr_percentile(f["atr14"], 100)

    adx_df = ind.adx(df["high"], df["low"], df["close"], 14)
    f["adx"] = adx_df["adx"]
    f["plus_di"] = adx_df["plus_di"]
    f["minus_di"] = adx_df["minus_di"]

    bb = ind.bollinger_bands(df["close"], 20, 2.0)
    f["bb_upper"] = bb["upper"]
    f["bb_mid"] = bb["mid"]
    f["bb_lower"] = bb["lower"]
    f["bb_width"] = bb["width"]
    f["bb_width_median100"] = f["bb_width"].rolling(100, min_periods=20).median()
    f["bb_width_pctrank"] = f["bb_width"].rolling(100, min_periods=30).apply(
        lambda x: x.rank(pct=True).iloc[-1] if len(x.dropna()) > 1 else float("nan"), raw=False,
    )

    donchian = ind.donchian_channel(df["high"], df["low"], 20)
    f["donchian_upper"] = donchian["upper"]
    f["donchian_lower"] = donchian["lower"]

    sw = ind.swing_points(df["high"], df["low"], 5)
    f["swing_high"] = sw["swing_high"]
    f["swing_low"] = sw["swing_low"]

    ema_fast_regime = ind.ema(df["close"], 20)
    f["ema_slope5"] = ema_fast_regime.diff(5) / df["close"]

    # Multi-horizon EMA pairs used as a fast proxy for "higher timeframe
    # trend" without repeatedly resampling per bar (see strategies/
    # multi_timeframe.py). Multiplier ~3x and ~9x approximate one and two
    # steps up the typical H1->H4->D1 chain without needing separate
    # resampled series recomputed on every truncated window.
    f["mtf_base_fast"], f["mtf_base_slow"] = f["ema20"], f["ema50"]
    f["mtf_mid_fast"] = ind.ema(df["close"], 60)
    f["mtf_mid_slow"] = ind.ema(df["close"], 150)
    f["mtf_high_fast"] = ind.ema(df["close"], 180)
    f["mtf_high_slow"] = ind.ema(df["close"], 450)

    return f
