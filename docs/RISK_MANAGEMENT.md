# Risk Management

These are defaults in `fx_engine/config.py`, overridable via `.env`. None
of this is enforced by an order-placement layer (there isn't one -- see
`docs/SECURITY.md`); it governs what the *signal* recommends and what the
paper-trading loop simulates.

## Per-trade risk

`RISK_PER_TRADE_PCT` (default 0.5%): the fraction of account equity a
single trade's stop-loss distance should represent. This engine does not
compute lot size for you automatically (that requires knowing your actual
account currency/equity/leverage, which only exists once a broker adapter
with a real account is connected) -- treat the SL/TP levels in a signal
as price levels, and size the position yourself so that (entry - stop) x
position_size = RISK_PER_TRADE_PCT x account_equity.

## Daily loss circuit breaker

`MAX_DAILY_LOSS_PCT` (default 2.0%): stop taking new signals for the day
once cumulative realized loss reaches this. Not yet automatically
enforced inside `paper_trading.py` (it would need same-day P&L
aggregation wired to a "pause" state) -- see `docs/LIMITATIONS.md`. Apply
it manually until that's built.

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

## The numbers that actually mattered in the source material

Buried in the YouTube breakdown this project started from was one
genuinely sound piece of risk discipline, worth stating plainly since it's
echoed in the defaults above: risk a small, fixed percentage per trade
(0.25-1%), cap the day's total loss at ~2%, and size up only with profit
already banked that session -- never in reaction to a losing streak.
