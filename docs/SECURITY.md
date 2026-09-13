# Security

## Credentials

Nothing in this codebase hard-codes a secret. Every credential --
`EXNESS_MT5_LOGIN`, `EXNESS_MT5_PASSWORD`, `EXNESS_MT5_SERVER`,
`TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID` -- is read from the environment
via `fx_engine/config.py`, sourced from a local `.env` file. `.env` is
listed in `.gitignore`; only `.env.example` (with empty values) is
committed. Never commit a real `.env`.

## This system does not place real trades

`fx_engine/broker/base.py`'s `BrokerAdapter.place_order()` always raises
`NotImplementedError` with an explanation. Neither `PaperBrokerAdapter`
nor `ExnessMT5Adapter` overrides it. Nothing in `signal_engine.py` or
`paper_trading.py` calls `place_order()` -- there is no code path from a
generated signal to a real order anywhere in this repository. If you
eventually want that, it is a deliberate, separate piece of code to write
and review on its own, not a flag to flip.

## Database

SQLite is a local file (`fx_engine.sqlite3` by default, path configurable
via `FX_DB_PATH`, also git-ignored). It contains your signal history and
paper-trade log, not credentials. No network exposure by default -- if you
build the dashboard mentioned in `docs/LIMITATIONS.md`, secure it before
exposing it beyond localhost.

## Third-party surface

- Telegram Bot API: only receives the formatted signal text you already
  see in your terminal/Telegram -- no credentials or account data beyond
  what's in the message itself.
- Yahoo Finance (optional `yahoo` provider): only sends pair/timeframe/date
  range in the request; no credentials involved (it's an unauthenticated
  public endpoint).
- MetaTrader5 / Exness: credentials go directly from your `.env` to the
  local MT5 terminal via the official package, not over the network to
  any third party this codebase controls.
