# Exness Integration -- Read This Before Assuming Anything

## The one fact that shapes everything else

Exness does not offer a modern cloud REST/websocket API you authenticate
with an API key. There is no `EXNESS_API_KEY`. The supported way to
access an Exness account programmatically is the **MetaTrader 5 terminal
protocol**: you run the actual MT5 terminal application, log it into your
Exness account, and talk to *that running terminal* from Python via the
official `MetaTrader5` package.

This is true for essentially every retail forex broker, not something
specific to Exness -- worth knowing generally, not just for this project.

**Confirmed directly against PyPI's JSON API while building this
integration** (`https://pypi.org/pypi/MetaTrader5/json`): every published
release of the `MetaTrader5` package, for every supported Python version
from 3.6 through 3.14, ships as a `win_amd64` wheel. There is no
Linux/macOS wheel and no source distribution at all -- `pip install
MetaTrader5` cannot succeed on Linux or macOS, full stop, not even to get
a broken/degraded install. This isn't a Windows *recommendation*, it's a
hard platform requirement.

## What that means practically

- The `MetaTrader5` Python package only installs and runs on **Windows**
  (natively, or under Wine running a Windows Python interpreter). It
  works by calling into the terminal's local API, not over the network.
- This sandboxed development container has neither Windows nor an
  installable MT5 terminal (confirmed above), so `fx_engine/broker/exness_mt5.py`
  was *written and reviewed* here but not *run against a real terminal*
  here. Its import is guarded: importing the module elsewhere in the
  codebase never fails; only instantiating `MT5DataProvider` /
  `ExnessMT5Adapter` without the package installed and a terminal running
  raises a clear `RuntimeError` telling you exactly what's missing.
- What *could* be verified here: the adapter's actual control flow --
  initialize/retry, detecting and recovering from a terminal that's
  logged into the wrong account, symbol validation, and retrying
  transient empty responses -- was exercised against a fake `MetaTrader5`
  module standing in for the real one (`tests/test_exness_mt5.py`, 15
  tests, all passing). That proves the adapter's logic is sound; it does
  not prove the real terminal integration works end-to-end, which needs
  a real Windows run. See `docs/PERFORMANCE.md`.
- Your eventual 24/7 host (see `docs/DEPLOYMENT.md`) needs to be a
  Windows machine (a small Windows VPS is the common choice for this
  exact use case) or Linux+Wine -- not a plain Linux box making HTTP
  calls.

## What the adapter now handles (verified in `tests/test_exness_mt5.py`)

- **Wrong-account recovery**: if a MT5 terminal is already running but
  logged into a different account than `EXNESS_MT5_LOGIN` (common on a
  shared VPS, or a terminal you opened manually before starting this),
  the adapter detects the mismatch and explicitly calls `mt5.login()` to
  switch to the configured account, rather than silently operating
  against the wrong one.
- **Retry with backoff** on `initialize()` and `copy_rates_range()`, since
  both can transiently fail while the terminal reconnects to Exness's
  servers.
- **Symbol validation before use**: `symbol_info()`/`symbol_select()` are
  checked before any tick/rate call, and a missing symbol raises an error
  that explicitly names the common Exness suffix gotcha below, rather
  than an opaque "no data."
- **Stale-tick logging** (not a hard failure, since a flat weekend/holiday
  market legitimately has an old last tick): `get_quote()` logs a warning
  if the tick is more than 5 minutes old.

## Setup steps (do this on a Windows machine or VPS)

1. Download and install the MetaTrader 5 terminal from Exness (Exness's
   own site links the correctly-branded build, or the generic MT5
   installer works with any broker's server).
2. Log into your Exness account inside the terminal (demo account first,
   always).
3. `pip install MetaTrader5` (uncomment it in `requirements.txt`).
4. Set in `.env`:
   ```
   EXNESS_MT5_LOGIN=12345678
   EXNESS_MT5_PASSWORD=your-password
   EXNESS_MT5_SERVER=Exness-MT5Trial8   # exact server name shown in the terminal
   EXNESS_MT5_TERMINAL_PATH=C:\Program Files\MetaTrader 5\terminal64.exe
   ```
5. Set `FX_DATA_PROVIDER=mt5` in `.env`.
6. Run `python -m fx_engine.main signal-once --pair EURUSD` -- if MT5
   isn't reachable, you'll get the `RuntimeError` from
   `exness_mt5._require_mt5()` / `_ensure_initialized()`, not a silent
   failure or fabricated data.

## What still needs to be discovered on your end, per instrument

Before trusting `mt5` data for anything: confirm Exness's exact symbol
names for the pairs you care about (may differ slightly from the plain
`EURUSD` used throughout this codebase, e.g. a `.raw` or `.pro` suffix
depending on account type), your account's actual leverage and contract
specifications, and whether historical tick data (`mt5.copy_ticks_range`,
not currently wired up -- only `copy_rates_range` OHLC is) is available at
the depth you'd want for spread-history analysis. `MT5DataProvider.get_ohlc`
in `broker/exness_mt5.py` is the place to extend for tick-level work.
