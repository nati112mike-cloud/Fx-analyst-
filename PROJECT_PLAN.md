# PROJECT_PLAN.md -- AI Multi-Strategy Forex Signal Engine

Personal-use, signal-only research platform. Turns established, explicitly-
specified Forex strategies into backtested, walk-forward-validated,
spread-and-regime-aware signals delivered to Telegram. It does not place
real trades and does not claim any signal is guaranteed to win.

## 1. What changed from the original brief, and why

The original plan (preserved in spirit throughout this codebase) assumed a
few things that needed correcting before implementation:

1. **"Exness API"** does not mean a modern REST/websocket API with an API
   key. Exness, like almost every retail forex broker, is only accessible
   for algo purposes through the **MetaTrader 5 terminal protocol** -- a
   running MT5 terminal, officially Windows-only (Wine works unofficially),
   talked to via the `MetaTrader5` Python package. This reshapes hosting:
   see `docs/EXNESS_INTEGRATION.md` and `docs/DEPLOYMENT.md`.
2. **Backtesting needs Exness's own historical bid/ask**, not a generic
   vendor's, or the cost model is fiction relative to what you'd actually
   trade. The MT5 adapter is the intended real data source once you're set
   up; a documented spread *approximation* is used elsewhere (see
   `docs/LIMITATIONS.md`).
3. **This sandboxed development environment has no MT5 terminal and no
   general internet access** (outbound HTTP is allow-listed to a handful
   of domains -- PyPI, GitHub, the Anthropic API -- everything else,
   including Yahoo Finance and every forex data vendor tried, returns a
   policy-level 403). So everything in this repository is built and proven
   correct here using a clearly-labeled **synthetic data provider**; real
   data hookup (Yahoo for quick backtesting research, or MT5/Exness for
   the real thing) happens wherever you run this next, with normal
   internet access.
4. **Scope was built as a vertical slice first**, not 16 phases breadth-
   first: one full pipeline (data -> indicators -> regime -> 7 strategies
   -> ensemble -> spread/cost gate -> quality score -> position size -> DB
   -> Telegram) proven end-to-end before anything else, rather than
   shallow stubs of everything. That slice has since grown to cover
   position sizing, an auto-enforced daily loss breaker, swap/rollover
   cost modeling, and a local dashboard as well (see section 2 below and
   `docs/PERFORMANCE.md` for how each was verified). A live economic-
   calendar feed (plan section 15) remains the one deliberately-left gap
   rather than a half-built stub -- see `docs/LIMITATIONS.md`.

## 2. Architecture

```
fx_engine/
  config.py            environment-driven configuration, nothing hard-coded
  data/
    models.py           Timeframe enum, OHLC resampling helper
    providers.py         HistoricalDataProvider: Synthetic | Yahoo | MT5
  indicators.py          EMA/SMA/RSI/MACD/ATR/ADX/Bollinger/Donchian/swings
  features.py             precomputes every indicator ONCE per series
                           (this is what keeps backtests O(n), not O(n^2))
  regime.py               8-state market regime classifier + strategy gating
  costs.py                 bid/ask spread modeling, ask/bid fill rules
  swap.py                   rollover cost mechanism (pips/lot/night, triple
                             Wednesday), rates default to zero -- see limitations
  position_sizing.py         lots/risk/margin from equity+risk%+stop, correct
                             pip-value math for direct/inverse/cross pairs
  strategies/
    base.py                 StrategySpec / StrategyResult / BaseStrategy
    trend_pullback.py, momentum.py, breakout.py, mean_reversion.py,
    price_action.py, volatility.py, multi_timeframe.py
  backtest/
    engine.py                spread-aware, no-lookahead bar-by-bar backtester
    metrics.py                win rate, profit factor, expectancy, Sharpe, ...
    walk_forward.py            sequential folds + untouched holdout, overfit flags
  ensemble.py                combines strategy votes, weighted by validated
                             historical expectancy (never arbitrary preference)
  scoring.py                  0-100 signal quality score (NOT a probability)
  db.py                        SQLite: strategy_versions, backtests, signals,
                               signal_outcomes, paper_trades, system_health;
                               also daily realized-P&L / loss-breaker queries
                               and the read models the dashboard uses
  broker/
    base.py                    BrokerAdapter interface (signal-only by default)
    paper.py                    simulated fills + running balance for paper trading
    exness_mt5.py                real Exness access via MT5 terminal
  news.py                        economic-calendar risk filter (see limitations)
  telegram_bot.py                  signal formatting + sending, incl. position size
  signal_engine.py                  orchestrates one full evaluation cycle,
                                    including the daily-loss-breaker gate
  paper_trading.py                   continuous loop: signals -> paper fills ->
                                     swap-aware $ P&L -> broker balance update
  dashboard.py + templates/            local read-only Flask dashboard (Section 22)
  main.py                             CLI: backtest / walk-forward / signal-once /
                                      paper / dashboard
```

