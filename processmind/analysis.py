"""Analysis engine: find concrete, evidence-backed problems in the real process.

Every finding carries the numbers that justify it, so recommendations are
data-based rather than generic ("30% of cases return to the customer because
X is missing") — the insights layer turns these into plain language.
"""

from __future__ import annotations

from statistics import mean, median
from typing import Any

from .discovery import END, START, directly_follows
from .schema import EventLog, percentile


def analyze(log: EventLog) -> list[dict[str, Any]]:
    findings: list[dict[str, Any]] = []
    findings += find_bottlenecks(log)
    findings += find_rework(log)
    findings += find_ping_pong(log)
    findings += find_single_person_dependencies(log)
    findings += find_duration_outliers(log)
    findings += find_attribute_delay_drivers(log)
    order = {"high": 0, "medium": 1, "low": 2}
    findings.sort(key=lambda f: (order[f["severity"]], -f.get("impact_score", 0)))
    return findings


def _severity(impact: float) -> str:
    return "high" if impact >= 0.66 else "medium" if impact >= 0.33 else "low"


def find_bottlenecks(log: EventLog, top: int = 5) -> list[dict[str, Any]]:
    """Transitions where cases spend a disproportionate share of total time."""
    dfg = directly_follows(log)
    total_mean = mean(log.durations) or 1.0
    findings = []
    real_edges = [
        e for e in dfg["edges"] if e["source"] != START and e["target"] != END and e["frequency"] >= max(3, len(log.cases) * 0.05)
    ]
    for e in sorted(real_edges, key=lambda e: -e["wait_hours"]["mean"] * e["frequency"])[:top]:
        share = e["wait_hours"]["mean"] / total_mean
        if share < 0.10:
            continue
        impact = min(1.0, share * (e["case_coverage"] / 100))
        findings.append(
            {
                "type": "bottleneck",
                "severity": _severity(impact),
                "impact_score": round(impact, 2),
                "title": f"Waiting before '{e['target']}' (after '{e['source']}')",
                "evidence": {
                    "transition": [e["source"], e["target"]],
                    "mean_wait_hours": e["wait_hours"]["mean"],
                    "p90_wait_hours": e["wait_hours"]["p90"],
                    "case_coverage_pct": e["case_coverage"],
                    "share_of_mean_case_duration_pct": round(100 * share, 1),
                },
            }
        )
    return findings


def find_rework(log: EventLog) -> list[dict[str, Any]]:
    """Activities that are repeated within the same case (loops back to earlier stages)."""
    repeat_cases: dict[str, list[str]] = {}
    extra_hours: dict[str, list[float]] = {}
    for case in log.cases:
        seen: dict[str, int] = {}
        for i, e in enumerate(case.events):
            if e.activity in seen:
                repeat_cases.setdefault(e.activity, []).append(case.case_id)
                gap = (e.timestamp - case.events[i - 1].timestamp).total_seconds() / 3600.0
                extra_hours.setdefault(e.activity, []).append(gap)
            seen[e.activity] = i
    findings = []
    for act, cases in repeat_cases.items():
        unique = set(cases)
        pct = 100 * len(unique) / len(log.cases)
        if pct < 5:
            continue
        impact = min(1.0, pct / 50)
        findings.append(
            {
                "type": "rework",
                "severity": _severity(impact),
                "impact_score": round(impact, 2),
                "title": f"Cases loop back to '{act}'",
                "evidence": {
                    "activity": act,
                    "affected_cases": len(unique),
                    "affected_pct": round(pct, 1),
                    "mean_extra_hours_per_loop": round(mean(extra_hours[act]), 2),
                    "example_cases": sorted(unique)[:5],
                },
            }
        )
    findings.sort(key=lambda f: -f["evidence"]["affected_pct"])
    return findings


def find_ping_pong(log: EventLog) -> list[dict[str, Any]]:
    """Cases bouncing back and forth between the same two resources (A→B→A)."""
    bounce_cases = []
    pair_counts: dict[tuple[str, str], int] = {}
    for case in log.cases:
        rs = case.resources
        bounced = False
        for a, b, c in zip(rs, rs[1:], rs[2:]):
            if a == c and a != b:
                bounced = True
                pair_counts[tuple(sorted((a, b)))] = pair_counts.get(tuple(sorted((a, b))), 0) + 1
        if bounced:
            bounce_cases.append(case.case_id)
    if not bounce_cases:
        return []
    pct = 100 * len(bounce_cases) / len(log.cases)
    if pct < 5:
        return []
    top_pairs = sorted(pair_counts.items(), key=lambda kv: -kv[1])[:3]
    impact = min(1.0, pct / 60)
    return [
        {
            "type": "ping_pong",
            "severity": _severity(impact),
            "impact_score": round(impact, 2),
            "title": "Work bounces back and forth between the same people",
            "evidence": {
                "affected_cases": len(bounce_cases),
                "affected_pct": round(pct, 1),
                "top_pairs": [{"pair": list(p), "bounces": n} for p, n in top_pairs],
            },
        }
    ]


