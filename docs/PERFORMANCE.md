# Performance -- Pipeline Correctness, Not Trading Performance

This document tracks whether the *system* works, as distinct from whether
any *strategy* has a real edge (for that, see `docs/LIMITATIONS.md` on
synthetic vs. real data, and always check a strategy's actual
`walk-forward` verdict before trusting it).

## Engineering performance: the O(n^2) bug and its fix

The first working version of `BacktestEngine` called each strategy's
`generate()` on a freshly truncated `df.iloc[:i+1]` at every bar, and each
strategy recomputed its indicators from scratch on that truncated window
every time. Individually correct (genuinely zero lookahead), but
quadratic: a ~3,100-bar H4/2-year backtest across 7 strategies did not
finish within a 120-second timeout.

Fix (`fx_engine/features.py`, `fx_engine/regime.py`): every indicator is
causal (backward-looking only), so computing the full indicator series
once and slicing it per bar is mathematically identical to recomputing on
every truncation, and is `O(n)` instead of `O(n^2)`. After the fix, the
same 7-strategy, 2-year, H4 backtest plus a 5-fold walk-forward validation
completed in well under a minute.

## What was actually verified end-to-end (synthetic data)

- `SyntheticDataProvider` generates ~3,100 regime-switching H4 bars for a
  2-year EUR/USD series (and equivalent for other pairs) on demand.
- All 7 strategies ran against that data and produced real, differing,
  non-fabricated trade counts and metrics -- e.g. in one representative
  run: `trend_pullback` 42 trades / win rate 0.29 / expectancy -0.18R;
  `price_action` 39 trades / win rate 0.54 / expectancy +0.26R; `breakout`
  10 trades / win rate 0.50 / expectancy +0.63R. Numbers vary run to run
  (new synthetic data each time) -- the point is that they're computed,
  not templated.
- Walk-forward validation correctly produced different verdicts for
  different strategy/pair combinations in the same run: one
  `trend_pullback` run was flagged `REJECTED` (negative expectancy in a
  majority of folds); a `price_action` run on the same data was flagged
  `CONSISTENT` (positive expectancy in 4/5 folds, held up in the untouched
  holdout). This is the anti-overfitting logic actually discriminating,
  not rubber-stamping.
- The full `signal_engine.SignalEngine.evaluate_pair()` path ran against
  all 9 default pairs, producing both `NoTradeReason` outcomes (the
  common case, exactly as intended) and fully actionable signals that
  passed every gate (spread-to-stop ratio, risk/reward after costs,
  minimum signal score) and produced a correctly-formatted Telegram
  message with entry/SL/TP, spread, regime, per-strategy agreement, and
  the "UNVALIDATED" warning correctly appearing when a voting strategy had
  no backtest on record yet.
- Signals persisted correctly to SQLite (`signals` table) with a real UUID,
  retrievable via `Database`.

## Yahoo integration: hardened and offline-tested, not yet live-tested

`YahooFinanceProvider` was rewritten to handle Yahoo's per-interval
lookback limits (auto-chunking multi-year H1/H4 requests), retry
transient failures with backoff, and fail with a clear, diagnosable error
rather than silently truncated data. `tests/test_yahoo_provider.py`
verifies all of this -- parsing, chunking into the correct number of
requests, concatenation without duplicates/gaps, retry-then-recover, and
the final-failure error message -- against a mocked response built to the
real documented Yahoo chart-API schema. Confirmed during this same
session: `query1.finance.yahoo.com` and `query2.finance.yahoo.com` both
return `CONNECT tunnel failed, response 403` from this sandbox --  an
organization network-policy denial, not a transient error -- so the code
is verified correct against the schema, but has never actually been
exercised against the live endpoint. That's the next milestone, from a
machine with normal internet access.

## MT5/Exness adapter: hardened and offline-tested, not live-tested

Confirmed against PyPI's JSON API (`https://pypi.org/pypi/MetaTrader5/json`)
while building this integration: every published `MetaTrader5` release,
for every supported Python version (3.6-3.14), ships as a `win_amd64`
wheel only -- no Linux/macOS wheel, no sdist. `pip install MetaTrader5`
was attempted in this sandbox and failed exactly as that implies ("No
matching distribution found"), so the real package cannot even be
installed here, let alone run against a real terminal.

What was verified instead: `fx_engine/broker/exness_mt5.py`'s actual
control flow, exercised against a fake `MetaTrader5` module injected into
`sys.modules` (`tests/test_exness_mt5.py`, 15 tests, all passing) --
- a terminal already logged into the wrong Exness account triggers an
  explicit `mt5.login()` switch rather than silently using the wrong one
  (and a failed switch raises, rather than proceeding anyway);
- `initialize()` and `copy_rates_range()` retry with backoff on transient
  failures and raise a clear, diagnosable error once retries are
  exhausted;
- an unrecognized symbol raises before any tick/rate call is attempted,
  naming Exness's common suffix pattern (`.raw`/`.pro`) as the likely
  cause;
- a stale tick is logged as a warning, not treated as fatal (a flat
  weekend market has a legitimately old last tick);
- the guarded-import behavior (package genuinely absent, the real state
  in this sandbox) raises a clear `RuntimeError` from every entry point,
  including `is_connected()` returning `False` rather than raising, and
  `place_order()` always raising `NotImplementedError` regardless of MT5
  availability.

This proves the adapter's logic is sound against realistic failure modes;
it does not prove the real MT5 terminal integration works end-to-end,
which can only happen on a Windows machine with a live Exness account --
the next milestone for this piece specifically.

## What has NOT been verified

- Real market data live end-to-end (Yahoo or MT5/Exness) -- blocked in
  this development sandbox by network policy (see `PROJECT_PLAN.md`
  section 1; both are now hardened and unit-tested offline, see above).
  This is the next thing to run, from an environment with normal internet
  access, before drawing any conclusion about real edge.
- Real Telegram delivery (formatting was verified; `TelegramNotifier.send()`
  needs a real bot token to test the actual HTTP call).
- Continuous 24/7 operation (needs a real deployment target per
  `docs/DEPLOYMENT.md`).
