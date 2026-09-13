"""Exness integration via a local MetaTrader 5 terminal.

READ docs/EXNESS_INTEGRATION.md BEFORE relying on this module. Summary of
the one fact that shapes this whole file: Exness, like almost every retail
forex broker, does not expose a modern cloud REST/websocket API for algo
access. The supported path is the MetaTrader 5 terminal protocol -- this
adapter talks to a MT5 terminal that is INSTALLED, RUNNING, and LOGGED IN
to your Exness account, via the official `MetaTrader5` Python package.

That package ships ONLY win_amd64 wheels on PyPI (verified directly
against the PyPI JSON API while building this module -- every release for
every supported Python version, cp36 through cp314, is win_amd64; there is
no Linux/macOS wheel and no sdist). Two consequences:

1. This module cannot be exercised inside this sandboxed Linux dev
   container -- there is no MT5 terminal here, and the package itself
   cannot even be installed here. Import is therefore guarded: importing
   fx_engine.broker.exness_mt5 does not fail elsewhere in the codebase,
   but instantiating MT5DataProvider/ExnessMT5Adapter without the
   `MetaTrader5` package installed and a running terminal will raise a
   clear RuntimeError rather than silently doing nothing.
2. Your 24/7 deployment target needs to be Windows (a small Windows VPS is
   the common, well-trodden choice) or Linux-with-Wine running a Windows
   Python interpreter -- not a plain Linux box, and not a Linux Python
   venv even under Wine. See docs/DEPLOYMENT.md.

Credentials (login/password/server) come ONLY from environment variables
via fx_engine.config -- never hard-code them here.

Everything below was verified with a fake `MetaTrader5` module injected
into sys.modules (tests/test_exness_mt5.py) -- exercising the actual
control flow (login-mismatch recovery, retries, symbol-not-found errors)
without needing a real terminal. It has NOT been run against a real MT5
terminal or a real Exness account; that verification can only happen on
a Windows machine, which this development sandbox is not. See
docs/LIMITATIONS.md.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timezone

import pandas as pd

from fx_engine import config
from fx_engine.broker.base import AccountInfo, BrokerAdapter
from fx_engine.costs import Quote
from fx_engine.data.models import Timeframe
from fx_engine.data.providers import HistoricalDataProvider

logger = logging.getLogger("fx_engine.broker.exness_mt5")

try:
    import MetaTrader5 as mt5  # type: ignore
    _MT5_AVAILABLE = True
except ImportError:
    mt5 = None
    _MT5_AVAILABLE = False


_MT5_TIMEFRAME = None  # populated lazily once mt5 is confirmed available
_INIT_RETRY_ATTEMPTS = 3
_INIT_RETRY_BACKOFF_SECONDS = 2.0
_DATA_RETRY_ATTEMPTS = 3
_DATA_RETRY_BACKOFF_SECONDS = 1.0


def _require_mt5() -> None:
    if not _MT5_AVAILABLE:
        raise RuntimeError(
            "The 'MetaTrader5' package is not installed / not usable in this environment. "
            "It ships Windows-only wheels (no Linux/macOS build exists on PyPI at all), so this "
            "adapter must run on Windows (or Linux+Wine running a Windows Python interpreter) "
            "with a MetaTrader 5 terminal installed, running, and logged into your Exness account. "
            "See docs/EXNESS_INTEGRATION.md for setup steps. Install with: pip install MetaTrader5"
        )


def _current_login() -> int | None:
    info = mt5.account_info()
    return int(info.login) if info is not None else None


def _do_initialize() -> None:
    kwargs = {}
    if config.EXNESS_MT5_TERMINAL_PATH:
        kwargs["path"] = config.EXNESS_MT5_TERMINAL_PATH
    if config.EXNESS_MT5_LOGIN:
        kwargs["login"] = int(config.EXNESS_MT5_LOGIN)
    if config.EXNESS_MT5_PASSWORD:
        kwargs["password"] = config.EXNESS_MT5_PASSWORD
    if config.EXNESS_MT5_SERVER:
        kwargs["server"] = config.EXNESS_MT5_SERVER

    for attempt in range(1, _INIT_RETRY_ATTEMPTS + 1):
        if mt5.initialize(**kwargs):
            break
        error = mt5.last_error()
        if attempt == _INIT_RETRY_ATTEMPTS:
            raise RuntimeError(
                f"MT5 initialize() failed after {_INIT_RETRY_ATTEMPTS} attempts: {error}. Common causes: "
                "the terminal isn't installed at EXNESS_MT5_TERMINAL_PATH, the terminal isn't running, "
                "or the login/password/server in .env don't match an account the terminal can reach. "
                "See docs/EXNESS_INTEGRATION.md."
            )
        logger.warning("MT5 initialize() failed (attempt %d/%d): %s -- retrying", attempt, _INIT_RETRY_ATTEMPTS, error)
        time.sleep(_INIT_RETRY_BACKOFF_SECONDS * attempt)

    if not config.EXNESS_MT5_LOGIN:
        return
    wanted_login = int(config.EXNESS_MT5_LOGIN)
    if _current_login() == wanted_login:
        return
    # initialize() connected to a terminal, but it's sitting on a different
    # account than configured (e.g. a shared/pre-existing terminal session)
    # -- explicitly log in to the intended account rather than silently
    # operating against the wrong one.
    ok = mt5.login(wanted_login, password=config.EXNESS_MT5_PASSWORD, server=config.EXNESS_MT5_SERVER)
    if not ok:
        raise RuntimeError(
            f"MT5 terminal is running but logged into a different account than EXNESS_MT5_LOGIN "
            f"({wanted_login}), and switching to it failed: {mt5.last_error()}"
        )


def _ensure_initialized() -> None:
    _require_mt5()
    if mt5.terminal_info() is not None and (not config.EXNESS_MT5_LOGIN or _current_login() == int(config.EXNESS_MT5_LOGIN)):
        return
    _do_initialize()


def _ensure_symbol_ready(symbol: str) -> None:
    """MT5 will return None from tick/rate calls for a symbol that exists
    but isn't currently selected in Market Watch -- a very easy silent
    trap. Select it explicitly and fail loudly, with the Exness-specific
    suffix gotcha spelled out, rather than an opaque 'no data'."""
    info = mt5.symbol_info(symbol)
    if info is None or not mt5.symbol_select(symbol, True):
        raise ValueError(
            f"MT5 does not recognize symbol '{symbol}' on this account ({mt5.last_error()}). "
            "Exness sometimes suffixes symbols by account type (e.g. 'EURUSDm', 'EURUSD.raw') -- "
            "check Market Watch in the terminal for the exact name and, if different, override it "
            "per-pair before calling this adapter. See docs/EXNESS_INTEGRATION.md."
        )


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
        _ensure_symbol_ready(pair)
        tf = _timeframe_map()[timeframe]

        rates = None
        for attempt in range(1, _DATA_RETRY_ATTEMPTS + 1):
            rates = mt5.copy_rates_range(pair, tf, start, end)
            if rates is not None and len(rates) > 0:
                break
            if attempt < _DATA_RETRY_ATTEMPTS:
                logger.warning("copy_rates_range returned no data for %s %s (attempt %d/%d) -- retrying",
                                pair, timeframe.value, attempt, _DATA_RETRY_ATTEMPTS)
                time.sleep(_DATA_RETRY_BACKOFF_SECONDS)

        if rates is None or len(rates) == 0:
            raise ValueError(
                f"MT5 returned no rates for {pair} {timeframe.value} between {start} and {end}: "
                f"{mt5.last_error()}. If the symbol and date range are correct, this pair/timeframe "
                "may not have history that far back on this account."
            )
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
        _ensure_symbol_ready(pair)
        tick = mt5.symbol_info_tick(pair)
        if tick is None:
            raise ValueError(f"No tick data for {pair}: {mt5.last_error()}")
        tick_age = datetime.now(timezone.utc).timestamp() - tick.time
        if tick_age > 300:
            logger.warning("%s tick is %.0fs old -- market may be closed (weekend/holiday) or feed is stale", pair, tick_age)
        return Quote(bid=tick.bid, ask=tick.ask)

    def get_account_info(self) -> AccountInfo:
        _ensure_initialized()
        info = mt5.account_info()
        if info is None:
            raise RuntimeError(f"Could not read MT5 account info: {mt5.last_error()}")
        return AccountInfo(balance=info.balance, equity=info.equity, currency=info.currency, leverage=info.leverage)

    # place_order() intentionally NOT overridden -- inherits BrokerAdapter's
    # base implementation, which always raises. See broker/base.py docstring.
