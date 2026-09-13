"""Broker adapter interface -- all broker-specific code lives behind this,
so a second broker could be added later without touching the strategy/
signal/backtest layers (Section 6 of the plan).

SAFETY: the system starts, and stays, in SIGNAL-ONLY MODE. No adapter in
this codebase sends a real order unless config.ENABLE_LIVE_TRADING is
explicitly set to true AND the adapter's place_order is called directly by
code you write and review yourself -- nothing in signal_engine.py or
paper_trading.py ever calls place_order(). See docs/SECURITY.md.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass

from fx_engine.costs import Quote


@dataclass
class AccountInfo:
    balance: float
    equity: float
    currency: str
    leverage: int | None = None


class BrokerAdapter(ABC):
    name: str = "base"

    @abstractmethod
    def is_connected(self) -> bool:
        raise NotImplementedError

    @abstractmethod
    def get_quote(self, pair: str) -> Quote:
        raise NotImplementedError

    @abstractmethod
    def get_account_info(self) -> AccountInfo:
        raise NotImplementedError

    def place_order(self, *args, **kwargs):
        raise NotImplementedError(
            "Live order placement is intentionally not wired up. This system runs in "
            "SIGNAL-ONLY / PAPER mode by design (see PROJECT_PLAN.md section 6 and "
            "docs/SECURITY.md). If you decide to automate execution after a long paper-"
            "trading track record, implement and review that separately and deliberately."
        )
