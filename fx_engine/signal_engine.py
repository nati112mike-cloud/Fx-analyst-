"""Orchestrates one full evaluation cycle for a single pair:

regime -> strategies -> ensemble -> spread/cost filter -> quality score ->
signal persistence -> Telegram alert.

This is the piece that answers the question the plan's final principle
poses: "given current conditions, which historically validated strategies
agree, what is the expected risk/reward after realistic costs, and is
there enough evidence to justify sending a signal" -- and its default
answer, per Section 26, is NO TRADE.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pandas as pd

from fx_engine import config
from fx_engine.broker.base import BrokerAdapter
from fx_engine.costs import SpreadModel, fill_price
from fx_engine.data.models import Timeframe
from fx_engine.data.providers import HistoricalDataProvider
from fx_engine.db import Database
from fx_engine.ensemble import EnsembleResult, StrategyEnsemble
from fx_engine.features import compute_feature_frame
from fx_engine.news import NewsFilter
from fx_engine.position_sizing import PositionSizingError, calculate_position_size
from fx_engine.regime import compute_regime_series, latest_regime
from fx_engine.scoring import compute_signal_score
from fx_engine.strategies import build_all
from fx_engine.strategies.base import Direction
from fx_engine.telegram_bot import SignalMessage, TelegramNotifier

logger = logging.getLogger("fx_engine.signal_engine")

MIN_WARMUP_BARS = 460  # multi_timeframe's slowest EMA (450) plus a margin


@dataclass
class NoTradeReason:
    pair: str
    reason: str


class SignalEngine:
    def __init__(
        self,
        data_provider: HistoricalDataProvider,
        db: Database | None = None,
        notifier: TelegramNotifier | None = None,
        news_filter: NewsFilter | None = None,
        min_signal_score: float = 55.0,
        broker: BrokerAdapter | None = None,
        fallback_account_equity: float = config.ACCOUNT_STARTING_BALANCE,
    ):
        self.data_provider = data_provider
        self.db = db or Database()
        self.db.init_schema()
        self.notifier = notifier
        self.news_filter = news_filter or NewsFilter()
        self.min_signal_score = min_signal_score
        self.strategies = build_all()
        self.ensemble = StrategyEnsemble(weight_lookup=self.db.strategy_expectancy)
        self.broker = broker
        self.fallback_account_equity = fallback_account_equity

    def current_account(self) -> tuple[float, str]:
        """Real equity/currency from the connected broker if there is one
        (e.g. PaperBrokerAdapter's running balance, or eventually a real
        MT5 account) -- otherwise a configured placeholder, clearly a
        placeholder because ACCOUNT_STARTING_BALANCE is what it's named."""
        if self.broker is not None:
            try:
                info = self.broker.get_account_info()
                return info.equity, info.currency
            except Exception as exc:
                logger.warning("could not read account info from broker, using fallback equity: %s", exc)
        return self.fallback_account_equity, config.ACCOUNT_CURRENCY

    def _price_lookup(self, pair: str) -> float:
        """Latest close for `pair`, used only to resolve a cross-pair pip-value
        conversion rate (e.g. USDJPY for an EURJPY position on a USD account).
        Uses a several-day H1 window regardless of the caller's own signal
        timeframe -- we only need "current price," and a short window can
        come back with too few bars for a data provider's own minimums
        (e.g. SyntheticDataProvider requires >=10 bars)."""
        end = datetime.now(timezone.utc)
        start = end - timedelta(days=5)
        df = self.data_provider.get_ohlc(pair, Timeframe.H1, start, end)
        return float(df["close"].iloc[-1])

    def evaluate_pair(self, pair: str, timeframe: Timeframe = Timeframe.H4, lookback_bars: int = 700):
        end = datetime.now(timezone.utc)
        start = end - timedelta(minutes=timeframe.minutes * lookback_bars)
        df = self.data_provider.get_ohlc(pair, timeframe, start, end)
        if len(df) < MIN_WARMUP_BARS:
            return NoTradeReason(pair, f"only {len(df)} bars available, need >= {MIN_WARMUP_BARS}")

        account_equity, _ = self.current_account()
        breached, realized_today = self.db.daily_loss_limit_breached(account_equity, config.MAX_DAILY_LOSS_PCT, end)
        if breached:
            return NoTradeReason(pair, f"daily loss circuit breaker: realized {realized_today:.2f} today "
                                        f"exceeds {config.MAX_DAILY_LOSS_PCT:.1f}% of {account_equity:.2f} equity "
                                        "-- no new signals until the next UTC day (section 26)")

        features = compute_feature_frame(df)
        regime_series = compute_regime_series(df, features)
        snapshot = latest_regime(df, features)

        if snapshot.regime == "unclear":
            return NoTradeReason(pair, "market regime is unclear -- default is NO TRADE (section 8/26)")

        results = {name: strat.generate(df, pair, snapshot.regime, features) for name, strat in self.strategies.items()}
        ensemble_result = self.ensemble.combine(results)

        if ensemble_result.direction == Direction.NEUTRAL or ensemble_result.primary is None:
            return NoTradeReason(pair, f"ensemble result: {ensemble_result.overall_signal} "
                                        f"(score {ensemble_result.score:+.2f}) -- no actionable agreement")

        primary = ensemble_result.primary
        spread_model = SpreadModel(pair)
        quote = spread_model.quote_from_mid(float(df["close"].iloc[-1]), end, snapshot.regime)
        pip = spread_model.pip

        estimated_entry = (primary.entry_low + primary.entry_high) / 2
        nominal_risk = abs(estimated_entry - primary.stop_loss)
        nominal_reward = abs(primary.take_profit - estimated_entry)
        spread_price = quote.spread

        if nominal_risk <= 0:
            return NoTradeReason(pair, "degenerate risk distance from primary strategy, rejecting")

        spread_to_stop_ratio = spread_price / nominal_risk
        if spread_to_stop_ratio > config.MAX_SPREAD_TO_STOP_RATIO:
            return NoTradeReason(pair, f"spread ({spread_price / pip:.1f} pips) is "
                                        f"{spread_to_stop_ratio:.0%} of the stop distance, over the "
                                        f"{config.MAX_SPREAD_TO_STOP_RATIO:.0%} limit -- rejected per section 7")

        effective_risk = nominal_risk + spread_price
        effective_reward = max(0.0, nominal_reward - spread_price)
        risk_reward_after_costs = effective_reward / effective_risk if effective_risk > 0 else 0.0

        if risk_reward_after_costs < config.MIN_RR_AFTER_COSTS:
            return NoTradeReason(pair, f"risk/reward after spread costs ({risk_reward_after_costs:.2f}) is below "
                                        f"the {config.MIN_RR_AFTER_COSTS} floor -- rejected per section 7/26")

        news_penalty = self.news_filter.risk_penalty(pair, end)

        agreeing_expectancies = [
            self.db.strategy_expectancy(r.strategy) for r in ensemble_result.agreeing
        ]
        agreeing_expectancies = [e for e in agreeing_expectancies if e is not None]
        avg_expectancy = sum(agreeing_expectancies) / len(agreeing_expectancies) if agreeing_expectancies else None

        score = compute_signal_score(
            ensemble=ensemble_result, regime_snapshot=snapshot,
            risk_reward=risk_reward_after_costs, spread_pips=spread_price / pip,
            stop_distance_pips=nominal_risk / pip, avg_agreeing_expectancy=avg_expectancy,
            news_risk_penalty=news_penalty,
        )

        if score.score < self.min_signal_score:
            return NoTradeReason(pair, f"signal quality score {score.score:.0f} is below the "
                                        f"{self.min_signal_score:.0f} floor -- rejected per section 26")

        direction = ensemble_result.direction
        entry_price = fill_price(quote, direction.value, "ENTRY")
        if direction == Direction.BUY:
            sl = entry_price - nominal_risk
            tp1 = entry_price + nominal_reward
            tp2 = entry_price + nominal_reward * 1.5
        else:
            sl = entry_price + nominal_risk
            tp1 = entry_price - nominal_reward
            tp2 = entry_price - nominal_reward * 1.5

        rr2 = risk_reward_after_costs * 1.5

        account_equity, account_currency = self.current_account()
        position_size, position_size_error = None, None
        try:
            position_size = calculate_position_size(
                pair=pair, direction=direction.value, entry_price=entry_price, stop_loss=sl,
                account_equity=account_equity, risk_pct=config.RISK_PER_TRADE_PCT,
                account_currency=account_currency,
                price_lookup=self._price_lookup,
                leverage=config.DEFAULT_LEVERAGE,
            )
        except PositionSizingError as exc:
            position_size_error = str(exc)
            logger.warning("position sizing failed for %s: %s", pair, exc)

        msg = SignalMessage(
            pair=pair, direction=direction, overall_signal=ensemble_result.overall_signal, score=score,
            ensemble=ensemble_result, regime=snapshot,
            entry_low=min(primary.entry_low, primary.entry_high), entry_high=max(primary.entry_low, primary.entry_high),
            stop_loss=sl, take_profit_1=tp1, take_profit_2=tp2,
            risk_reward_1=risk_reward_after_costs, risk_reward_2=rr2,
            spread_pips=spread_price / pip, reason=primary.reason, pip=pip,
            position_size=position_size, position_size_error=position_size_error,
        )

        if self.db.recent_signal_exists(pair, direction.value, minutes=timeframe.minutes * 2):
            return NoTradeReason(pair, "duplicate: a signal for this pair/direction was already sent recently")

        signal_id = self.db.insert_signal({
            "pair": pair, "direction": direction.value, "overall_signal": ensemble_result.overall_signal,
            "score": score.score, "entry_low": msg.entry_low, "entry_high": msg.entry_high,
            "stop_loss": sl, "take_profit_1": tp1, "take_profit_2": tp2,
            "spread_pips": msg.spread_pips, "regime": snapshot.regime,
            "strategies_agree": {n: r.direction.value for n, r in results.items()}, "reason": primary.reason,
        })

        if self.notifier and self.notifier.is_configured:
            self.notifier.send(msg.format())

        return signal_id, msg
