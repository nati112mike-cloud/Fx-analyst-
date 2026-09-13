"""Shared data types: timeframes and OHLC frame conventions.

Historical/live price series are represented as pandas DataFrames with a
UTC DatetimeIndex and columns: open, high, low, close, volume.
All prices in a raw OHLC frame are treated as MID prices; bid/ask are
derived explicitly by fx_engine.costs.SpreadModel wherever execution
matters (see docs/BACKTESTING.md - spread is never an afterthought).
"""
from __future__ import annotations

from enum import Enum


class Timeframe(str, Enum):
    M15 = "M15"
    H1 = "H1"
    H4 = "H4"
    D1 = "D1"

    @property
    def pandas_rule(self) -> str:
        return {
            Timeframe.M15: "15min",
            Timeframe.H1: "1h",
            Timeframe.H4: "4h",
            Timeframe.D1: "1D",
        }[self]

    @property
    def minutes(self) -> int:
        return {
            Timeframe.M15: 15,
            Timeframe.H1: 60,
            Timeframe.H4: 240,
            Timeframe.D1: 1440,
        }[self]


REQUIRED_COLUMNS = ["open", "high", "low", "close", "volume"]


def resample_ohlc(df, rule: str):
    """Resample a finer OHLC frame up to a coarser timeframe (e.g. H1 -> H4)."""
    agg = {"open": "first", "high": "max", "low": "min", "close": "last", "volume": "sum"}
    out = df.resample(rule).agg(agg).dropna(how="any")
    return out
