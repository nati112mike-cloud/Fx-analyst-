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

## Yahoo integration: live-tested via Colab, one real bug found and fixed

`YahooFinanceProvider` was rewritten to handle Yahoo's per-interval
lookback limits, retry transient failures with backoff, and fail with a
clear, diagnosable error rather than silently truncated data --
initially verified only offline (`tests/test_yahoo_provider.py` against a
mocked response), since `query1/query2.finance.yahoo.com` are blocked at
the network-policy level in this dev sandbox (`CONNECT tunnel failed,
response 403`).

**It has since been run for real**, from Google Colab (the workaround for
a user whose own machine couldn't get a working Python install --
Colab has real internet and needs no local setup). That run's actual
output was a live 422 Unprocessable Entity from Yahoo for a 728-day
60-minute-bar request -- meaning the "~730 days" lookback limit this
provider had assumed (a commonly cited figure) is wrong, or Yahoo has
tightened it since. Worse: the original code treated 400/422 as a
transient failure and retried the *identical* rejected request four
times, which cannot succeed and just wastes time.

Fixed: `_fetch_chunk` no longer retries a 400/422 -- `get_ohlc` catches it
and bisects the requested range in half, recursively, retrying each half,
so the provider self-corrects to whatever Yahoo's real current limit is
instead of relying on a hard-coded guess that can silently go stale
again. `tests/test_yahoo_provider.py` gained two tests for this exact
scenario: bisection recovering into a complete series, and a
range that's rejected at every size terminating in a clear `ValueError`
(bounded by `_MAX_BISECTION_DEPTH`) rather than retrying forever.

This is the project's first genuinely live-verified fix -- not just
offline-tested against an assumed schema, but caught from a real failure
against the real endpoint, exactly the gap `docs/LIMITATIONS.md` flagged
before this. Still not exercised live: true multi-year chunking end-to-end,
and daily-bar (`1d`) requests.

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

## Position sizing: verified against hand calculations

`fx_engine/position_sizing.py`'s three pip-value cases (direct pairs like
EURUSD, inverse pairs like USDJPY, and cross pairs like EURJPY needing a
third conversion rate) were each checked against manually computed
expected values in `tests/test_position_sizing.py` -- e.g. EURUSD pip
value $10/lot exactly, USDJPY pip value `1000/149.50` exactly, and a
$10,000-equity/0.5%-risk/30-pip-stop EURUSD trade sizing to exactly 0.16
lots after rounding down to the 0.01 lot step (never up past the risk
budget, checked across four different equity sizes). The cross-pair
auto-resolution (fetching a USDJPY quote to price an EURJPY position) was
exercised through the full `SignalEngine` and initially found -- and
fixed -- a real bug: the lookup window was too short for
`SyntheticDataProvider`'s own minimum bar count, which a live end-to-end
run surfaced immediately (see `docs/PERFORMANCE.md` engineering-performance
pattern above: a class of bug, not a one-off, since the exact same
too-short-window issue also had to be fixed in `paper_trading.py`'s trade
resolution once tested end-to-end).

## Daily loss circuit breaker: verified including the UTC-day boundary

`Database.daily_loss_limit_breached()` and `realized_pnl_today()` were
tested for: summing only today's closed trades (not yesterday's, not
still-open ones, not trades with an unknown dollar P&L), the exact-limit
boundary (breached at precisely 2.00% of equity, not 1.99% or 2.01%), and
that a profitable day never breaches regardless of trade count. A live
`SignalEngine.evaluate_pair()` call was verified to actually return
`NoTradeReason("... daily loss circuit breaker ...")` once a synthetic
loss was recorded, proving the gate runs before any strategy computation
(not just that the underlying query is correct).

## Swap/rollover: rollover-crossing counts verified against hand-picked dates

`fx_engine/swap.py`'s `count_rollover_charges()` was checked against
manually counted crossings for specific calendar dates (2024-03-11 is a
Monday, 2024-03-13 a Wednesday): zero charges for same-day-before-rollover,
one charge for an ordinary overnight hold, and four charge-units
(1 ordinary + 1 tripled) for a hold spanning a Wednesday rollover. The
result was then verified to actually flow into `Trade.pnl_price()`'s
final number, not just exist as an isolated calculation.

## Dashboard: every route tested, then actually rendered and screenshotted

Beyond `tests/test_dashboard.py` (all 5 pages return 200 with expected
data present, and all 5 render without error on a brand-new empty
database), the dashboard was run as a real Flask server in this sandbox
and its pages were fetched with `curl` (200 on every route) and then
rendered with a headless Chromium browser (already available in this
environment) to produce actual screenshots -- checked visually for the
overview stats, the signals/strategies tables including the color-coded
walk-forward verdict badges, and the server-rendered SVG equity-curve
sparkline, in both light and dark `prefers-color-scheme`. This caught one
real bug before it shipped: the `reason` column was queried from the
database correctly but never rendered in `signals.html`'s template --
found because the test asserted for specific reason text in the rendered
page body, not just a 200 status code.

## What has NOT been verified

- **Whether any strategy has a real edge on real data.** Yahoo fetching
  itself is now live-verified (see above), but that verification run
  didn't get as far as reading `walk-forward`'s actual verdict output --
  that's the next thing to actually look at.
- Full backtest/walk-forward runs across the whole default pair universe
  (9 pairs) and longer date ranges against the live Yahoo endpoint --
  verified so far for a couple of pairs at H1/H4 over roughly a 2-year
  span from Colab.
- MT5/Exness live end-to-end -- still blocked in this development sandbox
  (no Windows available at all, not just a network policy issue; hardened
  and unit-tested offline, see above and `docs/EXNESS_INTEGRATION.md`).
- Real Telegram delivery (formatting was verified; `TelegramNotifier.send()`
  needs a real bot token to test the actual HTTP call).
- Continuous 24/7 operation (needs a real deployment target per
  `docs/DEPLOYMENT.md`).
- The rollover hour (21:00 UTC) and triple-swap weekday (Wednesday) used
  by `fx_engine/swap.py` against a real Exness account's actual schedule
  -- these are the common industry convention, not a confirmed
  Exness-specific figure.
- The dashboard under real concurrent usage / any load beyond a single
  local user clicking around -- it's a small Flask dev-server setup by
  design (personal, single-user, `127.0.0.1`), not load-tested.
