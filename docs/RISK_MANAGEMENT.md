# Risk Management

These are defaults in `fx_engine/config.py`, overridable via `.env`. None
of this is enforced by an order-placement layer (there isn't one -- see
`docs/SECURITY.md`); it governs what the *signal* recommends, what the
position size calculator computes, and what the paper-trading loop
actually simulates and enforces.

## Per-trade risk and position sizing

`RISK_PER_TRADE_PCT` (default 0.5%): the fraction of account equity a
single trade's stop-loss distance should represent. `fx_engine/position_sizing.py`
turns this into an actual lot size -- every signal includes a `POSITION SIZE`
line (lots, dollar risk, required margin), computed correctly for direct
pairs (EURUSD on a USD account), inverse pairs (USDJPY on a USD account),
and cross pairs (EURJPY on a USD account, which needs a USDJPY conversion
rate -- resolved automatically from a live quote). See
`calculate_position_size()`'s docstring for the exact math, and
`tests/test_position_sizing.py` for it verified against hand calculations.
Equity comes from a connected broker's real account (`SignalEngine.current_account()`)
when one is set, otherwise from `ACCOUNT_STARTING_BALANCE` -- see `.env.example`.

## Daily loss circuit breaker

`MAX_DAILY_LOSS_PCT` (default 2.0%) is auto-enforced end to end:
`Database.daily_loss_limit_breached()` sums today's realized dollar P&L
across closed paper trades (`pnl_amount`, computed from each trade's real
`risk_amount`) and `SignalEngine.evaluate_pair()` refuses to evaluate any
new signal for the rest of the UTC day once the limit is hit -- verified
in `tests/test_daily_loss_and_paper.py`. It only counts *realized* losses
(a trade that's closed with a loss), not unrealized open drawdown, since
paper trades resolve on their own SL/TP rather than a continuous
mark-to-market check.

## Minimum risk/reward after costs

`MIN_RR_AFTER_COSTS` (default 1.5): see `docs/BACKTESTING.md` for exactly
how spread is subtracted from reward and added to risk before this check.
A setup that looks like 1:2 on a chart but doesn't clear 1.5 after real
costs is rejected before it ever reaches Telegram.

## Spread-to-stop ratio

`MAX_SPREAD_TO_STOP_RATIO` (default 30%): if the current spread is more
than this fraction of the stop distance, the setup is rejected regardless
of nominal R:R -- a tight stop with a fat spread is a bad trade even if
the reward target is far away.

## Max holding time

`MAX_HOLDING_BARS` (default 60): a trade idea that hasn't hit its stop or
target after this many bars is closed at market. Prevents a stale signal
from sitting open indefinitely on a pair that stopped moving.

## Swap/rollover cost

Positions held through the daily rollover accrue swap -- see
`fx_engine/swap.py` and `docs/LIMITATIONS.md` for the mechanism and why
the rates default to zero (`SWAP_LONG_PIPS_PER_NIGHT` / `SWAP_SHORT_PIPS_PER_NIGHT`
per pair). It's already subtracted from both backtest and paper-trade P&L
once you fill in real numbers from your Exness account.

## The numbers that actually mattered in the source material

Buried in the YouTube breakdown this project started from was one
genuinely sound piece of risk discipline, worth stating plainly since it's
echoed in the defaults above: risk a small, fixed percentage per trade
(0.25-1%), cap the day's total loss at ~2%, and size up only with profit
already banked that session -- never in reaction to a losing streak.
