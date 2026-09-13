"""Paper broker adapter: simulates fills using a HistoricalDataProvider's
latest data plus SpreadModel, and tracks a virtual account. Never touches
a real broker. This is what paper_trading.py runs against continuously
before any real capital is ever considered (Section 19).
"""
from __future__ import annotations

from datetime import datetime, timezone

from fx_engine.broker.base import AccountInfo, BrokerAdapter
from fx_engine.costs import Quote, SpreadModel
from fx_engine.data.models import Timeframe
from fx_engine.data.providers import HistoricalDataProvider


class PaperBrokerAdapter(BrokerAdapter):
    name = "paper"

    def __init__(self, data_provider: HistoricalDataProvider, starting_balance: float = 10_000.0):
        self.data_provider = data_provider
        self.balance = starting_balance
        self.equity = starting_balance
        self._spread_models: dict[str, SpreadModel] = {}

    def is_connected(self) -> bool:
        return True

    def _spread_model(self, pair: str) -> SpreadModel:
        if pair not in self._spread_models:
            self._spread_models[pair] = SpreadModel(pair)
        return self._spread_models[pair]

    def get_quote(self, pair: str) -> Quote:
        now = datetime.now(timezone.utc)
        df = self.data_provider.get_ohlc(pair, Timeframe.M15, now.replace(hour=0, minute=0), now)
        mid = float(df["close"].iloc[-1])
        return self._spread_model(pair).quote_from_mid(mid, now)

    def get_account_info(self) -> AccountInfo:
        return AccountInfo(balance=self.balance, equity=self.equity, currency="USD", leverage=None)
