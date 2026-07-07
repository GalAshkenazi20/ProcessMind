"""Process discovery: build the real process model from the event log.

Produces the directly-follows graph (with frequencies and waiting times),
variant analysis, and per-activity statistics — the "as-is" picture of the
process, including paths that do not appear in the official procedure.
"""

from __future__ import annotations

from statistics import mean, median
from typing import Any

from .schema import Case, EventLog, percentile

START = "__start__"
END = "__end__"


def directly_follows(log: EventLog) -> dict[str, Any]:
    """Edges of the real process: frequency and waiting-time stats per transition."""
    edge_waits: dict[tuple[str, str], list[float]] = {}
    edge_cases: dict[tuple[str, str], set[str]] = {}
    for case in log.cases:
        pairs = [(START, case.events[0].activity)] + list(
            zip((e.activity for e in case.events), (e.activity for e in case.events[1:]))
        ) + [(case.events[-1].activity, END)]
        gaps = [0.0] + [
            (b.timestamp - a.timestamp).total_seconds() / 3600.0
            for a, b in zip(case.events, case.events[1:])
        ] + [0.0]
        for (src, dst), gap in zip(pairs, gaps):
            edge_waits.setdefault((src, dst), []).append(gap)
            edge_cases.setdefault((src, dst), set()).add(case.case_id)

    edges = []
    for (src, dst), waits in edge_waits.items():
        edges.append(
            {
                "source": src,
                "target": dst,
                "frequency": len(waits),
                "case_coverage": round(100 * len(edge_cases[(src, dst)]) / len(log.cases), 1),
                "wait_hours": {
                    "mean": round(mean(waits), 2),
                    "median": round(median(waits), 2),
                    "p90": round(percentile(waits, 90), 2),
                },
            }
        )
    edges.sort(key=lambda e: -e["frequency"])
    return {"nodes": activity_stats(log), "edges": edges}


def activity_stats(log: EventLog) -> list[dict[str, Any]]:
    stats: dict[str, dict[str, Any]] = {}
    for case in log.cases:
        prev_ts = None
        for e in case.events:
            s = stats.setdefault(
                e.activity,
                {"activity": e.activity, "frequency": 0, "cases": set(), "resources": {}, "waits": []},
            )
            s["frequency"] += 1
            s["cases"].add(case.case_id)
            s["resources"][e.resource] = s["resources"].get(e.resource, 0) + 1
            if prev_ts is not None:
                s["waits"].append((e.timestamp - prev_ts).total_seconds() / 3600.0)
            prev_ts = e.timestamp

    out = []
    for s in stats.values():
        waits = s["waits"] or [0.0]
        top_resource, top_count = max(s["resources"].items(), key=lambda kv: kv[1])
        out.append(
            {
                "activity": s["activity"],
                "frequency": s["frequency"],
                "case_coverage": round(100 * len(s["cases"]) / len(log.cases), 1),
                "mean_wait_before_hours": round(mean(waits), 2),
                "p90_wait_before_hours": round(percentile(waits, 90), 2),
                "resources": len(s["resources"]),
                "top_resource": top_resource,
                "top_resource_share": round(100 * top_count / s["frequency"], 1),
            }
        )
    out.sort(key=lambda n: -n["frequency"])
    return out


def variants(log: EventLog, limit: int = 20) -> dict[str, Any]:
    groups: dict[tuple[str, ...], list[Case]] = {}
    for case in log.cases:
        groups.setdefault(case.variant, []).append(case)
    ranked = sorted(groups.items(), key=lambda kv: -len(kv[1]))
    items = [
        {
            "path": list(path),
            "cases": len(cs),
            "share_pct": round(100 * len(cs) / len(log.cases), 1),
            "mean_duration_hours": round(mean(c.duration_hours for c in cs), 2),
            "mean_handoffs": round(mean(c.handoffs for c in cs), 2),
        }
        for path, cs in ranked[:limit]
    ]
    happy = ranked[0] if ranked else None
    return {
        "total_variants": len(groups),
        "happy_path_share_pct": round(100 * len(happy[1]) / len(log.cases), 1) if happy else 0,
        "variants": items,
    }
