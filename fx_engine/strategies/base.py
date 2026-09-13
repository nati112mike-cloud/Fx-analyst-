"""Common interface every strategy module must implement.

Per the plan's section 4 (Strategy Formalization): every strategy carries
an explicit, inspectable specification (conditions, SL/TP methodology,
regimes it should NOT trade) rather than being a black box, and every
signal it emits is a structured, auditable result -- never a bare
"buy"/"sell" string.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from enum import Enum

import pandas as pd


class Direction(str, Enum):
    BUY = "BUY"
    SELL = "SELL"
    NEUTRAL = "NEUTRAL"


@dataclass
class StrategySpec:
    name: str
    version: str
    market_conditions: str
    pairs: list[str]
    timeframes: list[str]
    indicators: list[str]
    entry_conditions: list[str]
    exit_conditions: list[str]
    stop_loss_method: str
    take_profit_method: str
    min_risk_reward: float
    max_holding_bars: int
    invalidation_conditions: list[str]
    excluded_regimes: list[str]
    required_data: list[str]
    known_weaknesses: list[str]

    def as_markdown(self) -> str:
        lines = [f"### {self.name} (v{self.version})", ""]
        for field_name in (
            "market_conditions", "pairs", "timeframes", "indicators",
            "entry_conditions", "exit_conditions", "stop_loss_method",
            "take_profit_method", "min_risk_reward", "max_holding_bars",
            "invalidation_conditions", "excluded_regimes", "required_data",
            "known_weaknesses",
        ):
            val = getattr(self, field_name)
            label = field_name.replace("_", " ").title()
            if isinstance(val, list):
                lines.append(f"**{label}:**")
                lines.extend(f"- {v}" for v in val)
            else:
                lines.append(f"**{label}:** {val}")
            lines.append("")
        return "\n".join(lines)


@dataclass
class StrategyResult:
    strategy: str
    version: str
    direction: Direction
    confidence: float  # 0-1, this strategy's own internal confidence -- NOT a win probability
    entry_low: float | None
    entry_high: float | None
    stop_loss: float | None
    take_profit: float | None
    reason: str
    regime: str
    timestamp: pd.Timestamp

    @property
    def is_actionable(self) -> bool:
        return self.direction != Direction.NEUTRAL and self.stop_loss is not None and self.take_profit is not None


def compute_tp(entry: float, sl: float, direction: Direction, rr: float) -> float:
    risk = abs(entry - sl)
    return entry + risk * rr if direction == Direction.BUY else entry - risk * rr


class BaseStrategy(ABC):
    spec: StrategySpec

    @abstractmethod
    def generate(self, df: pd.DataFrame, pair: str, regime: str, features: pd.DataFrame | None = None) -> StrategyResult:
        """Look ONLY at df.iloc[:i+1]-equivalent data (df passed in must
        already be truncated to "now" by the caller -- no peeking at future
        bars) and return a single StrategyResult for the most recent bar.

        `features`, if given, is a precomputed indicator frame (see
        fx_engine.features.compute_feature_frame) aligned to df's index --
        use it instead of recomputing indicators for performance. If None,
        the strategy must compute what it needs from df itself (this path
        exists for standalone/ad-hoc use, e.g. a single live evaluation).
        """
        raise NotImplementedError

    def neutral(self, df: pd.DataFrame, regime: str, reason: str) -> StrategyResult:
        return StrategyResult(
            strategy=self.spec.name, version=self.spec.version, direction=Direction.NEUTRAL,
            confidence=0.0, entry_low=None, entry_high=None, stop_loss=None, take_profit=None,
            reason=reason, regime=regime, timestamp=df.index[-1],
        )
