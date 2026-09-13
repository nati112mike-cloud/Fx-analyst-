"""Historical/live OHLC data providers.

Three implementations:

- SyntheticDataProvider: deterministic, seeded, regime-switching random-walk
  data. Used as the default in sandboxed/offline environments so the rest of
  the pipeline (indicators, strategies, backtest, walk-forward, ensemble,
  Telegram formatting) can be built and proven correct without real market
  access. It is clearly NOT real market data -- never use it to justify a
  real trading decision. See docs/LIMITATIONS.md.

- YahooFinanceProvider: real historical OHLC via Yahoo's public chart
  endpoint. Free, no key, works from any machine with normal internet
  access. Good enough for strategy research and backtesting, but Yahoo's
  quotes are NOT Exness's quotes -- spread must still come from
  fx_engine.costs.SpreadModel, not from this provider.

- MT5DataProvider: pulls Exness's own historical and live rates through a
  running MetaTrader5 terminal. This is the only source whose prices match
  what you would actually trade. See fx_engine/broker/exness_mt5.py and
  docs/EXNESS_INTEGRATION.md for the setup this requires (Windows or Wine,
  MT5 terminal installed and logged into an Exness account).
"""
from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from fx_engine.data.models import Timeframe


class HistoricalDataProvider(ABC):
    name: str = "base"

    @abstractmethod
    def get_ohlc(self, pair: str, timeframe: Timeframe, start: datetime, end: datetime) -> pd.DataFrame:
        """Return a DataFrame indexed by UTC datetime with columns
        open, high, low, close, volume. Must raise, not fabricate data,
        if the request cannot be satisfied.
        """
        raise NotImplementedError


