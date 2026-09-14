# Running This Continuously

## Two deployment shapes, depending on data source

**Signal research only, no real broker connection** (`FX_DATA_PROVIDER=yahoo`):
any always-on Linux box works. This is the easy path if you just want
Telegram alerts based on real Yahoo-sourced prices without ever touching
MT5.

**Real Exness data / eventual paper trading against real quotes**
(`FX_DATA_PROVIDER=mt5`): needs Windows (a small Windows VPS is the
common, well-trodden choice) or Linux+Wine running an active MT5 terminal
logged into your Exness account -- see `docs/EXNESS_INTEGRATION.md`. The
terminal has to actually be running and logged in at all times; this is
the real operational cost of using Exness's data/execution, not a detail
to skip.

## Zero-install option: GitHub Actions (no server, no local machine)

If you don't have an always-on machine at all -- no VPS, no PC that can
run Python -- `.github/workflows/paper-trading.yml` runs the signal-only
(`FX_DATA_PROVIDER=yahoo`) paper-trading loop for you, for free, on
GitHub's own infrastructure:

- Runs one evaluation cycle (`python -m fx_engine.main paper --once`)
  every 4 hours on a GitHub-hosted runner, on a schedule (`cron`), plus
  a manual "Run workflow" button on the Actions tab any time.
- Commits the resulting `data/paper_trading.sqlite3` back into the repo
  after each run, so signal/paper-trade history accumulates just like it
  would on a long-running local loop -- there's simply no process to keep
  alive yourself.
- Sends Telegram alerts automatically once you add `TELEGRAM_BOT_TOKEN`
  and `TELEGRAM_CHAT_ID` as repo secrets (Settings -> Secrets and
  variables -> Actions -> New repository secret; see
  `docs/TELEGRAM.md` for creating the bot itself). Without them, it still
  runs and records results -- they're just visible only in the Actions
  logs and the committed database, not pushed to you.
- A second workflow, `.github/workflows/telegram-listener.yml`, checks
  every 5 minutes for a message you sent the bot and replies with a fresh
  analysis on demand -- see "On-demand analysis: just message the bot" in
  `docs/TELEGRAM.md`. It needs the same two secrets and otherwise does
  nothing (no API calls at all) until they're set.
- A third workflow, `.github/workflows/paper-trading-h1.yml`, runs the same
  idea on the 1-hour timeframe instead of 4-hour -- hourly instead of every
  4 hours, since that's how often a new H1 candle actually closes. Signals
  and trades from this track are tagged `timeframe=H1` in the database and
  labeled "(H1)" in Telegram alerts, kept fully separate from the H4
  track's history and duplicate-detection (`fx_engine/db.py`) -- they're
  different evidence, not the same signal counted twice. All three
  scheduled workflows share one concurrency group since they all commit to
  the same `data/paper_trading.sqlite3`.
- `.github/workflows/validate.yml` is a fourth, on-demand-only workflow
  (no schedule) for checking a strategy/pair/timeframe combination against
  real data BEFORE trusting a new paper-trading track: it backtests every
  strategy and then walk-forward validates each one individually, printing
  results to the run's log without committing anything. Run it from the
  Actions tab with your own pair/timeframe/days-back inputs any time you
  want real evidence before turning something new on.
- GitHub only fires `schedule` triggers from the workflow file as it
  exists on the repository's default branch -- if you rename or change
  the default branch, this workflow needs to live there too.

To see what it's doing: the repo's **Actions** tab lists every run, its
logs, and whether it succeeded. To see the accumulated results in the
dashboard instead of raw logs, point the dashboard at the committed file:

```bash
FX_DB_PATH=data/paper_trading.sqlite3 python -m fx_engine.main dashboard
```

This is the `yahoo`-only path (Section "Two deployment shapes" above) --
it cannot do the `mt5`/Exness path, since that needs a live, logged-in
Windows MT5 terminal, which a GitHub-hosted runner can't provide.

## Running the loop

```bash
python -m fx_engine.main paper --pairs EURUSD,GBPUSD,USDJPY --interval 900 --telegram
```

`--interval` is in seconds between evaluation cycles; 900 (15 min) is a
reasonable default for H4-timeframe strategies -- no need to poll faster
than your strategies' timeframe warrants (this mirrors the original
brief's "don't burn API calls on every tick" principle, applied to CPU
cycles here since there's no paid API in the loop, but the same logic:
match cadence to the strategies' actual timeframe).

## Process supervision (Linux)

A systemd unit is the simplest reliable way to get auto-restart-on-crash
and auto-start-on-boot:

```ini
# /etc/systemd/system/fx-signal-engine.service
[Unit]
Description=FX Signal Engine paper trading loop
After=network-online.target

[Service]
WorkingDirectory=/opt/fx-analyst
EnvironmentFile=/opt/fx-analyst/.env
ExecStart=/opt/fx-analyst/.venv/bin/python -m fx_engine.main paper --telegram
Restart=always
RestartSec=30

[Install]
WantedBy=multi-user.target
```

```bash
sudo systemctl enable --now fx-signal-engine
journalctl -u fx-signal-engine -f
```

## Process supervision (Windows, for the MT5 path)

Windows Task Scheduler, set to run at startup and configured to restart
on failure, is the equivalent. Ensure the MT5 terminal itself is also set
to auto-launch and auto-login (Tools -> Options in the terminal) so a
reboot doesn't leave the terminal logged out while the Python loop keeps
retrying.

## Monitoring

`paper_trading.PaperTradingLoop` writes a `system_health` row every cycle
(`OK` on success, `ERROR` with the exception message on failure) and
never lets one pair's exception kill the whole loop. Query recent health:

```bash
sqlite3 fx_engine.sqlite3 "SELECT * FROM system_health ORDER BY id DESC LIMIT 20;"
```

There's no dashboard yet (see `docs/LIMITATIONS.md`) -- this table plus
your Telegram channel are the monitoring surface today.

## What "recover after crashes" actually means here

The process supervisor (systemd/Task Scheduler) restarts the Python
process; the SQLite database persists across restarts so signal history
and open paper trades survive; `PaperTradingLoop.run_once()`'s per-pair
try/except means one bad pair (e.g. a data fetch failure) doesn't take
down evaluation of the other pairs in the same cycle.
