"""On-demand Telegram command handling (short-poll, not a persistent bot).

Lets the user request a fresh analysis any time by simply messaging their
bot -- no dashboard, no GitHub UI, no CLI needed on their end. Designed to
run from a short-lived scheduled job (see .github/workflows/telegram-listener.yml,
every 5 minutes -- the fastest schedule GitHub Actions allows) rather than a
long-running process: each invocation asks Telegram for messages since the
last processed update_id (persisted in the `app_state` table, so a message
is never re-processed on the next scheduled check) and, if the authorized
chat sent anything since then, runs a fresh evaluate_pair() over every
requested pair and replies with the result -- NO TRADE reasons included,
not just actionable signals, since "why isn't it trading" is itself part
of what was asked for.

Only messages from the configured TELEGRAM_CHAT_ID are ever acted on;
anything else (a stranger who finds the bot, a non-text update) is
acknowledged (so it's never re-fetched) but otherwise ignored.
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone

from fx_engine import config
from fx_engine.data.models import Timeframe
from fx_engine.signal_engine import NoTradeReason, SignalEngine
from fx_engine.telegram_bot import TelegramNotifier

logger = logging.getLogger("fx_engine.telegram_listener")

STATE_KEY = "telegram_last_update_id"


def _authorized_texts(updates: list[dict], chat_id: str) -> list[str]:
    texts = []
    seen_chat_ids = set()
    for u in updates:
        msg = u.get("message") or u.get("edited_message") or {}
        seen_id = str(msg.get("chat", {}).get("id", ""))
        seen_chat_ids.add(seen_id)
        if seen_id != str(chat_id):
            continue
        text = (msg.get("text") or "").strip()
        if text:
            texts.append(text)
    if not texts and seen_chat_ids:
        # Chat IDs aren't sensitive (unlike the bot token) -- logging them is
        # what makes a misconfigured TELEGRAM_CHAT_ID diagnosable at all,
        # rather than a silent "ignored" with no way to tell why.
        logger.info("saw message(s) from chat id(s) %s, configured TELEGRAM_CHAT_ID is %r -- no match",
                     sorted(seen_chat_ids), chat_id)
    return texts


def _pairs_mentioned(text: str, pairs: list[str]) -> list[str]:
    upper = text.upper()
    matched = [p for p in pairs if p in upper]
    return matched or list(pairs)


def check_and_respond(
    engine: SignalEngine,
    notifier: TelegramNotifier,
    pairs: list[str] | None = None,
    timeframe: Timeframe = Timeframe.H4,
) -> str:
    """Checks for a new Telegram message and, if the authorized chat sent
    one since the last check, replies with a fresh analysis. Returns a
    short human-readable summary of what happened, for logs/printing.
    """
    pairs = pairs or config.PAIRS
    last_id = engine.db.get_state(STATE_KEY)
    offset = int(last_id) + 1 if last_id is not None else None
    updates = notifier.get_updates(offset=offset)
    if not updates:
        return "no new Telegram messages"

    max_update_id = max(u["update_id"] for u in updates)
    engine.db.set_state(STATE_KEY, str(max_update_id))

    texts = _authorized_texts(updates, notifier.chat_id)
    if not texts:
        return f"{len(updates)} update(s) seen, none from the authorized chat -- ignored"

    target_pairs = _pairs_mentioned(texts[-1], pairs)
    lines = [f"\U0001F4CA ON-DEMAND ANALYSIS ({datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')})", ""]
    for pair in target_pairs:
        try:
            result = engine.evaluate_pair(pair, timeframe)
        except Exception as exc:
            logger.exception("error evaluating %s for on-demand request", pair)
            lines.append(f"{pair}: ERROR -- {exc}")
            continue
        if isinstance(result, NoTradeReason):
            lines.append(f"{pair}: NO TRADE -- {result.reason}")
        else:
            _, msg = result
            lines.append(f"{pair}: {msg.direction.value} signal, score {msg.score.score:.0f}/100 "
                         f"(full details sent separately above)")
    notifier.send("\n".join(lines))
    return f"replied to on-demand request for {len(target_pairs)} pair(s)"
