"""Exness integration via a local MetaTrader 5 terminal.

READ docs/EXNESS_INTEGRATION.md BEFORE relying on this module. Summary of
the one fact that shapes this whole file: Exness, like almost every retail
forex broker, does not expose a modern cloud REST/websocket API for algo
access. The supported path is the MetaTrader 5 terminal protocol -- this
adapter talks to a MT5 terminal that is INSTALLED, RUNNING, and LOGGED IN
to your Exness account, via the official `MetaTrader5` Python package.

That package is only officially supported on Windows (it talks to the
terminal over a local named pipe / DLL). Two consequences:

1. This module cannot be exercised inside this sandboxed Linux dev
   container -- there is no MT5 terminal here. Import is therefore guarded:
   importing fx_engine.broker.exness_mt5 does not fail elsewhere in the
   codebase, but instantiating MT5DataProvider/ExnessMT5Adapter without the
   `MetaTrader5` package installed and a running terminal will raise a
   clear RuntimeError rather than silently doing nothing.
2. Your 24/7 deployment target needs to be Windows (a small Windows VPS is
   the common, well-trodden choice) or Linux-with-Wine running the MT5
   terminal -- not a plain Linux box making HTTP calls. See
   docs/DEPLOYMENT.md.

Credentials (login/password/server) come ONLY from environment variables
via fx_engine.config -- never hard-code them here.
"""
from __future__ import annotations

from datetime import datetime, timezone

import pandas as pd

from fx_engine import config
from fx_engine.broker.base import AccountInfo, BrokerAdapter
from fx_engine.costs import Quote
from fx_engine.data.models import Timeframe
from fx_engine.data.providers import HistoricalDataProvider

try:
    import MetaTrader5 as mt5  # type: ignore
    _MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    _MT5_AVAILABLE = False


_MT5_TIMEFRAME = None  # populated lazily once mt5 is confirmed available


def _require_mt5() -> None:
    if not _MT5_AVAILABLE:
        raise RuntimeError(
            "The 'MetaTrader5' package is not installed / not usable in this environment. "
            "This adapter must run on Windows (or Linux+Wine) with a MetaTrader 5 terminal "
            "installed, running, and logged into your Exness account. "
            "See docs/EXNESS_INTEGRATION.md for setup steps. Install with: pip install MetaTrader5"
        )


def _ensure_initialized() -> None:
    _require_mt5()
    if mt5.terminal_info() is not None:
        return
    kwargs = {}
    if config.EXNESS_MT5_TERMINAL_PATH:
        kwargs["path"] = config.EXNESS_MT5_TERMINAL_PATH
    if config.EXNESS_MT5_LOGIN:
        kwargs["login"] = int(config.EXNESS_MT5_LOGIN)
    if config.EXNESS_MT5_PASSWORD:
        kwargs["password"] = config.EXNESS_MT5_PASSWORD
    if config.EXNESS_MT5_SERVER:
        kwargs["server"] = config.EXNESS_MT5_SERVER
    ok = mt5.initialize(**kwargs)
    if not ok:
        raise RuntimeError(f"MT5 initialize() failed: {mt5.last_error()}")


def _timeframe_map():
    global _MT5_TIMEFRAME
    if _MT5_TIMEFRAME is None:
        _require_mt5()
        _MT5_TIMEFRAME = {
            Timeframe.M15: mt5.TIMEFRAME_M15,
            Timeframe.H1: mt5.TIMEFRAME_H1,
            Timeframe.H4: mt5.TIMEFRAME_H4,
            Timeframe.D1: mt5.TIMEFRAME_D1,
        }
    return _MT5_TIMEFRAME


class MT5DataProvider(HistoricalDataProvider):
    """Historical/live OHLC pulled from Exness's own feed via a running
    MT5 terminal -- the only data source in this codebase whose prices
    actually match what you would trade. See module docstring for setup."""

    name = "mt5"

    def get_ohlc(self, pair: str, timeframe: Timeframe, start: datetime, end: datetime) -> pd.DataFrame:
        _ensure_initialized()
        tf = _timeframe_map()[timeframe]
        rates = mt5.copy_rates_range(pair, tf, start, end)
        if rates is None or len(rates) == 0:
            raise ValueError(f"MT5 returned no rates for {pair} {timeframe.value}: {mt5.last_error()}")
        df = pd.DataFrame(rates)
        df["time"] = pd.to_datetime(df["time"], unit="s", utc=True)
        df = df.set_index("time").rename(columns={"tick_volume": "volume"})
        return df[["open", "high", "low", "close", "volume"]]


class ExnessMT5Adapter(BrokerAdapter):
    name = "exness_mt5"

    def is_connected(self) -> bool:
        if not _MT5_AVAILABLE:
            return False
        try:
            _ensure_initialized()
            return mt5.terminal_info() is not None
        except Exception:
            return False

    def get_quote(self, pair: str) -> Quote:
        _ensure_initialized()
        tick = mt5.symbol_info_tick(pair)
        if tick is None:
            raise ValueError(f"No tick data for {pair}: {mt5.last_error()}")
        return Quote(bid=tick.bid, ask=tick.ask)

    def get_account_info(self) -> AccountInfo:
        _ensure_initialized()
        info = mt5.account_info()
        if info is None:
            raise RuntimeError(f"Could not read MT5 account info: {mt5.last_error()}")
        return AccountInfo(balance=info.balance, equity=info.equity, currency=info.currency, leverage=info.leverage)

    # place_order() intentionally NOT overridden -- inherits BrokerAdapter's
    # base implementation, which always raises. See broker/base.py docstring.
