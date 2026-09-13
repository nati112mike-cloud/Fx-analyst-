# Limitations -- Read This Before Trusting Any Number

Honesty about what's approximated or missing, in one place, per the
original brief's own principle: evidence over hype.

## Spread is approximated, not real historical Exness data

`fx_engine/costs.py: SpreadModel` estimates spread from a per-pair
"typical" baseline table, widened around the rollover window and during
high-volatility regimes. This is a reasonable, clearly-labeled
approximation for backtesting logic and for demonstrating the pipeline --
it is **not** Exness's actual historical bid/ask. Once the MT5 adapter is
connected (`docs/EXNESS_INTEGRATION.md`), real spread should replace this
for any backtest whose absolute numbers you intend to act on.

## Yahoo integration: now verified against the live endpoint, one real bug found and fixed

`YahooFinanceProvider` (`fx_engine/data/providers.py`) was first built and
tested only against a mocked Yahoo response, since this sandbox's own
outbound access to `query1.finance.yahoo.com` is blocked at the network-policy
level. It has since actually been run against the real endpoint -- from Google
Colab (a free, no-install way to run this: upload the project and a
ready-made notebook, no Python setup needed on your own machine) -- and
that run surfaced a real bug: the documented
"~730 days is Yahoo's max lookback for 60-minute bars" figure (a commonly
cited number) is wrong, or has gone stale; Yahoo returned a 422
Unprocessable Entity for a 728-day request. Worse, the original retry
logic treated this as a transient failure and retried the *identical*
request four times, which fails identically four times and wastes time
for nothing.

Fixed: a 400/422 response is no longer retried -- it's treated as "this
specific date range was rejected," and `get_ohlc()` bisects the range in
half and retries each half, recursively, until Yahoo accepts each piece.
This makes the provider self-correcting against whatever Yahoo's real
current limit actually is, rather than hard-coding a number that can
silently go stale again. Verified in `tests/test_yahoo_provider.py`
(bisection recovers and produces a complete, gap-free, duplicate-free
series; a range that's rejected no matter how far it's split terminates
with a clear error rather than hanging or retrying forever).

Genuinely unverified still: chunking/bisection across a true multi-year
request against the live endpoint (the Colab run that found this bug used
a shorter range), and daily-bar (`1d`) requests, which weren't exercised
live yet either.

## Synthetic data has no real market structure

`fx_engine/data/providers.py: SyntheticDataProvider` produces a seeded,
regime-switching random walk for pipeline development and testing. Any
backtest result, walk-forward verdict, or strategy expectancy computed
against synthetic data is proof the *code* works, not evidence a strategy
has a real edge -- there is no real trend persistence, no real news
reaction, no real liquidity structure for a strategy to actually exploit
or fail against. Real validation starts only once `yahoo` or `mt5` data is
in use, from an environment with normal internet access (this sandbox does
not have it -- see `PROJECT_PLAN.md` section 1).

## No live economic calendar

`fx_engine/news.py: NewsFilter` is a real, usable interface (CSV-loadable,
computes a risk penalty correctly) but ships with `calendar_loaded=False`
by default -- no live feed is wired up, deliberately, rather than
fabricating one. Every signal's news-risk component is neutral until you
either load a CSV export from a real calendar source yourself
(`NewsFilter.load_from_csv`) or extend this module with a real feed.

## Dashboard exists, but is read-only and local

`fx_engine/dashboard.py` (`python -m fx_engine.main dashboard`) covers
Section 22: overview, signals, strategy performance, paper trades with an
equity curve, and system health, all rendered from the local SQLite DB
with zero external network calls (no CDN assets either -- verified by
actually running the Flask server and screenshotting every page, in both
light and dark mode, in this sandbox; see `docs/PERFORMANCE.md`). It does
not write to the database, does not talk to a broker, and binds to
127.0.0.1 by default -- see `docs/SECURITY.md` before exposing it wider.
There's no auth layer; don't put this on a public interface as-is.

## Backtester is single-strategy; the paper-trading loop is the portfolio view

