# FX Signal Engine

A personal-use Forex research and signal-generation platform. It does
**not** guarantee profitable trades. It exists to:

1. Turn established trading concepts into explicit, backtestable rules.
2. Backtest them with realistic bid/ask spread costs.
3. Validate them with walk-forward + untouched holdout testing (so a
   strategy that only looked good on one lucky stretch of history gets
   rejected, not trusted).
4. Combine several independent strategies into one signal, weighted by
   each strategy's own validated track record.
5. Send the result to Telegram -- clearly labeled as a research signal,
   never as a guarantee.

Read `PROJECT_PLAN.md` first for the full architecture and the
corrections made to the original brief (most importantly: how Exness is
actually accessed). Read `docs/LIMITATIONS.md` before trusting any number
this produces.

## Quickstart

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env   # defaults to FX_DATA_PROVIDER=synthetic, works with no setup

# Backtest every strategy on EUR/USD with synthetic data (works anywhere, proves the pipeline)
python -m fx_engine.main backtest --pair EURUSD --strategy all --provider synthetic --save

# Walk-forward / holdout validation for one strategy
python -m fx_engine.main walk-forward --pair EURUSD --strategy price_action --provider synthetic --save

# Evaluate one pair right now and print the signal (or NO TRADE + why) --
# includes an actual position size (lots, dollar risk, required margin)
python -m fx_engine.main signal-once --pair EURUSD --provider synthetic

# Run one paper-trading cycle across the default 9-pair universe
python -m fx_engine.main paper --once --provider synthetic

# Browse signals, strategy performance, paper trades and system health
python -m fx_engine.main dashboard --port 8080
```

None of the above needs a Telegram bot, an Exness account, or real
internet access -- `synthetic` data lets you exercise the entire pipeline
immediately. To use it for real:

- **Real historical data for research**: set `FX_DATA_PROVIDER=yahoo` in
  `.env` and run from a machine with normal internet access (this dev
  sandbox specifically does not have it -- see `PROJECT_PLAN.md` section 1).
- **Real Exness data / eventual paper trading against real quotes**: see
  `docs/EXNESS_INTEGRATION.md` -- this needs a Windows (or Wine) machine
  running the MT5 terminal, logged into your Exness account.
- **Telegram alerts**: see `docs/TELEGRAM.md`, then add `--telegram` to
  `signal-once` / `paper`.

## What this does NOT do

- Does not place real trades. `broker/*.place_order()` always raises --
  see `docs/SECURITY.md`.
- Does not claim a signal score is a win probability -- it's a
  transparency tool, see `fx_engine/scoring.py`.
- Does not run 24/7 by itself in this sandbox -- see `docs/DEPLOYMENT.md`
  for what actually running this continuously requires.

## Documentation map

| Doc | Covers |
|---|---|
| `PROJECT_PLAN.md` | Full architecture, corrections to the original brief |
| `docs/ARCHITECTURE.md` | Module-by-module reference |
| `docs/STRATEGIES.md` | Every strategy's full spec (auto-generated from code) |
| `docs/BACKTESTING.md` | Spread/cost modeling, no-lookahead design |
| `docs/RISK_MANAGEMENT.md` | Position sizing, daily loss limits, swap cost |
| `docs/EXNESS_INTEGRATION.md` | The MT5 reality check, setup steps |
| `docs/TELEGRAM.md` | Bot setup |
| `docs/DASHBOARD.md` | The local read-only dashboard |
| `docs/DEPLOYMENT.md` | Running this continuously |
| `docs/SECURITY.md` | Credentials, signal-only-by-design |
| `docs/LIMITATIONS.md` | The honest list of what's approximated or missing |
| `docs/PERFORMANCE.md` | How pipeline correctness was verified so far |
