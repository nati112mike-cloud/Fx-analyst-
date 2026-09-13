"""Tests for the on-demand Telegram command handling
(fx_engine/telegram_listener.py): a message to the bot should trigger a
fresh analysis and a reply, exactly once per message, only for the
authorized chat, with the offset persisted so nothing is re-processed on
the next scheduled check.

    python -m unittest discover -s tests
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fx_engine.data.models import Timeframe
from fx_engine.db import Database
from fx_engine.signal_engine import NoTradeReason
from fx_engine.telegram_listener import check_and_respond


def _fresh_db() -> Database:
    fd = tempfile.NamedTemporaryFile(suffix=".sqlite3", delete=False)
    fd.close()
    db = Database(path=fd.name)
    db.init_schema()
    return db


class _FakeMessage:
    def __init__(self, direction, score):
        self.direction = direction
        self.score = _FakeScore(score)

    def format(self):
        return "formatted signal"


class _FakeScore:
    def __init__(self, score):
        self.score = score


class _FakeDirection:
    def __init__(self, value):
        self.value = value


class _FakeEngine:
    """Stands in for SignalEngine: same `.db` and `.evaluate_pair()` shape,
    with per-pair results the test controls directly, so this exercises
    check_and_respond's own logic without depending on strategy internals.
    """

    def __init__(self, db, results: dict):
        self.db = db
        self.results = results
        self.calls = []

    def evaluate_pair(self, pair, timeframe):
        self.calls.append(pair)
        return self.results[pair]


class _FakeNotifier:
    def __init__(self, chat_id, updates_batches):
        self.chat_id = chat_id
        self._batches = list(updates_batches)
        self.offsets_requested = []
        self.sent = []

    def get_updates(self, offset=None):
        self.offsets_requested.append(offset)
        return self._batches.pop(0) if self._batches else []

    def send(self, text):
        self.sent.append(text)
        return True


def _msg_update(update_id, chat_id, text):
    return {"update_id": update_id, "message": {"chat": {"id": chat_id}, "text": text}}


class TestCheckAndRespond(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def test_no_updates_returns_early_and_sends_nothing(self):
        notifier = _FakeNotifier(chat_id="111", updates_batches=[[]])
        engine = _FakeEngine(self.db, {})
        summary = check_and_respond(engine, notifier, pairs=["EURUSD"])
        self.assertEqual(summary, "no new Telegram messages")
        self.assertEqual(notifier.sent, [])
        self.assertEqual(engine.calls, [])

    def test_unauthorized_chat_is_ignored_but_offset_still_advances(self):
        updates = [_msg_update(5, chat_id="999", text="analyze")]
        notifier = _FakeNotifier(chat_id="111", updates_batches=[updates])
        engine = _FakeEngine(self.db, {})
        summary = check_and_respond(engine, notifier, pairs=["EURUSD"])
        self.assertIn("none from the authorized chat", summary)
        self.assertEqual(notifier.sent, [])
        self.assertEqual(engine.calls, [])
        self.assertEqual(self.db.get_state("telegram_last_update_id"), "5")

    def test_authorized_generic_message_analyzes_all_configured_pairs(self):
        updates = [_msg_update(10, chat_id="111", text="what's happening")]
        notifier = _FakeNotifier(chat_id="111", updates_batches=[updates])
        results = {
            "EURUSD": NoTradeReason(pair="EURUSD", reason="no actionable agreement"),
            "GBPUSD": (1, _FakeMessage(_FakeDirection("BUY"), 72)),
        }
        engine = _FakeEngine(self.db, results)
        summary = check_and_respond(engine, notifier, pairs=["EURUSD", "GBPUSD"])
        self.assertEqual(sorted(engine.calls), ["EURUSD", "GBPUSD"])
        self.assertEqual(len(notifier.sent), 1)
        reply = notifier.sent[0]
        self.assertIn("EURUSD: NO TRADE -- no actionable agreement", reply)
        self.assertIn("GBPUSD: BUY signal, score 72/100", reply)
        self.assertIn("2 pair(s)", summary)

    def test_message_naming_a_pair_only_analyzes_that_pair(self):
        updates = [_msg_update(20, chat_id="111", text="any read on eurusd right now?")]
        notifier = _FakeNotifier(chat_id="111", updates_batches=[updates])
        results = {"EURUSD": NoTradeReason(pair="EURUSD", reason="score below floor")}
        engine = _FakeEngine(self.db, results)
        check_and_respond(engine, notifier, pairs=["EURUSD", "GBPUSD", "USDJPY"])
        self.assertEqual(engine.calls, ["EURUSD"])

    def test_offset_persists_so_same_updates_are_not_reprocessed(self):
        updates = [_msg_update(30, chat_id="111", text="analyze")]
        notifier = _FakeNotifier(chat_id="111", updates_batches=[updates, []])
        engine = _FakeEngine(self.db, {"EURUSD": NoTradeReason(pair="EURUSD", reason="x")})
        check_and_respond(engine, notifier, pairs=["EURUSD"])
        self.assertEqual(notifier.offsets_requested[0], None)

        summary2 = check_and_respond(engine, notifier, pairs=["EURUSD"])
        self.assertEqual(notifier.offsets_requested[1], 31)
        self.assertEqual(summary2, "no new Telegram messages")
        self.assertEqual(len(notifier.sent), 1)  # only the first check sent a reply

    def test_engine_exception_for_one_pair_is_reported_not_raised(self):
        updates = [_msg_update(40, chat_id="111", text="analyze")]
        notifier = _FakeNotifier(chat_id="111", updates_batches=[updates])

        class _BoomEngine(_FakeEngine):
            def evaluate_pair(self, pair, timeframe):
                self.calls.append(pair)
                raise RuntimeError("data provider unreachable")

        engine = _BoomEngine(self.db, {})
        check_and_respond(engine, notifier, pairs=["EURUSD"])
        self.assertIn("EURUSD: ERROR -- data provider unreachable", notifier.sent[0])


class TestAppStateKeyValueStore(unittest.TestCase):
    def setUp(self):
        self.db = _fresh_db()

    def test_missing_key_returns_none(self):
        self.assertIsNone(self.db.get_state("nope"))

    def test_set_then_get_round_trips(self):
        self.db.set_state("telegram_last_update_id", "123")
        self.assertEqual(self.db.get_state("telegram_last_update_id"), "123")

    def test_set_overwrites_existing_value(self):
        self.db.set_state("k", "1")
        self.db.set_state("k", "2")
        self.assertEqual(self.db.get_state("k"), "2")


if __name__ == "__main__":
    unittest.main()
