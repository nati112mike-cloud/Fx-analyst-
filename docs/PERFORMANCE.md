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

## What has NOT been verified

- Real market data (Yahoo or MT5/Exness) -- blocked in this development
  sandbox by network policy (see `PROJECT_PLAN.md` section 1). This is
  the next thing to run, from an environment with normal internet access,
  before drawing any conclusion about real edge.
- Real Telegram delivery (formatting was verified; `TelegramNotifier.send()`
  needs a real bot token to test the actual HTTP call).
- The MT5/Exness adapter (needs Windows/Wine + a running terminal, neither
  available here).
- Continuous 24/7 operation (needs a real deployment target per
  `docs/DEPLOYMENT.md`).
