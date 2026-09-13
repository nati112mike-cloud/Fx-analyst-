"""Telegram signal notifications (Section 17).

Formatting is separated from sending so the message can be unit-tested /
previewed without a real bot token. Credentials come only from
fx_engine.config (environment variables) -- never hard-coded.
"""
from __future__ import annotations

from dataclasses import dataclass

from fx_engine import config
from fx_engine.ensemble import EnsembleResult
from fx_engine.position_sizing import PositionSizeResult
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
    position_size: PositionSizeResult | None = None
    position_size_error: str | None = None

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
        if self.position_size is not None:
            ps = self.position_size
            margin_txt = f", ~{ps.required_margin:.0f} {ps.account_currency} margin" if ps.required_margin else ""
            lines.append(f"POSITION SIZE: {ps.lots:.2f} lots (risking {ps.risk_amount:.2f} {ps.account_currency} "
                          f"= {ps.risk_pct:.2f}% of {ps.account_equity:.0f}{margin_txt})")
            for note in ps.notes:
                lines.append(f"  ⚠️ {note}")
        elif self.position_size_error:
            lines.append(f"POSITION SIZE: unavailable ({self.position_size_error})")
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

    def get_updates(self, offset: int | None = None) -> list[dict]:
        """Short-poll for messages sent to the bot since `offset` (a Telegram
        update_id, exclusive of anything already seen). Used by
        fx_engine.telegram_listener to let the user request an on-demand
        analysis by simply messaging the bot -- no long-lived connection
        needed, so this is safe to call from a short-lived scheduled job.
        """
        if not self.is_configured:
            raise RuntimeError(
                "TELEGRAM_BOT_TOKEN / TELEGRAM_CHAT_ID are not set. Create a bot via @BotFather, "
                "add both to your .env, and see docs/TELEGRAM.md."
            )
        import requests

        url = f"https://api.telegram.org/bot{self.bot_token}/getUpdates"
        params = {"timeout": 0}
        if offset is not None:
            params["offset"] = offset
        resp = requests.get(url, params=params, timeout=15)
        resp.raise_for_status()
        return resp.json().get("result", [])
