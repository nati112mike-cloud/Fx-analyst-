"""Vectorized technical indicators used by the strategy modules.

Pure pandas/numpy, no lookahead: every function only uses data up to and
including the current row for that row's value.
"""
from __future__ import annotations

import numpy as np
import pandas as pd


def ema(series: pd.Series, period: int) -> pd.Series:
    return series.ewm(span=period, adjust=False, min_periods=period).mean()


def sma(series: pd.Series, period: int) -> pd.Series:
    return series.rolling(period, min_periods=period).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    avg_gain = gain.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    avg_loss = loss.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    out = 100 - (100 / (1 + rs))
    return out.fillna(50)


def macd(series: pd.Series, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
    macd_line = ema(series, fast) - ema(series, slow)
    signal_line = macd_line.ewm(span=signal, adjust=False, min_periods=signal).mean()
    hist = macd_line - signal_line
    return pd.DataFrame({"macd": macd_line, "signal": signal_line, "hist": hist})


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([
        high - low,
        (high - prev_close).abs(),
        (low - prev_close).abs(),
    ], axis=1).max(axis=1)
    return tr


def atr(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.Series:
    tr = true_range(high, low, close)
    return tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(high: pd.Series, low: pd.Series, close: pd.Series, period: int = 14) -> pd.DataFrame:
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = np.where((up_move > down_move) & (up_move > 0), up_move, 0.0)
    minus_dm = np.where((down_move > up_move) & (down_move > 0), down_move, 0.0)
    plus_dm = pd.Series(plus_dm, index=high.index)
    minus_dm = pd.Series(minus_dm, index=high.index)

    tr = true_range(high, low, close)
    atr_ = tr.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()

    plus_di = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr_.replace(0, np.nan)
    minus_di = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr_.replace(0, np.nan)
    dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di).replace(0, np.nan)
    adx_ = dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    return pd.DataFrame({"adx": adx_.fillna(0), "plus_di": plus_di.fillna(0), "minus_di": minus_di.fillna(0)})


def bollinger_bands(series: pd.Series, period: int = 20, num_std: float = 2.0) -> pd.DataFrame:
    mid = sma(series, period)
    std = series.rolling(period, min_periods=period).std()
    upper = mid + num_std * std
    lower = mid - num_std * std
    width = (upper - lower) / mid.replace(0, np.nan)
    return pd.DataFrame({"mid": mid, "upper": upper, "lower": lower, "width": width})


def donchian_channel(high: pd.Series, low: pd.Series, period: int = 20) -> pd.DataFrame:
    # shift(1) so "upper"/"lower" reflect the channel BEFORE the current bar,
    # avoiding lookahead when checking for a breakout on the current bar.
    upper = high.rolling(period, min_periods=period).max().shift(1)
    lower = low.rolling(period, min_periods=period).min().shift(1)
    return pd.DataFrame({"upper": upper, "lower": lower})


def stochastic(high: pd.Series, low: pd.Series, close: pd.Series,
                k_period: int = 14, d_period: int = 3) -> pd.DataFrame:
    lowest_low = low.rolling(k_period, min_periods=k_period).min()
    highest_high = high.rolling(k_period, min_periods=k_period).max()
    k = 100 * (close - lowest_low) / (highest_high - lowest_low).replace(0, np.nan)
    d = k.rolling(d_period, min_periods=d_period).mean()
    return pd.DataFrame({"k": k, "d": d})


def swing_points(high: pd.Series, low: pd.Series, lookback: int = 5) -> pd.DataFrame:
    """Boolean series marking fractal swing highs/lows: a bar whose high/low
    is the extreme within +/- lookback bars. Uses centered windows so a
    point is only confirmed `lookback` bars after it occurs (no lookahead
    at signal time since callers only look at CONFIRMED swings, i.e. those
    at least `lookback` bars in the past).
    """
    is_swing_high = high == high.rolling(2 * lookback + 1, center=True, min_periods=2 * lookback + 1).max()
    is_swing_low = low == low.rolling(2 * lookback + 1, center=True, min_periods=2 * lookback + 1).min()
    return pd.DataFrame({"swing_high": is_swing_high.fillna(False), "swing_low": is_swing_low.fillna(False)})


def atr_percentile(atr_series: pd.Series, lookback: int = 100) -> pd.Series:
    """Rolling percentile rank of current ATR vs its own recent history --
    used for volatility-regime classification (0=very calm, 1=very volatile)."""
    def _pct_rank(x):
        return (x.rank(pct=True).iloc[-1]) if len(x.dropna()) > 1 else np.nan
    return atr_series.rolling(lookback, min_periods=max(10, lookback // 4)).apply(_pct_rank, raw=False)
