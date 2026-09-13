"""Offline validation of the Exness/MT5 adapter.

The `MetaTrader5` package ships ONLY win_amd64 wheels on PyPI (verified
directly against the PyPI JSON API: every release, cp36 through cp314, is
win_amd64 -- no Linux/macOS wheel, no sdist), so it cannot be installed in
this Linux dev sandbox at all, let alone run against a real terminal. This
test suite verifies fx_engine/broker/exness_mt5.py's actual control flow
(guarded-import behavior, initialize/login-mismatch recovery, symbol
validation, retry-then-succeed, retry-exhausted) by injecting a fake
`MetaTrader5` module into sys.modules and reloading the adapter module
against it -- real logic, fake terminal. It does NOT verify behavior
against a real MT5 terminal or a real Exness account; that can only
happen on a Windows machine. See docs/LIMITATIONS.md.

Run with: python -m unittest discover -s tests
"""
from __future__ import annotations

import importlib
import sys
import time
import types
import unittest
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fx_engine import config
from fx_engine.data.models import Timeframe


def _fake_rates(start_ts: int, n: int, step: int = 3600) -> list[dict]:
    return [
        {"time": start_ts + i * step, "open": 1.0800 + i * 1e-4, "high": 1.0810 + i * 1e-4,
         "low": 1.0790 + i * 1e-4, "close": 1.0805 + i * 1e-4, "tick_volume": 100 + i}
        for i in range(n)
    ]


def _build_fake_mt5(connected: bool = False, account_login: int | None = None) -> MagicMock:
    fake = MagicMock(name="FakeMetaTrader5Module")
    fake.TIMEFRAME_M15, fake.TIMEFRAME_H1, fake.TIMEFRAME_H4, fake.TIMEFRAME_D1 = "M15", "H1", "H4", "D1"
    fake.terminal_info.return_value = types.SimpleNamespace(connected=True) if connected else None
    fake.account_info.return_value = (
        types.SimpleNamespace(login=account_login, balance=10_000.0, equity=10_050.0, currency="USD", leverage=100)
        if account_login is not None else None
    )
    fake.initialize.return_value = True
    fake.login.return_value = True
    fake.symbol_info.return_value = types.SimpleNamespace(name="EURUSD")
    fake.symbol_select.return_value = True
    fake.last_error.return_value = (-1, "mock error")
    return fake


@contextmanager
def _mt5_module_injected(fake_module):
    """Registers `fake_module` as the importable 'MetaTrader5' package and
    reloads fx_engine.broker.exness_mt5 so its guarded top-level `import
    MetaTrader5 as mt5` picks it up -- then restores the original
    (package-not-installed) state afterward so other tests aren't affected."""
    import fx_engine.broker.exness_mt5 as adapter_module

    with patch.dict(sys.modules, {"MetaTrader5": fake_module}):
        importlib.reload(adapter_module)
        try:
            yield adapter_module
        finally:
            pass
    importlib.reload(adapter_module)  # restore guarded/unavailable state


class TestPackageNotInstalled(unittest.TestCase):
    """Exercises the real, unpatched environment -- MetaTrader5 genuinely
    isn't installed here, so this is not a simulation."""

    def test_data_provider_raises_clear_runtime_error(self):
        from fx_engine.broker.exness_mt5 import MT5DataProvider
        with self.assertRaises(RuntimeError) as ctx:
            MT5DataProvider().get_ohlc("EURUSD", Timeframe.H1,
                                        datetime(2023, 1, 1, tzinfo=timezone.utc),
                                        datetime(2023, 1, 2, tzinfo=timezone.utc))
        self.assertIn("not installed", str(ctx.exception))

    def test_adapter_raises_clear_runtime_error(self):
        from fx_engine.broker.exness_mt5 import ExnessMT5Adapter
        with self.assertRaises(RuntimeError):
            ExnessMT5Adapter().get_quote("EURUSD")

    def test_is_connected_returns_false_not_an_exception(self):
        from fx_engine.broker.exness_mt5 import ExnessMT5Adapter
        self.assertFalse(ExnessMT5Adapter().is_connected())

    def test_place_order_always_raises_regardless_of_mt5_availability(self):
        from fx_engine.broker.exness_mt5 import ExnessMT5Adapter
        with self.assertRaises(NotImplementedError):
            ExnessMT5Adapter().place_order()


