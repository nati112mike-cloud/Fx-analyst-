"""Offline validation of YahooFinanceProvider against a realistic mocked
Yahoo chart API response -- this project's dev sandbox has no outbound
access to query1.finance.yahoo.com (confirmed: org network policy denies
the CONNECT, not a transient failure), so this is how the parsing,
chunking, retry, and error-handling logic gets verified before you ever
run it against the real endpoint. See docs/LIMITATIONS.md.

Run with: python -m unittest discover -s tests
"""
from __future__ import annotations

import sys
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import requests

from fx_engine.data.models import Timeframe
from fx_engine.data.providers import YahooFinanceProvider


def _make_payload(start: datetime, n_bars: int, step_seconds: int, base_price: float = 1.0850):
    """Build a JSON payload shaped exactly like Yahoo's real
    /v8/finance/chart/ response schema."""
    ts = [int(start.timestamp()) + i * step_seconds for i in range(n_bars)]
    closes = [base_price + 0.0001 * i for i in range(n_bars)]
    opens = [c - 0.0002 for c in closes]
    highs = [c + 0.0005 for c in closes]
    lows = [c - 0.0005 for c in closes]
    volumes = [1000 + i for i in range(n_bars)]
    return {
        "chart": {
            "result": [{
                "timestamp": ts,
                "indicators": {"quote": [{"open": opens, "high": highs, "low": lows, "close": closes, "volume": volumes}]},
            }],
            "error": None,
        }
    }


def _mock_response(payload: dict, status_code: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status_code
    resp.json.return_value = payload
    if status_code >= 400:
        resp.raise_for_status.side_effect = requests.exceptions.HTTPError(f"{status_code} error", response=resp)
    else:
        resp.raise_for_status.side_effect = None
    return resp


class TestYahooProviderParsing(unittest.TestCase):
    def setUp(self):
        self.provider = YahooFinanceProvider()
        self.sleep_patch = patch("fx_engine.data.providers.time.sleep", return_value=None)
        self.sleep_patch.start()
        self.addCleanup(self.sleep_patch.stop)

    @patch("requests.get")
    def test_daily_single_chunk_parses_correctly(self, mock_get):
        start = datetime(2023, 1, 2, tzinfo=timezone.utc)
        payload = _make_payload(start, n_bars=30, step_seconds=86400)
        mock_get.return_value = _mock_response(payload)

        df = self.provider.get_ohlc("EURUSD", Timeframe.D1, start, start + timedelta(days=30))

        self.assertEqual(mock_get.call_count, 1)
        called_url = mock_get.call_args.args[0] if mock_get.call_args.args else mock_get.call_args.kwargs["url"]
        self.assertIn("EURUSD=X", called_url)
        self.assertEqual(mock_get.call_args.kwargs["params"]["interval"], "1d")
        self.assertEqual(list(df.columns), ["open", "high", "low", "close", "volume"])
        self.assertEqual(len(df), 30)
        self.assertTrue(df.index.is_monotonic_increasing)
        self.assertAlmostEqual(df["close"].iloc[0], 1.0850, places=4)

    @patch("requests.get")
    def test_h4_request_resamples_from_60m(self, mock_get):
        start = datetime(2023, 1, 2, tzinfo=timezone.utc)
        # 40 hourly bars -> should resample down to ~10 4-hour bars
        payload = _make_payload(start, n_bars=40, step_seconds=3600)
        mock_get.return_value = _mock_response(payload)

        df = self.provider.get_ohlc("GBPUSD", Timeframe.H4, start, start + timedelta(hours=40))

        self.assertEqual(mock_get.call_args.kwargs["params"]["interval"], "60m")
        self.assertLess(len(df), 40)
        self.assertGreater(len(df), 5)

    @patch("requests.get")
    def test_empty_result_raises_valueerror_not_silent_empty_frame(self, mock_get):
        mock_get.return_value = _mock_response({"chart": {"result": None, "error": {"description": "No data"}}})
        with self.assertRaises(ValueError):
            self.provider.get_ohlc("EURUSD", Timeframe.D1,
                                    datetime(2023, 1, 1, tzinfo=timezone.utc),
                                    datetime(2023, 2, 1, tzinfo=timezone.utc))

    @patch("requests.get")
    def test_transient_failure_then_success_recovers(self, mock_get):
        start = datetime(2023, 1, 2, tzinfo=timezone.utc)
        payload = _make_payload(start, n_bars=10, step_seconds=86400)
        mock_get.side_effect = [
            requests.exceptions.ConnectionError("simulated transient failure"),
            _mock_response(payload),
        ]
        df = self.provider.get_ohlc("EURUSD", Timeframe.D1, start, start + timedelta(days=10))
        self.assertEqual(mock_get.call_count, 2)
        self.assertEqual(len(df), 10)

    @patch("requests.get")
    def test_persistent_failure_raises_connectionerror_with_sandbox_hint(self, mock_get):
        mock_get.side_effect = requests.exceptions.ConnectionError("CONNECT tunnel failed, response 403")
        with self.assertRaises(ConnectionError) as ctx:
            self.provider.get_ohlc("EURUSD", Timeframe.D1,
                                    datetime(2023, 1, 1, tzinfo=timezone.utc),
                                    datetime(2023, 2, 1, tzinfo=timezone.utc))
        self.assertEqual(mock_get.call_count, YahooFinanceProvider._RETRY_ATTEMPTS)
        self.assertIn("network policy", str(ctx.exception))

    @patch("requests.get")
    def test_multiyear_h1_request_is_chunked_and_concatenated(self, mock_get):
        # 3 years exceeds the ~728-day max lookback for 60m bars -> must chunk.
        start = datetime(2021, 1, 1, tzinfo=timezone.utc)
        end = datetime(2024, 1, 1, tzinfo=timezone.utc)

        def side_effect(*args, **kwargs):
            p1 = kwargs["params"]["period1"]
            p2 = kwargs["params"]["period2"]
            chunk_start = datetime.fromtimestamp(p1, tz=timezone.utc)
            n_hours = max(1, (p2 - p1) // 3600)
            return _mock_response(_make_payload(chunk_start, n_bars=int(min(n_hours, 500)), step_seconds=3600))

        mock_get.side_effect = side_effect
        df = self.provider.get_ohlc("EURUSD", Timeframe.H1, start, end)

        self.assertGreaterEqual(mock_get.call_count, 2, "a 3-year 60m request must be split into multiple chunks")
        self.assertTrue(df.index.is_monotonic_increasing)
        self.assertFalse(df.index.duplicated().any())


if __name__ == "__main__":
    unittest.main()
