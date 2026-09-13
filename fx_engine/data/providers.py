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
import logging
import time
from abc import ABC, abstractmethod
from datetime import datetime, timedelta, timezone

import numpy as np
import pandas as pd

from fx_engine.data.models import Timeframe

logger = logging.getLogger("fx_engine.data.providers")


class _RangeRejected(Exception):
    """Internal signal: Yahoo rejected a request's date range outright
    (HTTP 400/422) rather than failing transiently. Caught by
    YahooFinanceProvider._fetch_with_bisection, never meant to escape it."""

    def __init__(self, status_code: int):
        self.status_code = status_code
        super().__init__(f"Yahoo rejected the requested range (HTTP {status_code})")


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
    query1.finance.yahoo.com. This will fail with a clear ConnectionError
    (never fabricated data) in network-restricted environments -- this
    codebase's own dev sandbox included, see docs/LIMITATIONS.md -- run it
    from a machine/VPS with normal internet access.

    Yahoo enforces a maximum lookback window per intraday interval that is
    both undocumented AND apparently tightening over time: an initial
    728-day guess for 60-minute bars (a commonly-cited figure) was
    confirmed WRONG against the live endpoint -- Yahoo returned a 422
    Unprocessable Entity for that exact range during real testing (see
    docs/PERFORMANCE.md). A 422/400 is not a transient failure (retrying
    the identical request four times just fails four times, as it did
    here), so `_fetch_chunk` no longer retries it -- instead
    `get_ohlc` catches `_RangeRejected` and bisects the offending range
    in half, recursively, until each half is accepted. This makes the
    provider self-correcting against whatever Yahoo's real current limit
    is, rather than hard-coding a guess that can silently go stale again.
    Daily bars have no such practical limit for the ranges this project uses.
    """

    name = "yahoo"
    _INTERVAL = {Timeframe.M15: "15m", Timeframe.H1: "60m", Timeframe.H4: "60m", Timeframe.D1: "1d"}
    # Starting guesses only -- kept conservative so the common case needs no
    # bisection at all; _RangeRejected handles it self-correcting either way.
    _MAX_LOOKBACK_DAYS = {"15m": 58, "60m": 360, "1d": 36500}
    _RETRY_ATTEMPTS = 4
    _RETRY_BACKOFF_SECONDS = 2.0
    _MAX_BISECTION_DEPTH = 8

    def _symbol(self, pair: str) -> str:
        return f"{pair}=X"

    def _fetch_chunk(self, symbol: str, interval: str, start: datetime, end: datetime) -> pd.DataFrame:
        import requests

        url = f"https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
        params = {"interval": interval, "period1": int(start.timestamp()), "period2": int(end.timestamp())}

        payload = None
        for attempt in range(1, self._RETRY_ATTEMPTS + 1):
            try:
                resp = requests.get(url, params=params, timeout=20, headers={"User-Agent": "Mozilla/5.0"})
                if resp.status_code in (400, 422):
                    # Not transient -- Yahoo is rejecting this specific range
                    # (too long for this interval). Retrying the same request
                    # would just fail the same way every time; the caller
                    # bisects the range instead. See class docstring.
                    raise _RangeRejected(resp.status_code)
                resp.raise_for_status()  # 429/5xx -> HTTPError, retried below like any other transient failure
                payload = resp.json()
                break
            except _RangeRejected:
                raise
            except requests.exceptions.RequestException as exc:
                if attempt == self._RETRY_ATTEMPTS:
                    raise ConnectionError(
                        f"Could not reach Yahoo Finance for {symbol} after {self._RETRY_ATTEMPTS} attempts "
                        f"({exc}). If you're running this from a sandboxed/restricted network (this "
                        "project's own dev environment included), outbound access to "
                        "query1.finance.yahoo.com may be blocked at the network policy level, not by "
                        "anything wrong with this code -- run it from a machine with normal internet "
                        "access instead. See docs/LIMITATIONS.md."
                    ) from exc
                time.sleep(self._RETRY_BACKOFF_SECONDS * attempt)

        result = payload.get("chart", {}).get("result")
        if not result:
            err = payload.get("chart", {}).get("error")
            raise ValueError(f"No data returned for {symbol} ({start.date()}..{end.date()}): {err}")
        r = result[0]
        ts = r.get("timestamp")
        if not ts:
            return pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        q = r["indicators"]["quote"][0]
        df = pd.DataFrame({
            "open": q["open"], "high": q["high"], "low": q["low"],
            "close": q["close"], "volume": q.get("volume", [0] * len(ts)),
        }, index=pd.to_datetime(ts, unit="s", utc=True))
        df.index.name = "time"
        return df.dropna(how="any")

    def _fetch_with_bisection(self, symbol: str, interval: str, start: datetime, end: datetime,
                               depth: int = 0) -> list[pd.DataFrame]:
        try:
            return [self._fetch_chunk(symbol, interval, start, end)]
        except _RangeRejected:
            if depth >= self._MAX_BISECTION_DEPTH or (end - start) <= timedelta(hours=4):
                raise ValueError(
                    f"Yahoo rejected {symbol} {interval} even for a short range ({start}..{end}) -- "
                    "this isn't a range-length problem, something else is wrong (bad symbol or interval)."
                )
            mid = start + (end - start) / 2
            logger.info("Yahoo rejected %s %s %s..%s as too long -- bisecting at %s", symbol, interval, start, end, mid)
            return (self._fetch_with_bisection(symbol, interval, start, mid, depth + 1)
                    + self._fetch_with_bisection(symbol, interval, mid, end, depth + 1))

    def get_ohlc(self, pair: str, timeframe: Timeframe, start: datetime, end: datetime) -> pd.DataFrame:
        if start.tzinfo is None:
            start = start.replace(tzinfo=timezone.utc)
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        if start >= end:
            raise ValueError(f"start ({start}) must be before end ({end})")

        interval = self._INTERVAL[timeframe]
        symbol = self._symbol(pair)
        max_days = self._MAX_LOOKBACK_DAYS[interval]

        chunks: list[pd.DataFrame] = []
        chunk_start = start
        while chunk_start < end:
            chunk_end = min(end, chunk_start + timedelta(days=max_days))
            chunks.extend(self._fetch_with_bisection(symbol, interval, chunk_start, chunk_end))
            chunk_start = chunk_end
            if chunk_start < end:
                time.sleep(0.4)  # polite pacing across multiple requests to an unofficial endpoint

        df = pd.concat(chunks) if chunks else pd.DataFrame(columns=["open", "high", "low", "close", "volume"])
        df = df[~df.index.duplicated(keep="first")].sort_index()
        if df.empty:
            raise ValueError(f"Yahoo returned no usable rows for {symbol} {timeframe.value} "
                              f"{start.date()}..{end.date()} -- check the symbol and date range")

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
