"""Work design: decide, per process stage, who should do the work.

Four execution modes, in line with the principle that not everything should
be automated and not everything should stay manual:

  traditional_automation — deterministic, rule-based steps (field checks,
                           routing, notifications)
  ai_with_review         — judgment tasks where AI drafts and a person
                           approves (classification, triage, drafting)
  ai_autonomous          — low-risk judgment tasks with cheap reversal
  human_only             — sensitive decisions and accountability points
                           (approvals, payments, exceptions)

Scoring uses observable signals from the log (repetitiveness, timing variance,
rework association, volume) plus semantic hints from activity names. Risky
names always force human involvement regardless of the statistics.
"""

from __future__ import annotations

import re
from statistics import mean, pstdev
from typing import Any

from .schema import EventLog

RISK_KEYWORDS = ["approve", "authoriz", "payment", "pay", "sign", "legal", "contract", "escalat", "refund"]
RULE_KEYWORDS = ["check", "validate", "verify", "route", "assign", "notify", "close", "open", "log", "record"]
JUDGMENT_KEYWORDS = ["classif", "triage", "categor", "review", "assess", "diagnos", "respond", "draft", "investigat", "analy"]


def _matches(name: str, keywords: list[str]) -> bool:
    # word-boundary prefixes so 'sign' doesn't match 'Assign', 'pay' not 'prepay'
    return any(re.search(rf"\b{k}", name) for k in keywords)


def design_work(log: EventLog) -> list[dict[str, Any]]:
    per_act: dict[str, dict[str, Any]] = {}
    for case in log.cases:
        prev = None
        for e in case.events:
            s = per_act.setdefault(
                e.activity,
                {"count": 0, "waits": [], "resources": set(), "next": {}, "rework": 0},
            )
            s["count"] += 1
            s["resources"].add(e.resource)
            if prev is not None:
                gap = (e.timestamp - prev.timestamp).total_seconds() / 3600.0
                s["waits"].append(gap)
            prev = e
        seen = set()
        for e in case.events:
            if e.activity in seen:
                per_act[e.activity]["rework"] += 1
            seen.add(e.activity)

    results = []
    for act, s in per_act.items():
        results.append(_classify(act, s, len(log.cases)))
    results.sort(key=lambda r: -r["frequency"])
    return results


def _classify(activity: str, s: dict[str, Any], n_cases: int) -> dict[str, Any]:
    name = activity.lower()
    waits = s["waits"] or [0.0]
    m = mean(waits)
    variability = pstdev(waits) / m if m > 0 else 0.0
    volume = s["count"] / n_cases
    rework_rate = s["rework"] / s["count"]

    risky = _matches(name, RISK_KEYWORDS)
    rule_like = _matches(name, RULE_KEYWORDS)
    judgment_like = _matches(name, JUDGMENT_KEYWORDS)

    reasons: list[str] = []
    if risky:
        mode = "human_only"
        reasons.append("the step involves an approval, payment or other sensitive decision")
        if volume >= 1:
            reasons.append(f"it occurs in effectively every case ({s['count']} executions), so keep the human step but remove waiting around it")
    elif rule_like and variability < 1.2:
        mode = "traditional_automation"
        reasons.append("the step is deterministic and rule-based by nature")
        reasons.append(f"its timing is consistent (variability {variability:.1f}), typical of repetitive work")
    elif judgment_like and rework_rate >= 0.10:
        mode = "ai_with_review"
        reasons.append("the step requires judgment")
        reasons.append(f"{rework_rate * 100:.0f}% of executions are repeats, so errors happen — a human check protects quality")
    elif judgment_like:
        mode = "ai_autonomous" if volume >= 0.8 and rework_rate < 0.05 else "ai_with_review"
        if mode == "ai_autonomous":
            reasons.append("the step requires judgment but errors are rare and cheap to correct")
        else:
            reasons.append("the step requires judgment; start with AI drafting and human approval")
    elif variability < 0.8 and volume >= 0.9:
        mode = "traditional_automation"
        reasons.append("high-volume, low-variance execution suggests a mechanical step")
    else:
        mode = "human_only"
        reasons.append("no strong automation signal — leave with people until more data accumulates")

    return {
        "activity": activity,
        "mode": mode,
        "frequency": s["count"],
        "distinct_resources": len(s["resources"]),
        "mean_wait_hours": round(m, 2),
        "wait_variability": round(variability, 2),
        "rework_rate_pct": round(100 * rework_rate, 1),
        "rationale": reasons,
    }