## 3. Technology stack

- **Python 3.11**, pandas/numpy for data and vectorized indicators (no
  heavyweight backtesting framework -- a custom engine was needed anyway
  to get bid/ask-aware fills exactly right).
- **SQLite** via the standard library (`fx_engine/db.py`) -- appropriate
  for personal, single-user use; append-only for strategy versions and
  backtests so history is never silently overwritten.
- **requests** for the Telegram Bot API (no heavier Telegram SDK needed).
- **MetaTrader5** (official package, Windows/Wine only) for the real
  Exness data/broker adapter.
- **Flask**, server-rendered Jinja templates, no build step or JS
  framework, for the local dashboard (`fx_engine/dashboard.py`) -- no CDN
  assets either, everything renders from inline CSS and the local DB.

## 4. Data sources

| Provider | Real data? | Needs | Use for |
|---|---|---|---|
| `synthetic` | No -- seeded regime-switching random walk | nothing | proving the pipeline works, in any environment |
| `yahoo` | Yes | normal outbound internet | quick, free strategy research/backtesting |
| `mt5` | Yes, Exness's own quotes | Windows/Wine + MT5 terminal + Exness login | paper trading and any eventual live use |

## 5. Broker integration approach

`fx_engine/broker/base.py` defines `BrokerAdapter`; `exness_mt5.py`
implements it against a running MT5 terminal. **No adapter in this
codebase places real orders** -- `place_order()` is not overridden by
either adapter and always raises. The system runs in **signal-only /
paper mode** permanently unless you deliberately write and review new code
to change that.

## 6. Strategy modules

