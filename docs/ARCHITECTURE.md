# Architecture

## Data flow, one evaluation cycle

```
HistoricalDataProvider.get_ohlc()
        |
        v
features.compute_feature_frame()  -- every indicator, computed ONCE
        |
        v
regime.compute_regime_series()  -- 8-state classification per bar
        |
        v
daily loss circuit breaker check (db.Database.daily_loss_limit_breached)
        |
        v
for each of 7 strategies: strategy.generate(df, pair, regime, features)
        |
        v
ensemble.StrategyEnsemble.combine()  -- weighted vote -> overall signal
        |
        v
costs.SpreadModel  -- reject if spread too wide relative to stop,
                       or if risk/reward after costs is too thin
        |
        v
scoring.compute_signal_score()  -- 0-100 quality score, reject if too low
        |
        v
position_sizing.calculate_position_size()  -- lots/risk/margin from real
                                              (or fallback) account equity
        |
        v
db.Database.insert_signal()  +  telegram_bot.TelegramNotifier.send()
```

Every arrow above can terminate in **NO TRADE** (`signal_engine.NoTradeReason`)
-- that is the expected, common outcome, not a failure mode. The daily
loss check runs FIRST, before any strategy even evaluates, so a breached
day is cheap to reject on every pair in the loop.

## Why `features.py` exists

Every indicator in `indicators.py` (EMA, RSI, MACD, ATR, ADX, Bollinger,
Donchian, swing points, percentile ranks) is *causal*: it only looks
backward. That means computing an indicator once on a full price series
and reading its value at position `i` gives the identical result to
recomputing it from scratch on `df.iloc[:i+1]`. The backtest engine relies
on this: it calls `compute_feature_frame(df)` and `compute_regime_series(df)`
exactly once per backtest, then slices the precomputed frames per bar
(`O(1)`-ish) instead of recomputing indicators on a growing window at every
single bar (`O(n^2)` -- this was the first version's actual bug, caught
and fixed during development; see `docs/PERFORMANCE.md`).

## Why strategies take an optional `features` argument

`BaseStrategy.generate(df, pair, regime, features=None)` -- when `features`
is provided (always, from the backtest engine and signal engine), the
strategy reads precomputed columns. When it's `None` (standalone/ad-hoc
use), the strategy computes what it needs from `df` itself. Same logic
either way, different cost.

## Regime gating vs. weighting

Two separate mechanisms restrict when a strategy can fire:

1. **Hard gate** (`regime.STRATEGY_ALLOWED_REGIMES`, checked inside each
   strategy's own `generate()`): a strategy built for trending markets
   simply returns NEUTRAL outside trend regimes, full stop.
2. **Soft weighting** (`ensemble.StrategyEnsemble`): among strategies that
   *did* fire, each one's vote is weighted by its own validated backtest
   expectancy -- a strategy with a negative track record barely moves the
   needle even if it's technically allowed to vote in this regime.

## Ensemble math

```
score = (buy_weight - sell_weight) / total_weight        # -1..+1
STRONG_BUY  >= 0.55        STRONG_SELL <= -0.55
BUY         >= 0.30        SELL        <= -0.30
WEAK_BUY    >= 0.12        WEAK_SELL   <= -0.12
otherwise -> NO_TRADE
```
...and additionally requires at least `ensemble.MIN_AGREEING_STRATEGIES`
(2) strategies actually voting that direction, so one high-weight
strategy can never single-handedly produce a signal (section 9 of the
original brief).

## Signal quality score components

See `scoring.WEIGHTS` for the exact breakdown (agreement, risk/reward,
spread quality, regime confidence, historical expectancy, news risk).
Every component is 0-1 and visible in `SignalScore.components` -- nothing
about the final 0-100 number is opaque.

## Position sizing and account equity

`position_sizing.calculate_position_size()` is pure math: given entry,
stop, equity, and risk%, it returns lots/dollar-risk/required-margin. The
equity it's given comes from `SignalEngine.current_account()`, which
prefers a connected `BrokerAdapter`'s real `get_account_info()` (e.g.
`PaperBrokerAdapter`'s running balance) and falls back to
`config.ACCOUNT_STARTING_BALANCE` when no broker is attached. Cross-pair
pip values (e.g. EURJPY on a USD account) need a third conversion rate,
resolved automatically via `SignalEngine._price_lookup()` fetching a
recent quote for the needed pair (e.g. USDJPY).

## Paper trading's money flow

`paper_trading.PaperTradingLoop` is the only place dollar P&L becomes
real (well, real-*ish* -- simulated): each closed trade's `pnl_amount` is
`pnl_r * risk_amount` (both stored on the `paper_trades` row at entry)
plus any swap cost/credit (`fx_engine/swap.py`, converted from pips to
dollars via `position_sizing.pip_value_per_lot`), and that amount is
added directly to the attached `PaperBrokerAdapter.balance`. That same
balance is what `SignalEngine.current_account()` reads for the *next*
trade's position sizing and what `Database.daily_loss_limit_breached()`
compares today's realized losses against -- the loop is a closed, self-
consistent simulation of one account, not a disconnected pile of
independent trade records.

## Dashboard

`fx_engine/dashboard.py` is a thin, read-only Flask layer: every route
calls a `Database` query method (several added specifically for this --
`recent_signals`, `latest_backtest_per_strategy_pair`, `recent_paper_trades`,
`paper_equity_curve`, `recent_health`) and renders a Jinja template from
`fx_engine/templates/`. No route mutates state. The equity-curve chart on
`/paper-trades` is a plain SVG `<polyline>` computed server-side in
`_equity_curve_svg_points()` -- no JS charting library, no CDN dependency.
