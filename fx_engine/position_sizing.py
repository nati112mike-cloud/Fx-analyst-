"""Position size calculator.

Turns (account equity, risk %, entry, stop) into an actual lot size and
dollar risk amount -- the piece docs/RISK_MANAGEMENT.md previously left as
"size it yourself." Every forex pair falls into one of three cases
relative to your account currency, and each needs different math:

1. **Direct pair** (quote currency == account currency, e.g. EURUSD on a
   USD account): pip value per lot is fixed -- pip_size * contract_size,
   no conversion needed.
2. **Indirect/inverse pair** (base currency == account currency, e.g.
   USDJPY on a USD account): pip value per lot depends on the pair's own
   current price.
3. **Cross pair** (neither leg is the account currency, e.g. EURJPY on a
   USD account): pip value needs a THIRD rate (here, USD/JPY) to convert
   into account currency. This module never guesses that rate -- either
   pass it explicitly, or pass a `price_lookup` callable and it will be
   resolved from a live quote, or a clear PositionSizingError is raised.

This does not simulate margin calls or a running account balance -- see
fx_engine/broker/paper.py for that. It answers one question correctly:
"how many lots, for this trade, right now."
"""
from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass, field

from fx_engine import config


class PositionSizingError(ValueError):
    pass


@dataclass
class PositionSizeResult:
    pair: str
    direction: str
    account_equity: float
    account_currency: str
    risk_pct: float
    risk_amount: float
    entry_price: float
    stop_loss: float
    stop_distance_pips: float
    pip_value_per_lot: float
    lots: float
    units: float
    contract_size: float
    required_margin: float | None
    notes: list[str] = field(default_factory=list)

    def summary_line(self) -> str:
        margin_txt = f", required margin ~{self.required_margin:.2f} {self.account_currency}" if self.required_margin else ""
        return (f"{self.direction} {self.lots:.2f} lots {self.pair} "
                f"(risking {self.risk_amount:.2f} {self.account_currency} = {self.risk_pct:.2f}% of "
                f"{self.account_equity:.2f}, stop {self.stop_distance_pips:.1f} pips{margin_txt})")


def pip_value_per_lot(
    pair: str,
    current_price: float,
    account_currency: str = "USD",
    conversion_rate: float | None = None,
    contract_size: float = config.CONTRACT_SIZE,
) -> float:
    """Value, in `account_currency`, of a 1-pip move on 1.0 standard lot of
    `pair` at `current_price`. See module docstring for the three cases."""
    pair = pair.upper()
    if len(pair) < 6:
        raise PositionSizingError(f"Unrecognized pair format: {pair!r} (expected 6 letters, e.g. 'EURUSD')")
    base, quote = pair[:3], pair[3:6]
    account_currency = account_currency.upper()
    pip = config.pip_size(pair)
    pip_value_quote_ccy = pip * contract_size

    if quote == account_currency:
        return pip_value_quote_ccy
    if base == account_currency:
        if current_price <= 0:
            raise PositionSizingError(f"current_price must be positive to convert {pair} pip value, got {current_price}")
        return pip_value_quote_ccy / current_price
    if conversion_rate is None:
        raise PositionSizingError(
            f"{pair} is a cross pair relative to account currency {account_currency} (neither leg matches). "
            f"A conversion_rate ({quote}->{account_currency}) is required -- pass one explicitly, or pass "
            "price_lookup to calculate_position_size() so it can be resolved from a live quote."
        )
    if conversion_rate <= 0:
        raise PositionSizingError(f"conversion_rate must be positive, got {conversion_rate}")
    return pip_value_quote_ccy * conversion_rate


