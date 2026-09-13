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

## Yahoo integration is hardened but unverified against the live endpoint

`YahooFinanceProvider` (`fx_engine/data/providers.py`) now handles the
things that would otherwise bite you the first time you actually used it:
Yahoo's undocumented per-interval lookback limits (~60 days for 15m bars,
~730 days for 60m bars) are respected by automatically splitting a
multi-year H1/H4 request into sub-limit chunks and concatenating them,
transient failures (connection errors, 429s, 5xx) retry with backoff, and
a persistent failure raises a clear `ConnectionError` that explicitly
names "network policy" as a possible cause rather than looking like a
code bug. All of this was verified with `tests/test_yahoo_provider.py`
against a realistic **mocked** Yahoo chart-API response, matching the
real documented JSON schema -- because this sandbox's outbound access to
`query1.finance.yahoo.com` is blocked at the network-policy level
(confirmed: `CONNECT tunnel failed, response 403`, not a timeout or a
transient error), the actual live endpoint has never been hit from here.
Run `python -m fx_engine.main backtest --provider yahoo ...` from a
machine with normal internet access to complete that verification --
if Yahoo has changed their response schema since this was written, that
run is where you'd find out.

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

## No dashboard

Section 22 of the original brief asked for a web dashboard. This build
prioritized proving the core pipeline (data -> strategies -> backtest ->
ensemble -> signal -> Telegram) end-to-end first, per the "vertical slice
before breadth" plan in `PROJECT_PLAN.md`. Today, `system_health`,
`signals`, `backtests`, and `paper_trades` SQLite tables plus direct SQL
queries or the CLI are the inspection surface. A dashboard is a
reasonable next addition once the underlying signals have a track record
worth visualizing.

## Single position per (pair, strategy) in the backtester

`BacktestEngine` does not model concurrent open positions across
strategies or portfolio-level position sizing -- each strategy's backtest
on a given pair opens and fully closes one trade at a time. Realistic for
evaluating one strategy in isolation; not yet a full portfolio simulator.

## No swap/rollover cost modeling

`config.SWAP_PER_LOT_PER_DAY` exists as a knob but defaults to 0 and
isn't yet subtracted anywhere in the backtest engine. For any strategy
that holds positions overnight (most of these close same-session, per
each strategy's `max_holding_bars`, but not strictly guaranteed), real
swap costs should be added to the P&L calculation before trusting
multi-day expectancy numbers.

## No margin/leverage simulation

Metrics are computed in R-multiples (risk-normalized), which sidesteps
needing an account-equity simulation for backtesting logic, but that also
means max drawdown in R-terms is not the same thing as margin-call risk
at real leverage. Position-sizing decisions still need your own separate
leverage/margin math against your actual account.

## Daily loss circuit breaker not yet auto-enforced

`config.MAX_DAILY_LOSS_PCT` is defined and documented
(`docs/RISK_MANAGEMENT.md`) but `paper_trading.py` doesn't yet aggregate
same-day P&L to automatically pause new signals once the cap is hit --
apply it manually until that's wired up.

## MT5/Exness adapter cannot be exercised in this development environment

Written and reviewed here, but this sandbox has no Windows and no MT5
terminal to actually run it against. See `docs/EXNESS_INTEGRATION.md` for
exactly what running it for real requires, and test it on a real machine
against a demo Exness account before trusting it with a live one.
