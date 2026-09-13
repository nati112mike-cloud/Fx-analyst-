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
db.Database.insert_signal()  +  telegram_bot.TelegramNotifier.send()
```

Every arrow above can terminate in **NO TRADE** (`signal_engine.NoTradeReason`)
-- that is the expected, common outcome, not a failure mode.

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
