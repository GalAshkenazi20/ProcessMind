"""Impact measurement: compare the process before and after a change.

Given two logs (or one log split at a change date), report whether handling
time, transfers, rework and throughput actually improved — closing the loop on
each recommendation. Outcomes are recorded in the knowledge base so the system
learns which kinds of changes work.
"""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from statistics import mean, median
from typing import Any

from .schema import EventLog, percentile


def compare_logs(before: EventLog, after: EventLog) -> dict[str, Any]:
    metrics = {}
    for key, fn, better in [
        ("median_duration_hours", lambda l: median(l.durations), "lower"),
        ("mean_duration_hours", lambda l: mean(l.durations), "lower"),
        ("p90_duration_hours", lambda l: percentile(l.durations, 90), "lower"),
        ("mean_handoffs", lambda l: mean(c.handoffs for c in l.cases), "lower"),
        ("rework_case_pct", lambda l: 100 * sum(1 for c in l.cases if c.rework_count > 0) / len(l.cases), "lower"),
        ("cases_per_week", _cases_per_week, "higher"),
    ]:
        b, a = fn(before), fn(after)
        change = 100 * (a - b) / b if b else 0.0
        improved = change < 0 if better == "lower" else change > 0
        metrics[key] = {
            "before": round(b, 2),
            "after": round(a, 2),
            "change_pct": round(change, 1),
            "improved": improved,
        }
    improved_n = sum(1 for m in metrics.values() if m["improved"])
    return {
        "before_cases": len(before.cases),
        "after_cases": len(after.cases),
        "metrics": metrics,
        "verdict": (
            "improved" if improved_n >= 4 else "mixed" if improved_n >= 2 else "not improved"
        ),
    }


def split_log(log: EventLog, split_at: datetime) -> tuple[EventLog, EventLog]:
    before = EventLog([e for c in log.cases if c.start < split_at for e in c.events], name=f"{log.name}:before")
    after = EventLog([e for c in log.cases if c.start >= split_at for e in c.events], name=f"{log.name}:after")
    if not before.cases or not after.cases:
        raise ValueError("Split date leaves one side empty — choose a date inside the log's time range.")
    return before, after


def _cases_per_week(log: EventLog) -> float:
    span_days = (log.cases[-1].start - log.cases[0].start).days or 1
    return len(log.cases) / (span_days / 7)


class KnowledgeBase:
    """Records recommendations and measured outcomes across analyses.

    Over time this tells the system which change types actually pay off, so
    repeatedly-failing suggestions can be ranked down.
    """

    def __init__(self, path: Path):
        self.path = path
        self.entries: list[dict[str, Any]] = []
        if path.exists():
            self.entries = json.loads(path.read_text())

    def record(self, entry: dict[str, Any]) -> dict[str, Any]:
        entry = {**entry, "recorded_at": datetime.now().isoformat(timespec="seconds")}
        self.entries.append(entry)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(self.entries, indent=2, default=str))
        return entry

    def effectiveness(self) -> dict[str, Any]:
        by_type: dict[str, dict[str, int]] = {}
        for e in self.entries:
            if "verdict" not in e:
                continue
            t = e.get("change_type", "unknown")
            s = by_type.setdefault(t, {"improved": 0, "mixed": 0, "not improved": 0})
            s[e["verdict"]] = s.get(e["verdict"], 0) + 1
        return {"total_recorded": len(self.entries), "by_change_type": by_type}
