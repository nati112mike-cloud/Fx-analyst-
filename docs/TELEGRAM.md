# Telegram Bot Setup

1. In Telegram, message **@BotFather** -> `/newbot` -> follow the prompts.
   You'll get a token like `123456789:AAExampleTokenDoNotShareThis`.
2. Put it in `.env` as `TELEGRAM_BOT_TOKEN=...`.
3. Find your chat ID:
   - Message your new bot anything (it won't reply yet).
   - Visit `https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates` in a
     browser and read `message.chat.id` from the JSON response.
   - Put it in `.env` as `TELEGRAM_CHAT_ID=...`.
4. Test it:
   ```bash
   python -m fx_engine.main signal-once --pair EURUSD --telegram
   ```
   If nothing is configured, `TelegramNotifier.send()` raises a clear
   `RuntimeError` rather than silently doing nothing.

## Message format

See `fx_engine/telegram_bot.py: SignalMessage.format()`. Every message
includes: pair, direction, the 0-100 signal quality score (explicitly
labeled as not a win probability), how many of the 7 strategies agree and
which ones, entry zone, stop-loss, two take-profit levels, risk/reward
after costs, current spread, market regime, the primary strategy's
reasoning, and a disclaimer line. If any voting strategy has no backtest
on record yet, an extra "UNVALIDATED" warning is prepended -- see
`ensemble.EnsembleResult.unvalidated`.

## De-duplication

`Database.recent_signal_exists(pair, direction, minutes)` prevents the
same pair/direction from spamming multiple alerts within one timeframe
window. `paper_trading.PaperTradingLoop` calls the signal engine on each
poll cycle; the dedupe check is what keeps a persistent setup from
re-alerting every cycle.
