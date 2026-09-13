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
