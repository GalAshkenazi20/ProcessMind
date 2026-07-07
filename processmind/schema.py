"""Core data model: events, cases and event logs in a unified format."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from statistics import mean, median
from typing import Any, Iterable


@dataclass(frozen=True)
class Event:
    case_id: str
    activity: str
    timestamp: datetime
    resource: str = "unknown"
    attributes: dict[str, Any] = field(default_factory=dict, compare=False)


@dataclass
class Case:
    case_id: str
    events: list[Event]

    def __post_init__(self) -> None:
        self.events.sort(key=lambda e: e.timestamp)

    @property
    def variant(self) -> tuple[str, ...]:
        return tuple(e.activity for e in self.events)

    @property
    def start(self) -> datetime:
        return self.events[0].timestamp

    @property
    def end(self) -> datetime:
        return self.events[-1].timestamp

    @property
    def duration_hours(self) -> float:
        return (self.end - self.start).total_seconds() / 3600.0

    @property
    def resources(self) -> list[str]:
        return [e.resource for e in self.events]

    @property
    def handoffs(self) -> int:
        """Number of times the case moved between different resources."""
        return sum(
            1
            for a, b in zip(self.resources, self.resources[1:])
            if a != b
        )

    @property
    def rework_count(self) -> int:
        """Number of events that repeat an activity already seen in the case."""
        seen: set[str] = set()
        count = 0
        for e in self.events:
            if e.activity in seen:
                count += 1
            seen.add(e.activity)
        return count

    @property
    def attributes(self) -> dict[str, Any]:
        """Case-level attributes: merged from events, first value wins."""
        merged: dict[str, Any] = {}
        for e in self.events:
            for k, v in e.attributes.items():
                merged.setdefault(k, v)
        return merged


class EventLog:
    """A collection of cases built from raw events."""

    def __init__(self, events: Iterable[Event], name: str = "log"):
        self.name = name
        by_case: dict[str, list[Event]] = {}
        for e in events:
            by_case.setdefault(e.case_id, []).append(e)
        self.cases: list[Case] = [
            Case(cid, evs) for cid, evs in by_case.items() if len(evs) >= 2
        ]
        self.cases.sort(key=lambda c: c.start)

    @property
    def events(self) -> list[Event]:
        return [e for c in self.cases for e in c.events]

    @cached_property
    def activities(self) -> list[str]:
        seen: dict[str, None] = {}
        for e in self.events:
            seen.setdefault(e.activity)
        return list(seen)

    @cached_property
    def durations(self) -> list[float]:
        return [c.duration_hours for c in self.cases]

    def summary(self) -> dict[str, Any]:
        durs = self.durations
        return {
            "name": self.name,
            "cases": len(self.cases),
            "events": len(self.events),
            "activities": len(self.activities),
            "resources": len({e.resource for e in self.events}),
            "start": min(e.timestamp for e in self.events).isoformat(),
            "end": max(e.timestamp for e in self.events).isoformat(),
            "duration_hours": {
                "mean": round(mean(durs), 2),
                "median": round(median(durs), 2),
                "p90": round(percentile(durs, 90), 2),
                "max": round(max(durs), 2),
            },
            "mean_handoffs": round(mean(c.handoffs for c in self.cases), 2),
            "rework_case_pct": round(
                100 * sum(1 for c in self.cases if c.rework_count > 0) / len(self.cases), 1
            ),
        }


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    s = sorted(values)
    k = (len(s) - 1) * p / 100
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)
