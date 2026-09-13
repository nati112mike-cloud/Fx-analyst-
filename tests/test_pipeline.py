"""Lightweight regression tests for the core pipeline. Run with:

    python -m unittest discover -s tests

These use the synthetic data provider only (no network needed) and exist
to catch the class of bug this project already hit once (the O(n^2)
lookahead-truncation issue, and the price_action boolean-index alignment
bug) -- not to validate trading performance, which needs real data.
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fx_engine.backtest.engine import BacktestEngine
from fx_engine.backtest.metrics import compute_metrics
from fx_engine.backtest.walk_forward import run_walk_forward
from fx_engine.costs import Quote, SpreadModel, fill_price
from fx_engine.data.models import Timeframe
from fx_engine.data.providers import SyntheticDataProvider
from fx_engine.ensemble import StrategyEnsemble
from fx_engine.features import compute_feature_frame
from fx_engine.regime import compute_regime_series
from fx_engine.strategies import build_all


class TestSyntheticData(unittest.TestCase):
    def test_deterministic(self):
        p = SyntheticDataProvider()
        start, end = datetime(2022, 1, 1, tzinfo=timezone.utc), datetime(2022, 6, 1, tzinfo=timezone.utc)
        df1 = p.get_ohlc("EURUSD", Timeframe.H4, start, end)
        df2 = p.get_ohlc("EURUSD", Timeframe.H4, start, end)
        self.assertTrue((df1["close"] == df2["close"]).all())

    def test_ohlc_invariants(self):
        p = SyntheticDataProvider()
        df = p.get_ohlc("GBPUSD", Timeframe.H4, datetime(2023, 1, 1, tzinfo=timezone.utc),
                         datetime(2023, 6, 1, tzinfo=timezone.utc))
        self.assertTrue((df["high"] >= df[["open", "close"]].max(axis=1)).all())
        self.assertTrue((df["low"] <= df[["open", "close"]].min(axis=1)).all())


class TestCosts(unittest.TestCase):
    def test_fill_price_direction(self):
        q = Quote(bid=1.1000, ask=1.1002)
        self.assertEqual(fill_price(q, "BUY", "ENTRY"), q.ask)
        self.assertEqual(fill_price(q, "BUY", "EXIT"), q.bid)
        self.assertEqual(fill_price(q, "SELL", "ENTRY"), q.bid)
        self.assertEqual(fill_price(q, "SELL", "EXIT"), q.ask)

    def test_spread_widens_at_rollover(self):
        sm = SpreadModel("EURUSD")
        normal = sm.estimate_spread_pips(datetime(2023, 6, 6, 10, 0, tzinfo=timezone.utc))
        rollover = sm.estimate_spread_pips(datetime(2023, 6, 6, 22, 0, tzinfo=timezone.utc))
        self.assertGreater(rollover, normal)


class TestPipelineIntegration(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.provider = SyntheticDataProvider()
        cls.df = cls.provider.get_ohlc(
            "EURUSD", Timeframe.H4, datetime(2022, 1, 1, tzinfo=timezone.utc), datetime(2023, 1, 1, tzinfo=timezone.utc),
        )

    def test_features_and_regime_no_exceptions(self):
        feats = compute_feature_frame(self.df)
        regime = compute_regime_series(self.df, feats)
        self.assertEqual(len(feats), len(self.df))
        self.assertEqual(len(regime), len(self.df))
        self.assertTrue(set(regime["regime"].unique()).issubset(
            {"strong_bull_trend", "strong_bear_trend", "weak_trend", "range",
             "high_volatility", "low_volatility", "breakout", "unclear"}))

    def test_all_strategies_run_without_exceptions(self):
        engine = BacktestEngine(warmup_bars=210)
        for name, strat in build_all().items():
            result = engine.run(strat, self.df, "EURUSD")
            metrics = compute_metrics(result.trades)
            # not asserting profitability (meaningless on synthetic data) --
            # only that the pipeline produces well-formed, consistent numbers
            self.assertGreaterEqual(metrics.total_trades, 0)
            if metrics.total_trades > 0:
                self.assertTrue(0.0 <= metrics.win_rate <= 1.0)

    def test_walk_forward_produces_a_verdict(self):
        strat = build_all()["price_action"]
        report = run_walk_forward(strat, self.df, "EURUSD")
        self.assertIn(report.verdict, {
            "CONSISTENT", "REJECTED", "OVERFIT_OR_DEGRADED", "WEAK_OUT_OF_SAMPLE", "INCONCLUSIVE",
        })

    def test_ensemble_never_fabricates_agreement(self):
        feats = compute_feature_frame(self.df)
        regime = compute_regime_series(self.df, feats)
        current_regime = str(regime["regime"].iloc[-1])
        results = {name: strat.generate(self.df, "EURUSD", current_regime, feats)
                   for name, strat in build_all().items()}
        ensemble = StrategyEnsemble().combine(results)
        if ensemble.overall_signal != "NO_TRADE":
            self.assertGreaterEqual(len(ensemble.agreeing), 2)


if __name__ == "__main__":
    unittest.main()
