"""Swap / rollover cost modeling.

Forex positions held open through the daily rollover (when the broker
rolls the settlement date forward, conventionally ~21:00-22:00 UTC,
aligned with the 5pm New York forex-day close) are charged or credited a
swap: an overnight interest-rate differential between the two traded
currencies, quoted by the broker as pips per 1.0 lot per night -- the
units used here, matching how MT5's own Symbol Specification window
quotes it. Wednesday (the industry-standard "triple swap" day) is
charged 3x to cover the weekend the market is closed for.

CONFIRM the exact rollover hour and triple-swap weekday against your own
Exness account before trusting this verbatim -- brokers vary, and this
uses the most common industry convention as a reasonable default, not a
verified Exness-specific figure (this sandbox has no way to check a real
Exness terminal -- see docs/LIMITATIONS.md).

All per-pair rates default to 0.0 (fx_engine.config.SWAP_LONG_PIPS_PER_NIGHT
/ SWAP_SHORT_PIPS_PER_NIGHT) -- there is no honest non-zero default, since
swap rates are broker- and account-type-specific and change with interest
rates. Fill in your own account's real figures (MT5 terminal -> Market
Watch -> right-click a symbol -> Specification) to make this non-zero.
The mechanism below is real and tested regardless of what the rates are.
"""
from __future__ import annotations

from datetime import datetime, time, timedelta, timezone

from fx_engine import config

ROLLOVER_HOUR_UTC = 21
TRIPLE_SWAP_WEEKDAY = 2  # Monday=0 ... Wednesday=2, the common industry convention


def count_rollover_charges(
    entry_time: datetime,
    exit_time: datetime,
    rollover_hour_utc: int = ROLLOVER_HOUR_UTC,
    triple_weekday: int = TRIPLE_SWAP_WEEKDAY,
) -> int:
    """Number of nightly-swap charge units a position held from entry_time
    to exit_time accrues (a position must be open AT the rollover moment
    to be charged for that night), where the triple-swap day counts as 3.
    """
    if entry_time.tzinfo is None:
        entry_time = entry_time.replace(tzinfo=timezone.utc)
    if exit_time.tzinfo is None:
        exit_time = exit_time.replace(tzinfo=timezone.utc)
    if exit_time <= entry_time:
        return 0

    first_rollover = datetime.combine(entry_time.date(), time(rollover_hour_utc, 0), tzinfo=timezone.utc)
    if first_rollover <= entry_time:
        first_rollover += timedelta(days=1)

    charges = 0
    current = first_rollover
    while current <= exit_time:
        charges += 3 if current.weekday() == triple_weekday else 1
        current += timedelta(days=1)
    return charges


def swap_pips(pair: str, direction: str, entry_time: datetime, exit_time: datetime) -> float:
    """Total swap in pips (negative = cost to the account, positive =
    credit) for holding `direction` on `pair` from entry_time to exit_time."""
    table = config.SWAP_LONG_PIPS_PER_NIGHT if direction.upper() == "BUY" else config.SWAP_SHORT_PIPS_PER_NIGHT
    per_night = table.get(pair, 0.0)
    if per_night == 0.0:
        return 0.0
    return per_night * count_rollover_charges(entry_time, exit_time)
