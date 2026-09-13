from fx_engine.strategies.base import BaseStrategy, Direction, StrategyResult, StrategySpec
from fx_engine.strategies.breakout import BreakoutStrategy
from fx_engine.strategies.mean_reversion import MeanReversionStrategy
from fx_engine.strategies.momentum import MomentumStrategy
from fx_engine.strategies.multi_timeframe import MultiTimeframeStrategy
from fx_engine.strategies.price_action import PriceActionStrategy
from fx_engine.strategies.trend_pullback import TrendPullbackStrategy
from fx_engine.strategies.volatility import VolatilityStrategy

ALL_STRATEGIES: dict[str, type[BaseStrategy]] = {
    "trend_pullback": TrendPullbackStrategy,
    "momentum": MomentumStrategy,
    "breakout": BreakoutStrategy,
    "mean_reversion": MeanReversionStrategy,
    "price_action": PriceActionStrategy,
    "volatility": VolatilityStrategy,
    "multi_timeframe": MultiTimeframeStrategy,
}


def build_all() -> dict[str, BaseStrategy]:
    return {name: cls() for name, cls in ALL_STRATEGIES.items()}
