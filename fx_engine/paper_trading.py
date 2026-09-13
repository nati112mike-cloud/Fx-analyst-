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
from datetime import datetime, timezone

from fx_engine import config
from fx_engine.data.models import Timeframe
from fx_engine.data.providers import HistoricalDataProvider
from fx_engine.signal_engine import NoTradeReason, SignalEngine

logger = logging.getLogger("fx_engine.paper_trading")


class PaperTradingLoop:
    def __init__(
        self,
        signal_engine: SignalEngine,
        data_provider: HistoricalDataProvider,
        pairs: list[str] | None = None,
        timeframe: Timeframe = Timeframe.H4,
        poll_interval_seconds: int = 900,
    ):
        self.engine = signal_engine
        self.data_provider = data_provider
        self.pairs = pairs or config.PAIRS
        self.timeframe = timeframe
        self.poll_interval_seconds = poll_interval_seconds

    def _resolve_open_trades(self) -> None:
        db = self.engine.db
        for trade in db.open_paper_trades():
            pair = trade["pair"]
            try:
                df = self.data_provider.get_ohlc(
                    pair, Timeframe.M15,
                    datetime.fromisoformat(trade["entry_time"]),
                    datetime.now(timezone.utc),
                )
            except Exception as exc:
                logger.warning("could not fetch data to resolve paper trade %s (%s): %s", trade["id"], pair, exc)
                continue
            if df.empty:
                continue

            direction = trade["direction"]
            sl, tp = trade["stop_loss"], trade["take_profit"]
            hit_sl = (df["low"] <= sl).any() if direction == "BUY" else (df["high"] >= sl).any()
            hit_tp = (df["high"] >= tp).any() if direction == "BUY" else (df["low"] <= tp).any()

            if hit_sl or hit_tp:
                exit_price = sl if hit_sl else tp  # conservative: stop assumed first if both touched
                reason = "SL" if hit_sl else "TP"
                sign = 1 if direction == "BUY" else -1
                risk = abs(trade["entry_price"] - sl)
                pnl_r = (sign * (exit_price - trade["entry_price"])) / risk if risk > 0 else 0.0
                db.close_paper_trade(trade["id"], datetime.now(timezone.utc).isoformat(), exit_price, reason, pnl_r)
                if trade.get("signal_id"):
                    db.resolve_signal(trade["signal_id"], reason, exit_price, pnl_r)
                logger.info("paper trade %s (%s %s) closed: %s, pnl_r=%.2f", trade["id"], pair, direction, reason, pnl_r)

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