Seven independent, explicitly-specified strategies (full spec for each in
`docs/STRATEGIES.md`, generated directly from the code so it can't drift):
trend pullback, momentum (MACD+RSI), Donchian breakout, Bollinger/RSI mean
reversion, price-action break-of-structure, volatility-squeeze breakout,
and a multi-timeframe trend-agreement confirmation overlay. Each is gated
to the market regimes it's designed for (`regime.STRATEGY_ALLOWED_REGIMES`)
and returns a structured `StrategyResult`, never a bare buy/sell.

## 7. Database schema

See `fx_engine/db.py` `SCHEMA`. Tables: `strategy_versions`, `backtests`,
`signals`, `signal_outcomes`, `paper_trades` (now carrying real `lots` /
`risk_amount` / `pnl_amount` in account-currency terms, not just
R-multiples), `system_health`. Scoped down from the original plan's full
multi-user schema (Users, per-strategy version-controlled history as
separate rows, SpreadHistory, MarketRegimes, NewsEvents as persisted
tables) to what a single personal user actually needs; strategy specs and
backtests remain append-only/versioned. The dashboard (`fx_engine/dashboard.py`)
reads this database directly and writes nothing to it.

## 8. Backtesting design

`BacktestEngine` (see `docs/BACKTESTING.md` for full detail) walks bar by
bar with **zero lookahead by construction** -- every strategy call only
ever sees data up to and including "now" -- and fills through
`SpreadModel` so BUY pays the ask and sells pay the bid, both on entry and
exit. Indicators are precomputed once per series (`features.py`) rather
than recomputed on every growing window, which is what makes a multi-year
backtest finish in seconds instead of not finishing at all.

## 9. Anti-overfitting design

`backtest/walk_forward.py` partitions trades into N sequential folds plus
a final holdout slice never used to shape any decision. A strategy is
only called `CONSISTENT` if expectancy holds up across folds AND in the
untouched holdout; otherwise it's `REJECTED`, `OVERFIT_OR_DEGRADED`,
`WEAK_OUT_OF_SAMPLE`, or `INCONCLUSIVE` -- verdicts intended to be taken
at face value, not argued with.

## 10. Signal generation design

`signal_engine.py`: daily-loss-breaker check (section 26 -- the very
first gate, before any strategy even runs) -> regime classification ->
all 7 strategies vote -> `StrategyEnsemble` combines votes weighted by
each strategy's latest validated backtest expectancy (equal weight + an
explicit "UNVALIDATED" flag until a backtest exists) -> spread-vs-stop
and risk/reward-after-costs gates (section 7 of the original brief) ->
0-100 quality score -> position size (`fx_engine/position_sizing.py`,
using the connected broker's real equity or a configured fallback) -> NO
TRADE unless every gate clears. Section 26's principle is load-bearing
here, not decorative: most evaluations should end in NO TRADE.

## 11. Telegram architecture

`telegram_bot.py` separates message formatting (`SignalMessage.format()`,
testable without any credentials) from sending (`TelegramNotifier`, needs
`TELEGRAM_BOT_TOKEN`/`TELEGRAM_CHAT_ID`). Duplicate signals for the same
pair/direction within one timeframe window are suppressed via
`Database.recent_signal_exists()`.

## 12. 24/7 deployment architecture

See `docs/DEPLOYMENT.md`. Short version: `python -m fx_engine.main paper`
run under systemd (Linux, `yahoo`/no-broker mode) or as a scheduled task
on a small Windows VPS (for the real MT5/Exness adapter), with
`system_health` rows as the monitoring signal and process-manager restart
as the recovery mechanism -- no custom orchestration layer was built for
this personal-scale deployment.

## 13. Security

No credentials are hard-coded anywhere in this repository; everything
comes from environment variables via `fx_engine/config.py`, loaded from a
local `.env` that is git-ignored. See `docs/SECURITY.md`.

## 14. Testing strategy

The pipeline was validated end-to-end against synthetic data during
development (see commit history / `docs/PERFORMANCE.md`): indicators,
regime detection, all 7 strategies, the backtest engine, walk-forward
validation, the ensemble, the signal scorer, position sizing (direct,
inverse, and cross-pair pip-value math checked against hand
calculations), the daily loss circuit breaker, swap/rollover cost
(rollover-crossing counts checked against hand-calculated dates,
including the triple-Wednesday case), SQLite persistence, Telegram
message formatting, and the local dashboard (every route, plus actual
rendered screenshots in light and dark mode via a headless browser) were
all exercised together and produced real, varying, non-fabricated
numbers -- 73 automated tests, all passing. This is evidence the
*pipeline* is correct -- it is explicitly NOT evidence that any strategy
has a real edge, since synthetic data has no real market structure to
have an edge against. Real validation starts once real data (`yahoo` or
`mt5`) is plugged in on a machine with normal internet access.

## 15. Risks and limitations

See `docs/LIMITATIONS.md` for the full, honest list: synthetic-data-only
validation so far, approximated (not real historical) spread, no live
economic calendar connected, swap rates and margin simulation still need
real account figures filled in, no correlated-exposure cap across
concurrent paper positions, and the hard MT5/Windows dependency for
anything touching real Exness data or execution.
