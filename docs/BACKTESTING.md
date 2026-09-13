# Backtesting Methodology

## No-lookahead, by construction

`BacktestEngine.run()` (`fx_engine/backtest/engine.py`) iterates bar by
bar. At bar `i`, a strategy is only ever shown `df.iloc[:i+1]` (and the
matching slice of the precomputed feature/regime frames -- see
`docs/ARCHITECTURE.md`). If a signal fires, it is filled at bar `i+1`'s
**open**, never at bar `i`'s own close -- you cannot trade the same
candle you used to decide.

## Spread is not an afterthought

`fx_engine/costs.py`:

```
BUY  entry -> ASK      SELL entry -> BID
BUY  exit  -> BID      SELL exit  -> ASK
```

Every simulated fill goes through this. `SpreadModel` estimates spread
in pips per pair, per bar, widened during the low-liquidity rollover
window (21:00-23:00 UTC) and during high-volatility regimes -- real
Exness-like behavior, but an **approximation** (see the limitation below).

## What "risk/reward after costs" means

For a nominal setup with (say) a 10-pip stop and 20-pip target and a
3-pip spread, the realized economics are **not** 1:2. The round-trip
spread cost is subtracted from the reward and added to the risk:

```
effective_risk   = nominal_risk_pips + spread_pips
effective_reward = max(0, nominal_reward_pips - spread_pips)
risk_reward_after_costs = effective_reward / effective_risk
```

`signal_engine.py` rejects any candidate below `MIN_RR_AFTER_COSTS`
(default 1.5) after this adjustment, and separately rejects if spread is
more than `MAX_SPREAD_TO_STOP_RATIO` (default 30%) of the nominal stop
distance -- both configurable in `.env`, both computed per-instrument
rather than against one universal threshold, per the original brief's
explicit instruction not to use a single global spread cutoff.

## Conservative same-bar assumption

If a single bar's high/low range technically could have touched both the
stop and the target, the engine assumes the **stop hit first**. This is
the conservative choice and avoids overstating results.

## Metrics computed

`fx_engine/backtest/metrics.py`: total trades, win rate, loss rate,
profit factor, expectancy (in R-multiples, so comparable across pairs
with very different pip values), average win/loss, net R, max drawdown,
Sharpe-like and Sortino-like ratios, max consecutive losses, recovery
factor, and breakdowns by pair, by regime, and by exit reason (SL/TP/TIME/
END_OF_DATA).

## Walk-forward and the holdout

See `fx_engine/backtest/walk_forward.py` and its own detailed docstring.
Short version: one continuous no-lookahead backtest is run, then its
trades are partitioned by entry time into `WALK_FORWARD_FOLDS` (default
5) sequential folds plus a final `HOLDOUT_FRACTION` (default 20%) slice.
A strategy is called `CONSISTENT` only if expectancy is positive in most
folds *and* holds up in the untouched holdout slice; otherwise it's
`REJECTED`, `OVERFIT_OR_DEGRADED`, `WEAK_OUT_OF_SAMPLE`, or
`INCONCLUSIVE` (too few trades to say anything). These verdicts are
meant to be taken at face value.

Because these strategies use fixed, hand-specified rules rather than
fitted parameters, this isn't classical walk-forward *optimization* --
it's a test of whether the edge is stable across time, which is the
question that actually matters here. If you start tuning parameters
(e.g. trying EMA 45 vs 50 vs 55), re-run walk-forward per candidate and
compare fold-to-fold consistency, not just the full-sample number --
section 14 of the original brief's "EMA 49/50/51 all good vs. only 50
good" overfitting test applies directly.

## Known limitation: spread is approximated, not historical

There is no historical Exness bid/ask tick feed wired up (that requires
the MT5 adapter -- see `docs/EXNESS_INTEGRATION.md`). Until you're
backtesting against real MT5-sourced spread history, treat any backtest's
absolute profitability numbers as directionally informative at best, and
lean more on relative comparisons (does this strategy survive walk-
forward at all) than on the precise expectancy figure. See
`docs/LIMITATIONS.md`.