`BacktestEngine` deliberately opens one trade at a time for a single
(pair, strategy) combination -- that's the right scope for asking "does
this strategy have an edge," which is what backtesting is for, and
mixing in other strategies/pairs would muddy that question. Portfolio-
level behavior -- multiple concurrent positions across different pairs
and strategies, sharing one account balance -- already exists one layer
up, in `paper_trading.PaperTradingLoop`: `Database.open_paper_trades()`
naturally holds many simultaneous rows, and a shared `PaperBrokerAdapter`
balance is updated as each one closes (see `docs/RISK_MANAGEMENT.md`).
What's still missing at the portfolio layer: correlated-exposure limits
(e.g. capping total risk across multiple JPY pairs open at once) --
nothing currently prevents every pair from independently risking its own
`RISK_PER_TRADE_PCT` at the same time, which compounds if several are
correlated.

## Swap/rollover cost modeling exists, real rates still need to be filled in

`fx_engine/swap.py` implements the actual mechanism (pips per lot per
night, applied at the 21:00 UTC rollover, tripled on Wednesday for the
weekend) and it's wired into both `BacktestEngine.Trade.pnl_price()` and
`paper_trading.py`'s trade resolution -- verified with hand-calculated
rollover-crossing counts in `tests/test_swap.py`. The rates themselves
(`config.SWAP_LONG_PIPS_PER_NIGHT` / `SWAP_SHORT_PIPS_PER_NIGHT`) are all
zero by default, because there is no honest non-zero default -- swap
rates are broker- and account-type specific and move with interest rates.
Fill in your own account's real figures (MT5 terminal -> Market Watch ->
right-click a symbol -> Specification) before trusting multi-day expectancy
numbers for strategies that hold positions overnight. Also unverified:
the exact rollover hour and triple-swap weekday convention against a real
Exness account (21:00 UTC / Wednesday is the common industry default used
here, not a confirmed Exness-specific figure).

## Margin/leverage: computed at signal time, not simulated over an equity path

`fx_engine/position_sizing.py` computes required margin for a given trade
correctly (notional / leverage) and it's shown in every signal, but there
is still no running margin-call simulation across a backtest's full
equity path -- max drawdown in R-multiples is not the same thing as
margin-call risk at real leverage over time. Size conservatively and
watch your real account's margin level directly until this exists.

## Daily loss circuit breaker: auto-enforced in paper trading, needs real data to mean much

`config.MAX_DAILY_LOSS_PCT` is now actually enforced:
`Database.daily_loss_limit_breached()` sums today's realized dollar P&L
(each paper trade's `pnl_amount`, computed from its real `risk_amount` via
`fx_engine/position_sizing.py`) and `SignalEngine.evaluate_pair()` returns
NO TRADE for the rest of the UTC day once it's breached -- verified in
`tests/test_daily_loss_and_paper.py`, including that it only triggers on
realized losses (not unrealized drawdown) and resets at the UTC day
boundary. Like everything else running on `synthetic`/unverified-`yahoo`
data, the breaker's real value only shows up once it's protecting an
account whose numbers come from genuine backtested/live performance.

## MT5/Exness adapter is hardened and offline-tested, not live-verified

`fx_engine/broker/exness_mt5.py` was rewritten to handle the failure
modes that would otherwise surface for the first time on a real account:
a terminal already logged into the wrong account (explicit `mt5.login()`
recovery rather than silently using the wrong account), transient
`initialize()`/`copy_rates_range()` failures (retry with backoff), an
unrecognized symbol (a clear error naming Exness's common `.raw`/`.pro`
suffix pattern instead of an opaque "no data"), and a stale tick (logged,
not treated as fatal, since a flat weekend market legitimately has an old
last tick). All of it is verified in `tests/test_exness_mt5.py` (15
tests) against a fake `MetaTrader5` module injected into `sys.modules` --
real control-flow logic, fake terminal.

What that testing cannot do: verify the real `MetaTrader5` package or a
real MT5 terminal, because the package has no Linux/macOS build at all
(confirmed against PyPI's JSON API: every release, every supported Python
version, is `win_amd64`-only -- see `docs/EXNESS_INTEGRATION.md`) and this
sandbox has no Windows. Test it on a real Windows machine against a demo
Exness account -- see `docs/EXNESS_INTEGRATION.md` -- before trusting it
with a live one.