def find_single_person_dependencies(log: EventLog) -> list[dict[str, Any]]:
    """Stages where one person performs almost all the work — a key-person risk."""
    findings = []
    per_activity: dict[str, dict[str, int]] = {}
    for e in log.events:
        per_activity.setdefault(e.activity, {})
        per_activity[e.activity][e.resource] = per_activity[e.activity].get(e.resource, 0) + 1
    for act, resources in per_activity.items():
        total = sum(resources.values())
        if total < max(5, len(log.cases) * 0.1):
            continue
        top_res, top_n = max(resources.items(), key=lambda kv: kv[1])
        share = top_n / total
        if share < 0.85 or len(resources) > 2 or top_res == "unknown":
            continue
        coverage = total / len(log.cases)
        impact = min(1.0, share * min(1.0, coverage))
        findings.append(
            {
                "type": "single_person_dependency",
                "severity": _severity(impact),
                "impact_score": round(impact, 2),
                "title": f"'{act}' depends almost entirely on {top_res}",
                "evidence": {
                    "activity": act,
                    "resource": top_res,
                    "share_pct": round(100 * share, 1),
                    "executions": total,
                },
            }
        )
    return findings


def find_duration_outliers(log: EventLog) -> list[dict[str, Any]]:
    durs = log.durations
    p90 = percentile(durs, 90)
    med = median(durs) or 1.0
    if p90 < med * 3:
        return []
    slow = [c for c in log.cases if c.duration_hours >= p90]
    # what do the slow cases have in common? most-frequent extra activity
    fast_acts = _activity_rates([c for c in log.cases if c.duration_hours < p90])
    slow_acts = _activity_rates(slow)
    diffs = sorted(
        ((act, slow_acts.get(act, 0) - fast_acts.get(act, 0)) for act in slow_acts),
        key=lambda kv: -kv[1],
    )
    marker = diffs[0] if diffs and diffs[0][1] > 0.15 else None
    return [
        {
            "type": "duration_outliers",
            "severity": "medium",
            "impact_score": round(min(1.0, (p90 / med) / 10), 2),
            "title": "The slowest 10% of cases take far longer than typical ones",
            "evidence": {
                "median_hours": round(med, 1),
                "p90_hours": round(p90, 1),
                "ratio": round(p90 / med, 1),
                "slow_cases": len(slow),
                "distinguishing_activity": (
                    {"activity": marker[0], "rate_gap_pct": round(100 * marker[1], 1)} if marker else None
                ),
            },
        }
    ]


def find_attribute_delay_drivers(log: EventLog) -> list[dict[str, Any]]:
    """Case attributes (e.g. missing document flags, categories) linked to long durations."""
    med = median(log.durations) or 1.0
    findings = []
    values: dict[tuple[str, str], list[float]] = {}
    for case in log.cases:
        for k, v in case.attributes.items():
            if isinstance(v, (dict, list)):
                continue
            values.setdefault((k, str(v)), []).append(case.duration_hours)
    for (attr, val), durs in values.items():
        if len(durs) < max(5, len(log.cases) * 0.05) or len(durs) > len(log.cases) * 0.95:
            continue
        ratio = mean(durs) / (mean(log.durations) or 1.0)
        if ratio < 1.5:
            continue
        impact = min(1.0, (ratio - 1) * len(durs) / len(log.cases))
        findings.append(
            {
                "type": "attribute_delay_driver",
                "severity": _severity(impact),
                "impact_score": round(impact, 2),
                "title": f"Cases with {attr}={val} take {ratio:.1f}x longer than average",
                "evidence": {
                    "attribute": attr,
                    "value": val,
                    "cases": len(durs),
                    "share_pct": round(100 * len(durs) / len(log.cases), 1),
                    "mean_duration_hours": round(mean(durs), 1),
                    "overall_mean_hours": round(mean(log.durations), 1),
                },
            }
        )
    findings.sort(key=lambda f: -f["impact_score"])
    return findings[:5]


def _activity_rates(cases) -> dict[str, float]:
    if not cases:
        return {}
    counts: dict[str, int] = {}
    for c in cases:
        for act in set(c.variant):
            counts[act] = counts.get(act, 0) + 1
    return {act: n / len(cases) for act, n in counts.items()}
