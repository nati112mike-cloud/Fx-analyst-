"""Spread-aware, no-lookahead bar-by-bar backtesting engine.

Design choices, deliberately matching the plan's Section 5/6/12:

- Every strategy call receives ONLY data up to and including the bar being
  evaluated (df.iloc[:i+1]) -- there is no way for a strategy to see the
  future here, by construction.
- A signal confirmed on bar i is filled at bar i+1's open (you cannot
  trade the same close you used to decide), through SpreadModel so BUY
  entries pay the ask and SELL entries pay the bid, exits the opposite way.
- If both the stop and target could technically be touched within the same
  bar (a bar whose high/low range spans both), the stop is assumed to hit
  first. This is the conservative assumption and avoids overstating results.
- Only one open position per (pair, strategy) at a time in this engine --
  portfolio-level position sizing across concurrent trades is explicitly
  out of scope for this version (see docs/LIMITATIONS.md).
"""
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from fx_engine import config
from fx_engine.costs import SpreadModel, fill_price
from fx_engine.features import compute_feature_frame
from fx_engine.regime import compute_regime_series
from fx_engine.strategies.base import BaseStrategy, Direction
from fx_engine.swap import swap_pips


@dataclass
class Trade:
    pair: str
    strategy: str
    direction: str
    entry_time: pd.Timestamp
    entry_price: float
    stop_loss: float
    take_profit: float
    exit_time: pd.Timestamp | None = None
    exit_price: float | None = None
    exit_reason: str | None = None  # "TP" | "SL" | "TIME" | "END_OF_DATA"
    regime_at_entry: str = ""
    spread_pips_at_entry: float = 0.0
    risk_distance: float = 0.0
    pip: float = 0.0001

    @property
    def is_closed(self) -> bool:
        return self.exit_price is not None

    def swap_price(self) -> float:
        """Swap cost/credit (see fx_engine.swap), converted from pips into
        the same price units as entry_price/exit_price so it folds directly
        into pnl_price() -- zero by default (see config.SWAP_*_PIPS_PER_NIGHT)."""
        if not self.is_closed:
            return 0.0
        pips = swap_pips(self.pair, self.direction, self.entry_time.to_pydatetime(), self.exit_time.to_pydatetime())
        return pips * self.pip

    def pnl_price(self) -> float:
        if not self.is_closed:
            return 0.0
        sign = 1 if self.direction == "BUY" else -1
        return sign * (self.exit_price - self.entry_price) + self.swap_price()

    def pnl_r(self) -> float:
        if not self.is_closed or self.risk_distance <= 0:
            return 0.0
        return self.pnl_price() / self.risk_distance


@dataclass
class BacktestResult:
    pair: str
    strategy: str
    trades: list[Trade] = field(default_factory=list)
    bars_evaluated: int = 0


class BacktestEngine:
    def __init__(self, warmup_bars: int = 210):
        self.warmup_bars = warmup_bars

    def run(self, strategy: BaseStrategy, df: pd.DataFrame, pair: str) -> BacktestResult:
        if len(df) <= self.warmup_bars + 2:
            return BacktestResult(pair=pair, strategy=strategy.spec.name)

        spread_model = SpreadModel(pair)
        trades: list[Trade] = []
        open_trade: Trade | None = None
        holding_bars = 0

        # Precompute ONCE for the whole series -- every indicator/regime
        # calculation is causal (backward-looking only), so slicing these
        # precomputed frames at position i is equivalent to, and vastly
        # cheaper than, recomputing everything from scratch on df.iloc[:i+1]
        # for every single bar (which is what made this O(n^2) originally).
        features_full = compute_feature_frame(df)
        regime_full = compute_regime_series(df, features_full)

        n = len(df)
        for i in range(self.warmup_bars, n - 1):
            window = df.iloc[: i + 1]
            feat_window = features_full.iloc[: i + 1]

            if open_trade is None:
                current_regime = str(regime_full["regime"].iloc[i])
                result = strategy.generate(window, pair, current_regime, feat_window)
                if result.is_actionable:
                    next_ts = df.index[i + 1]
                    entry_mid = df["open"].iloc[i + 1]
                    quote = spread_model.quote_from_mid(entry_mid, next_ts, current_regime)
                    entry_price = fill_price(quote, result.direction.value, "ENTRY")

                    est_entry = (
                        (result.entry_low + result.entry_high) / 2
                        if result.entry_low is not None and result.entry_high is not None
                        else window["close"].iloc[-1]
                    )
                    risk_distance = abs(est_entry - result.stop_loss)
                    reward_distance = abs(result.take_profit - est_entry)
                    if risk_distance <= 0:
                        continue

                    if result.direction == Direction.BUY:
                        sl = entry_price - risk_distance
                        tp = entry_price + reward_distance
                    else:
                        sl = entry_price + risk_distance
                        tp = entry_price - reward_distance

                    open_trade = Trade(
                        pair=pair, strategy=strategy.spec.name, direction=result.direction.value,
                        entry_time=next_ts, entry_price=entry_price, stop_loss=sl, take_profit=tp,
                        regime_at_entry=current_regime,
                        spread_pips_at_entry=quote.spread / spread_model.pip,
                        risk_distance=risk_distance, pip=spread_model.pip,
                    )
                    holding_bars = 0
                continue

            # manage the open trade using bar i+1 (the bar we're about to
            # step into was already used for entry above on the bar it
            # opened; from here we check subsequent bars)
            bar = df.iloc[i + 1]
            ts = df.index[i + 1]
            holding_bars += 1
            direction = open_trade.direction

            hit_sl = bar["low"] <= open_trade.stop_loss if direction == "BUY" else bar["high"] >= open_trade.stop_loss
            hit_tp = bar["high"] >= open_trade.take_profit if direction == "BUY" else bar["low"] <= open_trade.take_profit

            exit_quote = spread_model.quote_from_mid(bar["close"], ts, "normal")

            if hit_sl:
                open_trade.exit_time, open_trade.exit_price, open_trade.exit_reason = ts, open_trade.stop_loss, "SL"
            elif hit_tp:
                open_trade.exit_time, open_trade.exit_price, open_trade.exit_reason = ts, open_trade.take_profit, "TP"
            elif holding_bars >= config.MAX_HOLDING_BARS:
                exit_price = fill_price(exit_quote, direction, "EXIT")
                open_trade.exit_time, open_trade.exit_price, open_trade.exit_reason = ts, exit_price, "TIME"

            if open_trade.is_closed:
                trades.append(open_trade)
                open_trade = None

        if open_trade is not None:
            last_bar = df.iloc[-1]
            exit_quote = spread_model.quote_from_mid(last_bar["close"], df.index[-1], "normal")
            open_trade.exit_time = df.index[-1]
            open_trade.exit_price = fill_price(exit_quote, open_trade.direction, "EXIT")
            open_trade.exit_reason = "END_OF_DATA"
            trades.append(open_trade)

        return BacktestResult(pair=pair, strategy=strategy.spec.name, trades=trades, bars_evaluated=n - self.warmup_bars)
