"""Telegram signal notifications (Section 17).

Formatting is separated from sending so the message can be unit-tested /
previewed without a real bot token. Credentials come only from
fx_engine.config (environment variables) -- never hard-coded.
"""
from __future__ import annotations

from dataclasses import dataclass

from fx_engine import config
from fx_engine.ensemble import EnsembleResult
from fx_engine.regime import RegimeSnapshot
from fx_engine.scoring import SignalScore
from fx_engine.strategies.base import Direction

DIRECTION_EMOJI = {Direction.BUY: "\U0001F7E2", Direction.SELL: "\U0001F534"}
CHECK, DASH = "✅", "➖"


@dataclass
class SignalMessage:
    pair: str
    direction: Direction
    overall_signal: str
    score: SignalScore
    ensemble: EnsembleResult
    regime: RegimeSnapshot
    entry_low: float
    entry_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float | None
    risk_reward_1: float
    risk_reward_2: float | None
    spread_pips: float
    reason: str
    pip: float

    def _fmt(self, price: float) -> str:
        digits = 3 if self.pip == 0.01 else 5
        return f"{price:.{digits}f}"

    def format(self) -> str:
        emoji = DIRECTION_EMOJI.get(self.direction, "⚪")
        lines = [
            "━" * 18,
            "\U0001F6A8 FOREX SIGNAL",
            "━" * 18,
            "",
            f"PAIR: {self.pair}",
            f"DIRECTION: {emoji} {self.direction.value}",
            "",
            f"SIGNAL SCORE: {self.score.score:.0f}/100  (quality score, NOT a win probability)",
            f"STRATEGY AGREEMENT: {len(self.ensemble.agreeing)}/{len(self.ensemble.all_results)}",
            "",
            f"ENTRY: {self._fmt(self.entry_low)} - {self._fmt(self.entry_high)}",
            f"STOP LOSS: {self._fmt(self.stop_loss)}",
            f"TAKE PROFIT 1: {self._fmt(self.take_profit_1)}",
        ]
        if self.take_profit_2:
            lines.append(f"TAKE PROFIT 2: {self._fmt(self.take_profit_2)}")
        rr_line = f"RISK/REWARD: 1:{self.risk_reward_1:.1f}"
        if self.risk_reward_2:
            rr_line += f" / 1:{self.risk_reward_2:.1f}"
        lines.append(rr_line)
        lines += [
            f"SPREAD: {self.spread_pips:.1f} pips",
            f"MARKET REGIME: {self.regime.regime.replace('_', ' ').title()}",
            "",
            "STRATEGIES:",
        ]
        for name, result in self.ensemble.all_results.items():
            mark = CHECK if result.direction == self.direction else DASH
            lines.append(f"  {name}: {mark} ({result.direction.value})")
        lines += [
            "",
            f"REASON: {self.reason}",
            "",
            "⚠️ This is a quantitative research signal, not a guarantee of profit.",
            "━" * 18,
        ]
        if self.ensemble.unvalidated:
            lines.insert(4, "⚠️ UNVALIDATED: one or more strategies here have no backtest on "
                             "record yet -- weights are equal-default, not evidence-based.")
        return "\n".join(lines)


class TelegramNotifier:
    def __init__(self, bot_token: str | None = None, chat_id: str | None = None):
        self.bot_token = bot_token or config.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or config.TELEGRAM_CHAT_ID

    @property
    def is_configured(self) -> bool:
        return bool(self.bot_token and self.chat_id)

    def send(self, text: str) -> bool:
        if not self.is_configured:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set. Create a bot via @BotFather, "
                "add both to your .env, and see docs/TELEGRAM.md."
            )
        import requests

        url = f"https://api.telegram.org/bot{self.bot_token}/sendMessage"
        resp = requests.post(url, json={"chat_id": self.chat_id, "text": text}, timeout=15)
        resp.raise_for_status()
        return resp.json().get("ok", False)
