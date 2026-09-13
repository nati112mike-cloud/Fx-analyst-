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

## What that means practically

- The `MetaTrader5` Python package is officially supported on **Windows
  only**. It works by calling into the terminal's local API, not over the
  network. Linux support exists unofficially via Wine (running the
  Windows MT5 terminal under Wine, then the Windows build of Python +
  `MetaTrader5` also under Wine, talking to it) -- more fragile, but a
  documented community path if you're deploying on Linux.
- This sandboxed development container has neither Windows nor a
  installable MT5 terminal, so `fx_engine/broker/exness_mt5.py` could be
  *written and reviewed* here but not *run* here. Its import is guarded:
  importing the module elsewhere in the codebase never fails; only
  instantiating `MT5DataProvider` / `ExnessMT5Adapter` without the package
  installed and a terminal running raises a clear `RuntimeError` telling
  you exactly what's missing.
- Your eventual 24/7 host (see `docs/DEPLOYMENT.md`) needs to be a
  Windows machine (a small Windows VPS is the common choice for this
  exact use case) or Linux+Wine -- not a plain Linux box making HTTP
  calls.

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
