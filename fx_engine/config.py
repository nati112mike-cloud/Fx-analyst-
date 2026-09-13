"""Central configuration, loaded from environment variables.

Nothing sensitive is hard-coded here. Copy .env.example to .env and fill it in.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


def _env_float(name: str, default: float) -> float:
    val = os.getenv(name)
    return float(val) if val not in (None, "") else default


def _env_int(name: str, default: int) -> int:
    val = os.getenv(name)
    return int(val) if val not in (None, "") else default


def _env_list(name: str, default: list[str]) -> list[str]:
    val = os.getenv(name)
    if not val:
        return default
    return [x.strip() for x in val.split(",") if x.strip()]


# --- Instrument universe -----------------------------------------------
DEFAULT_PAIRS = [
    "EURUSD", "GBPUSD", "USDJPY", "USDCHF",
    "AUDUSD", "USDCAD", "NZDUSD", "EURJPY", "GBPJPY",
]
PAIRS = _env_list("FX_PAIRS", DEFAULT_PAIRS)

# --- Risk management -----------------------------------------------------
RISK_PER_TRADE_PCT = _env_float("RISK_PER_TRADE_PCT", 0.5)       # % of equity risked per trade
MAX_DAILY_LOSS_PCT = _env_float("MAX_DAILY_LOSS_PCT", 2.0)       # daily circuit breaker
MIN_RR_AFTER_COSTS = _env_float("MIN_RR_AFTER_COSTS", 1.5)       # reject signals below this
MAX_SPREAD_TO_STOP_RATIO = _env_float("MAX_SPREAD_TO_STOP_RATIO", 0.30)  # spread must be < 30% of SL distance
MAX_HOLDING_BARS = _env_int("MAX_HOLDING_BARS", 60)              # invalidate a trade idea after N bars unresolved

# --- Position sizing / account -------------------------------------------
ACCOUNT_CURRENCY = os.getenv("ACCOUNT_CURRENCY", "USD")
ACCOUNT_STARTING_BALANCE = _env_float("ACCOUNT_STARTING_BALANCE", 10_000.0)
DEFAULT_LEVERAGE = _env_int("DEFAULT_LEVERAGE", 100)
CONTRACT_SIZE = _env_float("CONTRACT_SIZE", 100_000.0)  # units per 1.0 standard lot
MIN_LOT_STEP = _env_float("MIN_LOT_STEP", 0.01)  # Exness typically allows 0.01-lot increments

# --- Per-pair "normal" spread table (pips) -------------------------------
# Approximation used for backtesting/demo purposes only. When connected to
# Exness via MT5 (see broker/exness_mt5.py), REAL live/historical spread
# should be used instead of this table -- see docs/LIMITATIONS.md.
TYPICAL_SPREAD_PIPS: dict[str, float] = {
    "EURUSD": 0.9, "GBPUSD": 1.3, "USDJPY": 1.0, "USDCHF": 1.6,
    "AUDUSD": 1.1, "USDCAD": 1.5, "NZDUSD": 1.6, "EURJPY": 1.4, "GBPJPY": 2.2,
}
JPY_PAIRS = {"USDJPY", "EURJPY", "GBPJPY"}


def pip_size(pair: str) -> float:
    return 0.01 if pair in JPY_PAIRS else 0.0001


# --- Data / broker credentials (never commit real values) ---------------
DATA_PROVIDER = os.getenv("FX_DATA_PROVIDER", "synthetic")  # synthetic | yahoo | mt5

EXNESS_MT5_LOGIN = os.getenv("EXNESS_MT5_LOGIN", "")
EXNESS_MT5_PASSWORD = os.getenv("EXNESS_MT5_PASSWORD", "")
EXNESS_MT5_SERVER = os.getenv("EXNESS_MT5_SERVER", "")
EXNESS_MT5_TERMINAL_PATH = os.getenv("EXNESS_MT5_TERMINAL_PATH", "")

TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TELEGRAM_CHAT_ID = os.getenv("TELEGRAM_CHAT_ID", "")

DB_PATH = os.getenv("FX_DB_PATH", "fx_engine.sqlite3")

# --- Backtesting / validation --------------------------------------------
WALK_FORWARD_FOLDS = _env_int("WALK_FORWARD_FOLDS", 5)
HOLDOUT_FRACTION = _env_float("HOLDOUT_FRACTION", 0.20)  # final slice never used for tuning

SLIPPAGE_PIPS = _env_float("SLIPPAGE_PIPS", 0.3)

# --- Swap / rollover (pips per 1.0 lot, per night held) ------------------
# All zero by default -- these are broker- and account-type-specific (and
# change over time with interest rates), so there is no honest default
# except "unknown, assume zero until you fill in your own account's real
# figures from the Exness terminal (Market Watch -> right-click a symbol
# -> Specification -> swap long/short, usually quoted in points/pips)".
# The mechanism (fx_engine/swap.py -- applied at the 21:00 UTC rollover,
# tripled on Wednesday for the weekend) is real and tested; only the
# numbers below are placeholders. See docs/LIMITATIONS.md.
SWAP_LONG_PIPS_PER_NIGHT: dict[str, float] = {p: 0.0 for p in DEFAULT_PAIRS}
SWAP_SHORT_PIPS_PER_NIGHT: dict[str, float] = {p: 0.0 for p in DEFAULT_PAIRS}
