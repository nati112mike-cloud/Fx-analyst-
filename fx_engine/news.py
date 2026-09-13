"""Economic calendar / news-risk filter.

Section 15 is explicit: "When reliable data is unavailable, do NOT pretend
it is available." No live economic calendar API is wired up here (that's a
deliberate scope decision, not an oversight -- see docs/LIMITATIONS.md for
what a real integration would need). This module instead:

- defines the NewsEvent/NewsFilter shape the rest of the system expects,
- can load events from a plain CSV you maintain or export from any
  calendar source (columns: timestamp_utc, currency, title, impact),
- and when no calendar has been loaded, is_high_impact_nearby() always
  returns False WHILE ALSO setting `.calendar_loaded = False` so callers
  (see signal_engine.py) can visibly flag "news filter inactive" rather
  than silently implying every signal was checked against real events.
"""
from __future__ import annotations

import csv
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone


@dataclass
class NewsEvent:
    timestamp: datetime
    currency: str
    title: str
    impact: str  # low | medium | high


@dataclass
class NewsFilter:
    events: list[NewsEvent] = field(default_factory=list)
    calendar_loaded: bool = False

    def load_from_csv(self, path: str) -> None:
        events = []
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                ts = datetime.fromisoformat(row["timestamp_utc"]).replace(tzinfo=timezone.utc)
                events.append(NewsEvent(timestamp=ts, currency=row["currency"].upper(),
                                         title=row["title"], impact=row["impact"].lower()))
        self.events = events
        self.calendar_loaded = True

    def _currencies_for_pair(self, pair: str) -> tuple[str, str]:
        return pair[:3].upper(), pair[3:6].upper()

    def is_high_impact_nearby(self, pair: str, at: datetime, window_minutes: int = 60) -> bool:
        if not self.calendar_loaded:
            return False
        base, quote = self._currencies_for_pair(pair)
        window = timedelta(minutes=window_minutes)
        for ev in self.events:
            if ev.impact != "high":
                continue
            if ev.currency not in (base, quote):
                continue
            if abs((ev.timestamp - at)) <= window:
                return True
        return False

    def risk_penalty(self, pair: str, at: datetime) -> float:
        """0.0 = no penalty (either no event, or no calendar connected --
        those are NOT the same thing, see calendar_loaded), up to 1.0 for
        an imminent high-impact event on this pair's currencies."""
        if not self.calendar_loaded:
            return 0.0
        return 1.0 if self.is_high_impact_nearby(pair, at, window_minutes=30) else (
            0.4 if self.is_high_impact_nearby(pair, at, window_minutes=120) else 0.0
        )
