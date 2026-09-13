"""Continuous paper-trading loop (Section 19).

Runs the SignalEngine on a schedule, opens a simulated (never real) trade
for every actionable signal, and resolves open paper trades by checking
whether price has since hit their stop or target. This is the step between
"backtest looks good" and "risking real money" -- run it for weeks before
trusting a strategy's live behavior, since it captures execution/slippage
realities a backtest can only approximate.
"""
from __future__ import annotations

import logging
import time
from datetime import datetime, timedelta, timezone

import pandas as pd

from fx_engine import config
from fx_engine.broker.paper import PaperBrokerAdapter
from fx_engine.data.models import Timeframe
from fx_engine.data.providers import HistoricalDataProvider
from fx_engine.position_sizing import PositionSizingError, pip_value_per_lot
from fx_engine.signal_engine import NoTradeReason, SignalEngine
from fx_engine.swap import swap_pips

logger = logging.getLogger("fx_engine.paper_trading")


class PaperTradingLoop:
    def __init__(
        self,
        signal_engine: SignalEngine,
        data_provider: HistoricalDataProvider,
        pairs: list[str] | None = None,
        timeframe: Timeframe = Timeframe.H4,
        poll_interval_seconds: int = 900,
        broker: PaperBrokerAdapter | None = None,
    ):
        self.engine = signal_engine
        self.data_provider = data_provider
        self.pairs = pairs or config.PAIRS
        self.timeframe = timeframe
        self.poll_interval_seconds = poll_interval_seconds
        self.broker = broker  # when set, its running balance is updated on every closed trade

    def _resolve_open_trades(self) -> None:
        db = self.engine.db
        for trade in db.open_paper_trades():
            pair = trade["pair"]
            entry_time = datetime.fromisoformat(trade["entry_time"])
            now = datetime.now(timezone.utc)
            # Fetch a window that starts well before entry_time even for a
            # trade opened moments ago -- some providers (SyntheticDataProvider
            # included) reject a too-short range outright, and a too-short
            # range is also just less robust in general. Filter back down to
            # entry_time before checking for a hit, though: fetching extra
            # PRE-entry bars must never let pre-entry price action look like
            # it triggered a stop/target that didn't exist yet.
            fetch_start = min(entry_time, now - timedelta(hours=6))
            try:
                df = self.data_provider.get_ohlc(pair, Timeframe.M15, fetch_start, now)
            except Exception as exc:
                logger.warning("could not fetch data to resolve paper trade %s (%s): %s", trade["id"], pair, exc)
                continue
            df = df[df.index >= pd.Timestamp(entry_time)]
            if df.empty:
                continue

            direction = trade["direction"]
            sl, tp = trade["stop_loss"], trade["take_profit"]
            hit_sl = (df["low"] <= sl).any() if direction == "BUY" else (df["high"] >= sl).any()
            hit_tp = (df["high"] >= tp).any() if direction == "BUY" else (df["low"] <= tp).any()

            if hit_sl or hit_tp:
                exit_time_dt = datetime.now(timezone.utc)
                exit_price = sl if hit_sl else tp  # conservative: stop assumed first if both touched
                reason = "SL" if hit_sl else "TP"
                sign = 1 if direction == "BUY" else -1
                risk = abs(trade["entry_price"] - sl)
                pnl_r = (sign * (exit_price - trade["entry_price"])) / risk if risk > 0 else 0.0

                # Swap/rollover (fx_engine.swap) -- zero by default until real
                # per-pair rates are configured, see docs/LIMITATIONS.md.
                swap_amount = 0.0
                pips = swap_pips(pair, direction, entry_time, exit_time_dt)
                if pips != 0.0 and trade.get("lots"):
                    try:
                        account_currency = self.broker.get_account_info().currency if self.broker else config.ACCOUNT_CURRENCY
                        pv = pip_value_per_lot(pair, exit_price, account_currency)
                        swap_amount = pips * pv * trade["lots"]
                    except PositionSizingError as exc:
                        logger.warning("could not convert swap to account currency for %s: %s -- "
                                        "treating swap as 0 for this trade", pair, exc)

                risk_amount = trade.get("risk_amount")
                pnl_amount = (pnl_r * risk_amount + swap_amount) if risk_amount is not None else None

                db.close_paper_trade(trade["id"], exit_time_dt.isoformat(), exit_price, reason, pnl_r, pnl_amount)
                if trade.get("signal_id"):
                    db.resolve_signal(trade["signal_id"], reason, exit_price, pnl_r)

                if self.broker is not None and pnl_amount is not None:
                    self.broker.balance += pnl_amount
                    self.broker.equity = self.broker.balance

                pnl_txt = f", pnl=${pnl_amount:.2f}" if pnl_amount is not None else " (no risk_amount recorded at entry)"
                logger.info("paper trade %s (%s %s) closed: %s, pnl_r=%.2f%s", trade["id"], pair, direction,
                            reason, pnl_r, pnl_txt)

    def run_once(self) -> list:
        self._resolve_open_trades()
        outcomes = []
        for pair in self.pairs:
            try:
                result = self.engine.evaluate_pair(pair, self.timeframe)
            except Exception as exc:
                logger.exception("error evaluating %s", pair)
                self.engine.db.log_health("signal_engine", "ERROR", f"{pair}: {exc}")
                continue

            if isinstance(result, NoTradeReason):
                outcomes.append(result)
                logger.info("NO TRADE %s: %s", result.pair, result.reason)
                continue

            signal_id, msg = result
            self.engine.db.insert_paper_trade({
                "signal_id": signal_id, "pair": pair, "direction": msg.direction.value,
                "entry_time": datetime.now(timezone.utc).isoformat(),
                "entry_price": (msg.entry_low + msg.entry_high) / 2,
                "stop_loss": msg.stop_loss, "take_profit": msg.take_profit_1,
                "lots": msg.position_size.lots if msg.position_size else None,
                "risk_amount": msg.position_size.risk_amount if msg.position_size else None,
            })
            outcomes.append((signal_id, msg))
            logger.info("SIGNAL %s %s score=%.0f", pair, msg.direction.value, msg.score.score)

        self.engine.db.log_health("paper_trading_loop", "OK", f"cycle complete, {len(self.pairs)} pairs")
        return outcomes

    def run_forever(self) -> None:
        logger.info("paper trading loop starting: pairs=%s timeframe=%s interval=%ss",
                     self.pairs, self.timeframe.value, self.poll_interval_seconds)
        while True:
            try:
                self.run_once()
            except Exception:
                logger.exception("unhandled error in paper trading cycle -- continuing")
                self.engine.db.log_health("paper_trading_loop", "ERROR", "unhandled exception, see logs")
            time.sleep(self.poll_interval_seconds)
