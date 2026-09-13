# Dashboard

A small local, read-only Flask app over the SQLite database (Section 22
of the original plan). No external network calls, no CDN assets --
everything needed to render is either inline CSS or comes from the local
DB, so it works exactly the same whether you have internet access or not.

## Running it

```bash
python -m fx_engine.main dashboard --port 8080
```

Then open `http://127.0.0.1:8080/`. It binds to `127.0.0.1` by default --
see `docs/SECURITY.md` before changing `--host` to anything wider.

## Pages

- **Overview** (`/`): headline stats (cumulative paper P&L, open/closed
  trade counts), the 10 most recent signals, currently open paper trades,
  and the 10 most recent system-health entries.
- **Signals** (`/signals`): every signal ever generated, most recent
  first, with its eventual outcome once resolved.
- **Strategies** (`/strategies`): the latest saved backtest per
  (strategy, pair) -- trades, win rate, expectancy, profit factor, max
  drawdown, and the walk-forward verdict as a color-coded badge. Empty
  until you run `backtest --save` or `walk-forward --save`.
- **Paper Trades** (`/paper-trades`): the full simulated trade log plus a
  server-rendered SVG sparkline of cumulative realized P&L -- no
  JavaScript charting library, just a `<polyline>` computed from
  `Database.paper_equity_curve()`.
- **System Health** (`/health`): every row `paper_trading.py` has logged
  (`OK` per successful cycle, `ERROR` with the exception message on
  failure) -- the same table `docs/DEPLOYMENT.md`'s monitoring section
  queries directly with `sqlite3`.

## Design notes

- Read-only: no route writes to the database or talks to a broker.
- Theme-aware: uses `prefers-color-scheme` for light/dark, no toggle
  needed (verified by rendering both in this project's own dev sandbox
  with a headless browser, light and dark, before shipping -- see
  `docs/PERFORMANCE.md`).
- Every page degrades gracefully on an empty database (a brand new
  install's state) -- verified in `tests/test_dashboard.py`.
- No auth. This is meant for `127.0.0.1` on your own machine. If you ever
  want to check it from another device, put it behind your own
  authenticated reverse proxy rather than exposing it directly -- nothing
  here implements login/sessions.
