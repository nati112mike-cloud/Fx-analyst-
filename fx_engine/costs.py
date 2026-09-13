"""Spread-aware execution cost modeling.

This is the piece the original plan correctly insists on: forex is traded
bid/ask, not mid-price, and spread must flow through entry price, exit
price, SL, TP, R:R and backtest P&L -- never bolted on afterward.

IMPORTANT LIMITATION: without a live/historical Exness tick feed (which
requires MT5 -- see broker/exness_mt5.py), we do not have Exness's actual
historical bid/ask series. SpreadModel therefore approximates spread from
a per-pair "typical" baseline (config.TYPICAL_SPREAD_PIPS) widened around
the rollover window and during high-volatility regimes, which is a
reasonable, honestly-labeled approximation for backtesting -- not a
substitute for real spread data once you're paper/live trading through
MT5, where SpreadModel.from_live_quote() should be used instead.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, time, timezone

from fx_engine import config


@dataclass
class Quote:
    bid: float
    ask: float

    @property
    def mid(self) -> float:
        return (self.bid + self.ask) / 2

    @property
    def spread(self) -> float:
        return self.ask - self.bid


class SpreadModel:
    def __init__(self, pair: str):
        self.pair = pair
        self.pip = config.pip_size(pair)
        self.base_pips = config.TYPICAL_SPREAD_PIPS.get(pair, 2.0)

    def estimate_spread_pips(self, ts: datetime, volatility_regime: str = "normal") -> float:
        """Approximate the spread (in pips) at a given UTC timestamp.

        Widens during the low-liquidity rollover window (21:00-23:00 UTC)
        and during high-volatility regimes, both realistic Exness behaviors.
        """
        mult = 1.0
        t = ts.astimezone(timezone.utc).time() if ts.tzinfo else ts.time()
        if time(21, 0) <= t <= time(23, 0):
            mult *= 2.5
        if ts.weekday() == 6 or (ts.weekday() == 4 and t >= time(21, 0)):  # Sun / late Fri
            mult *= 3.0
        if volatility_regime == "high_volatility":
            mult *= 1.8
        elif volatility_regime == "low_volatility":
            mult *= 0.9
        return round(self.base_pips * mult, 2)

    def quote_from_mid(self, mid_price: float, ts: datetime, volatility_regime: str = "normal") -> Quote:
        spread_price = self.estimate_spread_pips(ts, volatility_regime) * self.pip
        half = spread_price / 2
        return Quote(bid=mid_price - half, ask=mid_price + half)

    @staticmethod
    def from_live_quote(bid: float, ask: float) -> Quote:
        """Use this instead of quote_from_mid() once real bid/ask is
        available from the broker adapter (paper or live MT5)."""
        return Quote(bid=bid, ask=ask)


def fill_price(quote: Quote, direction: str, action: str) -> float:
    """direction: 'BUY' or 'SELL'. action: 'ENTRY' or 'EXIT'.

    BUY entry / SELL exit -> ASK
    SELL entry / BUY exit -> BID
    """
    if (direction == "BUY" and action == "ENTRY") or (direction == "SELL" and action == "EXIT"):
        return quote.ask
    return quote.bid


def spread_cost_pips(quote: Quote, pip: float) -> float:
    return quote.spread / pip