def _resolve_conversion_rate(quote_currency: str, account_currency: str, price_lookup: Callable[[str], float]) -> float:
    for candidate, invert in ((f"{quote_currency}{account_currency}", False), (f"{account_currency}{quote_currency}", True)):
        try:
            price = price_lookup(candidate)
        except Exception:
            continue
        if price:
            return (1.0 / price) if invert else price
    raise PositionSizingError(
        f"Could not resolve a conversion rate {quote_currency}->{account_currency} via price_lookup "
        f"(tried '{quote_currency}{account_currency}' and '{account_currency}{quote_currency}')."
    )


def calculate_position_size(
    pair: str,
    direction: str,
    entry_price: float,
    stop_loss: float,
    account_equity: float,
    risk_pct: float = config.RISK_PER_TRADE_PCT,
    account_currency: str = config.ACCOUNT_CURRENCY,
    conversion_rate: float | None = None,
    price_lookup: Callable[[str], float] | None = None,
    leverage: int | None = config.DEFAULT_LEVERAGE,
    contract_size: float = config.CONTRACT_SIZE,
    lot_step: float = config.MIN_LOT_STEP,
) -> PositionSizeResult:
    """The one function to call. Rounds DOWN to the nearest `lot_step` --
    never rounds up past the requested risk (Exness, like most brokers,
    only accepts lot sizes in fixed increments, typically 0.01)."""
    if account_equity <= 0:
        raise PositionSizingError(f"account_equity must be positive, got {account_equity}")
    if risk_pct <= 0:
        raise PositionSizingError(f"risk_pct must be positive, got {risk_pct}")
    if entry_price <= 0 or stop_loss <= 0:
        raise PositionSizingError("entry_price and stop_loss must be positive prices")
    direction = direction.upper()
    if direction not in ("BUY", "SELL"):
        raise PositionSizingError(f"direction must be 'BUY' or 'SELL', got {direction!r}")

    pair = pair.upper()
    account_currency = account_currency.upper()
    pip = config.pip_size(pair)
    stop_distance_price = abs(entry_price - stop_loss)
    if stop_distance_price <= 0:
        raise PositionSizingError("entry_price and stop_loss cannot be equal")
    stop_distance_pips = stop_distance_price / pip

    notes: list[str] = []
    if (direction == "BUY" and stop_loss >= entry_price) or (direction == "SELL" and stop_loss <= entry_price):
        notes.append(f"WARNING: stop_loss is on the wrong side of entry_price for a {direction} -- "
                      "double check these levels before trusting this size.")

    quote_ccy = pair[3:6]
    if conversion_rate is None and quote_ccy != account_currency and pair[:3] != account_currency and price_lookup is not None:
        conversion_rate = _resolve_conversion_rate(quote_ccy, account_currency, price_lookup)

    pv = pip_value_per_lot(pair, entry_price, account_currency, conversion_rate, contract_size)

    risk_amount_budget = account_equity * (risk_pct / 100.0)
    raw_lots = risk_amount_budget / (stop_distance_pips * pv)
    lots = math.floor(raw_lots / lot_step + 1e-9) * lot_step
    lots = round(max(lots, 0.0), 8)

    if lots <= 0:
        notes.append(f"Computed size rounds down to 0 lots at a {lot_step} lot step -- the stop is too wide, "
                      f"risk_pct too small, or account_equity too small for this setup (raw size was "
                      f"{raw_lots:.4f} lots before rounding).")

    units = lots * contract_size
    actual_risk_amount = lots * stop_distance_pips * pv

    required_margin = None
    if leverage:
        required_margin = (units * entry_price) / leverage

    return PositionSizeResult(
        pair=pair, direction=direction, account_equity=account_equity, account_currency=account_currency,
        risk_pct=risk_pct, risk_amount=round(actual_risk_amount, 2), entry_price=entry_price,
        stop_loss=stop_loss, stop_distance_pips=round(stop_distance_pips, 1), pip_value_per_lot=round(pv, 5),
        lots=round(lots, 2), units=round(units, 2), contract_size=contract_size,
        required_margin=round(required_margin, 2) if required_margin is not None else None, notes=notes,
    )