class SyntheticDataProvider(HistoricalDataProvider):
    """Regime-switching synthetic OHLC generator for pipeline testing.

    Deterministic given (pair, timeframe, start, end): same seed always
    produces the same series, so backtests are reproducible.
    """

    name = "synthetic"

    # Rough realistic starting mid-price and daily-vol-in-pips per pair,
    # used only to make the synthetic series look plausible.
    _BASE_PRICE = {
        "EURUSD": 1.0850, "GBPUSD": 1.2650, "USDJPY": 149.50, "USDCHF": 0.8800,
        "AUDUSD": 0.6550, "USDCAD": 1.3650, "NZDUSD": 0.6050, "EURJPY": 162.20,
        "GBPJPY": 189.00,
    }
    _DAILY_VOL_PIPS = {
        "EURUSD": 70, "GBPUSD": 90, "USDJPY": 80, "USDCHF": 65,
        "AUDUSD": 75, "USDCAD": 70, "NZDUSD": 80, "EURJPY": 110, "GBPJPY": 150,
    }

    def _seed(self, pair: str, timeframe: Timeframe, start: datetime, end: datetime) -> int:
        key = f"{pair}|{timeframe.value}|{start.isoformat()}|{end.isoformat()}"
        return int(hashlib.sha256(key.encode()).hexdigest()[:8], 16)

    def get_ohlc(self, pair: str, timeframe: Timeframe, start: datetime, end: datetime) -> pd.DataFrame:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        rng = np.random.default_rng(self._seed(pair, timeframe, start, end))
        idx = pd.date_range(start, end, freq=timeframe.pandas_rule, tz="UTC")
        idx = idx[(idx.dayofweek < 5)]  # drop weekends, forex convention
        n = len(idx)
        if n < 10:
            raise ValueError("Requested synthetic range too short")

        pip = 0.01 if pair in {"USDJPY", "EURJPY", "GBPJPY"} else 0.0001
        base_price = self._BASE_PRICE.get(pair, 1.0)
        bars_per_day = max(1, round(1440 / timeframe.minutes))
        daily_vol = self._DAILY_VOL_PIPS.get(pair, 80) * pip
        bar_vol = daily_vol / np.sqrt(bars_per_day)

        # Regime-switching volatility & drift: alternate calm/trend/volatile
        # blocks so the regime detector and strategies have something real
        # to distinguish between.
        regime_len = max(20, n // 15)
        n_blocks = n // regime_len + 1
        regimes = rng.choice(["calm_range", "trend_up", "trend_down", "volatile"],
                              size=n_blocks, p=[0.40, 0.22, 0.22, 0.16])

        drift = np.zeros(n)
        vol = np.zeros(n)
        for b in range(n_blocks):
            s, e = b * regime_len, min((b + 1) * regime_len, n)
            if s >= n:
                break
            r = regimes[b]
            if r == "calm_range":
                drift[s:e] = 0.0
                vol[s:e] = bar_vol * 0.6
            elif r == "trend_up":
                drift[s:e] = bar_vol * 0.18
                vol[s:e] = bar_vol * 0.9
            elif r == "trend_down":
                drift[s:e] = -bar_vol * 0.18
                vol[s:e] = bar_vol * 0.9
            else:  # volatile
                drift[s:e] = 0.0
                vol[s:e] = bar_vol * 2.0

        returns = rng.normal(drift, vol)
        mid_close = base_price * np.exp(np.cumsum(returns) / base_price)  # additive-ish in price space
        # simpler & numerically safer: additive random walk in price units
        mid_close = base_price + np.cumsum(returns)

        opens = np.empty(n)
        opens[0] = base_price
        opens[1:] = mid_close[:-1]
        closes = mid_close

        intrabar_range = np.abs(rng.normal(0, vol * 1.3, n)) + vol * 0.4
        highs = np.maximum(opens, closes) + intrabar_range * rng.uniform(0.2, 1.0, n)
        lows = np.minimum(opens, closes) - intrabar_range * rng.uniform(0.2, 1.0, n)
        volumes = rng.integers(500, 5000, n)

        df = pd.DataFrame(
            {"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes},
            index=idx,
        )
        df.index.name = "time"
        return df


class YahooFinanceProvider(HistoricalDataProvider):
    """Real historical OHLC from Yahoo Finance's public chart endpoint.

    NOTE: requires normal outbound internet access to
    query1.finance.yahoo.com. This will fail with a clear error (not
    fabricated data) in network-restricted environments -- run it from
    your own machine/VPS.
    """

    name = "yahoo"
    _INTERVAL = {Timeframe.M15: "15m", Timeframe.H1: "60m", Timeframe.H4: "60m", Timeframe.D1: "1d"}

    def _symbol(self, pair: str) -> str:
        return f"{pair}=X"

    def get_ohlc(self, pair: str, timeframe: Timeframe, start: datetime, end: datetime) -> pd.DataFrame:
        import requests

        interval = self._INTERVAL[timeframe]
        symbol = self._symbol(pair)
        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {
            "interval": interval,
            "period1": int(start.replace(tzinfo=timezone.utc).timestamp()),
            "period2": int(end.replace(tzinfo=timezone.utc).timestamp()),
        }
        resp = requests.get(url, params=params, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
        resp.raise_for_status()
        payload = resp.json()
        result = payload.get("chart", {}).get("result")
        if not result:
            raise ValueError(f"No data returned for {symbol}: {payload.get('chart', {}).get('error')}")
        r = result[0]
        ts = r["timestamp"]
        q = r["indicators"]["quote"][0]
        df = pd.DataFrame({
            "open": q["open"], "high": q["high"], "low": q["low"],
            "close": q["close"], "volume": q.get("volume", [0] * len(ts)),
        }, index=pd.to_datetime(ts, unit="s", utc=True))
        df.index.name = "time"
        df = df.dropna(how="any")

        if timeframe == Timeframe.H4:
            from fx_engine.data.models import resample_ohlc
            df = resample_ohlc(df, "4h")
        return df


def get_provider(name: str) -> HistoricalDataProvider:
    if name == "synthetic":
        return SyntheticDataProvider()
    if name == "yahoo":
        return YahooFinanceProvider()
    if name == "mt5":
        from fx_engine.broker.exness_mt5 import MT5DataProvider
        return MT5DataProvider()
    raise ValueError(f"Unknown data provider: {name}")