class TestWithFakeTerminal(unittest.TestCase):
    def setUp(self):
        self.sleep_patch = patch("time.sleep", return_value=None)
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)
        self.cfg_patches = [
            patch.object(config, "EXNESS_MT5_LOGIN", "555111"),
            patch.object(config, "EXNESS_MT5_PASSWORD", "secret"),
            patch.object(config, "EXNESS_MT5_SERVER", "Exness-MT5Trial8"),
            patch.object(config, "EXNESS_MT5_TERMINAL_PATH", ""),
        ]
        for p in self.cfg_patches:
            p.start()
            self.addCleanup(p.stop)

    def test_fresh_initialize_with_matching_account_succeeds(self):
        fake = _build_fake_mt5(connected=False, account_login=555111)
        with _mt5_module_injected(fake) as adapter_module:
            info = adapter_module.ExnessMT5Adapter().get_account_info()
            fake.initialize.assert_called_once()
            fake.login.assert_not_called()  # already the right account, no need to re-login
            self.assertEqual(info.balance, 10_000.0)
            self.assertEqual(info.currency, "USD")

    def test_terminal_running_under_wrong_account_triggers_explicit_login(self):
        fake = _build_fake_mt5(connected=True, account_login=999999)  # wrong account already logged in
        with _mt5_module_injected(fake) as adapter_module:
            adapter_module.ExnessMT5Adapter().get_account_info()
            fake.login.assert_called_once_with(555111, password="secret", server="Exness-MT5Trial8")

    def test_login_switch_failure_raises_with_context(self):
        fake = _build_fake_mt5(connected=True, account_login=999999)
        fake.login.return_value = False
        with _mt5_module_injected(fake) as adapter_module:
            with self.assertRaises(RuntimeError) as ctx:
                adapter_module.ExnessMT5Adapter().get_account_info()
            self.assertIn("different account", str(ctx.exception))

    def test_initialize_retries_then_succeeds(self):
        fake = _build_fake_mt5(connected=False, account_login=555111)
        fake.initialize.side_effect = [False, False, True]
        with _mt5_module_injected(fake) as adapter_module:
            adapter_module.ExnessMT5Adapter().get_account_info()
            self.assertEqual(fake.initialize.call_count, 3)

    def test_initialize_exhausts_retries_raises(self):
        fake = _build_fake_mt5(connected=False, account_login=555111)
        fake.initialize.return_value = False
        with _mt5_module_injected(fake) as adapter_module:
            with self.assertRaises(RuntimeError) as ctx:
                adapter_module.ExnessMT5Adapter().get_account_info()
            self.assertIn("failed after", str(ctx.exception))

    def test_unknown_symbol_raises_clear_error_mentioning_suffix(self):
        fake = _build_fake_mt5(connected=True, account_login=555111)
        fake.symbol_info.return_value = None
        fake.symbol_select.return_value = False
        with _mt5_module_injected(fake) as adapter_module:
            with self.assertRaises(ValueError) as ctx:
                adapter_module.ExnessMT5Adapter().get_quote("NOTAREALSYMBOL")
            self.assertIn("suffix", str(ctx.exception))

    def test_get_quote_returns_bid_ask(self):
        fake = _build_fake_mt5(connected=True, account_login=555111)
        fake.symbol_info_tick.return_value = types.SimpleNamespace(
            bid=1.0850, ask=1.0852, time=int(datetime.now(timezone.utc).timestamp()))
        with _mt5_module_injected(fake) as adapter_module:
            quote = adapter_module.ExnessMT5Adapter().get_quote("EURUSD")
            self.assertAlmostEqual(quote.bid, 1.0850)
            self.assertAlmostEqual(quote.ask, 1.0852)

    def test_get_quote_stale_tick_does_not_raise_but_is_usable(self):
        fake = _build_fake_mt5(connected=True, account_login=555111)
        stale_ts = int(datetime.now(timezone.utc).timestamp()) - 10_000  # ~2.8 hours old
        fake.symbol_info_tick.return_value = types.SimpleNamespace(bid=1.0850, ask=1.0852, time=stale_ts)
        with _mt5_module_injected(fake) as adapter_module:
            quote = adapter_module.ExnessMT5Adapter().get_quote("EURUSD")  # should not raise
            self.assertAlmostEqual(quote.bid, 1.0850)

    def test_copy_rates_retries_then_succeeds(self):
        fake = _build_fake_mt5(connected=True, account_login=555111)
        rates = _fake_rates(int(datetime(2023, 1, 2, tzinfo=timezone.utc).timestamp()), 20)
        fake.copy_rates_range.side_effect = [None, None, rates]
        with _mt5_module_injected(fake) as adapter_module:
            df = adapter_module.MT5DataProvider().get_ohlc(
                "EURUSD", Timeframe.H1, datetime(2023, 1, 2, tzinfo=timezone.utc), datetime(2023, 1, 3, tzinfo=timezone.utc))
            self.assertEqual(fake.copy_rates_range.call_count, 3)
            self.assertEqual(list(df.columns), ["open", "high", "low", "close", "volume"])
            self.assertEqual(len(df), 20)

    def test_copy_rates_exhausts_retries_raises_valueerror(self):
        fake = _build_fake_mt5(connected=True, account_login=555111)
        fake.copy_rates_range.return_value = None
        with _mt5_module_injected(fake) as adapter_module:
            with self.assertRaises(ValueError):
                adapter_module.MT5DataProvider().get_ohlc(
                    "EURUSD", Timeframe.H1, datetime(2023, 1, 2, tzinfo=timezone.utc), datetime(2023, 1, 3, tzinfo=timezone.utc))
            self.assertEqual(fake.copy_rates_range.call_count, adapter_module._DATA_RETRY_ATTEMPTS)

    def test_symbol_checked_before_fetching_rates(self):
        fake = _build_fake_mt5(connected=True, account_login=555111)
        fake.symbol_info.return_value = None
        fake.symbol_select.return_value = False
        with _mt5_module_injected(fake) as adapter_module:
            with self.assertRaises(ValueError):
                adapter_module.MT5DataProvider().get_ohlc(
                    "EURUSD", Timeframe.H1, datetime(2023, 1, 2, tzinfo=timezone.utc), datetime(2023, 1, 3, tzinfo=timezone.utc))
            fake.copy_rates_range.assert_not_called()


if __name__ == "__main__":
    unittest.main()
