"""0-100 Signal Quality Score.

Per Section 11: this is explicitly NOT a win probability. It is a
transparency tool -- every component is visible so you can see WHY a
signal scored the way it did, and disagree with the weighting if you want.
"""
from __future__ import annotations

from dataclasses import dataclass

from fx_engine import config
from fx_engine.ensemble import EnsembleResult
from fx_engine.regime import RegimeSnapshot

# component weights, must sum to 1.0
WEIGHTS = {
    "agreement": 0.28,
    "risk_reward": 0.20,
    "spread_quality": 0.17,
    "regime_confidence": 0.15,
    "historical_expectancy": 0.15,
    "news_risk": 0.05,
}


@dataclass
class SignalScore:
    score: float  # 0-100
    components: dict[str, float]  # each already 0-1, for transparency
    label: str = "SIGNAL QUALITY SCORE -- not a win probability"

    def as_dict(self) -> dict:
        return {"score": round(self.score, 1), "components": {k: round(v, 3) for k, v in self.components.items()},
                "label": self.label}


def _clamp01(x: float) -> float:
    return max(0.0, min(1.0, x))


def compute_signal_score(
    ensemble: EnsembleResult,
    regime_snapshot: RegimeSnapshot,
    risk_reward: float,
    spread_pips: float,
    stop_distance_pips: float,
    avg_agreeing_expectancy: float | None,
    news_risk_penalty: float = 0.0,  # 0 = no event risk, 1 = major event imminent
) -> SignalScore:
    agreement = _clamp01(abs(ensemble.score))

    rr_component = _clamp01((risk_reward - 1.0) / 3.0)

    spread_ratio = (spread_pips / stop_distance_pips) if stop_distance_pips > 0 else 1.0
    spread_component = _clamp01(1.0 - spread_ratio / config.MAX_SPREAD_TO_STOP_RATIO)

    regime_component = _clamp01(regime_snapshot.confidence)

    if avg_agreeing_expectancy is None:
        expectancy_component = 0.4  # unvalidated -- deliberately mediocre, not rewarded
    else:
        expectancy_component = _clamp01(0.5 + avg_agreeing_expectancy)

    news_component = _clamp01(1.0 - news_risk_penalty)

    components = {
        "agreement": agreement,
        "risk_reward": rr_component,
        "spread_quality": spread_component,
        "regime_confidence": regime_component,
        "historical_expectancy": expectancy_component,
        "news_risk": news_component,
    }
    total = sum(WEIGHTS[k] * v for k, v in components.items())
    return SignalScore(score=round(total * 100, 1), components=components)
