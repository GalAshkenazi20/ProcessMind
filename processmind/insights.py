"""Insights layer: explain the analysis in plain language and suggest changes.

Two paths:
  * Claude (via the anthropic SDK) writes a consultant-grade narrative from the
    structured analysis results. Used whenever API credentials resolve.
  * A deterministic template engine produces the same structure from the raw
    findings, so the product works fully offline / without credentials.

The narrative never invents numbers — both paths only restate figures that the
analysis engine computed.
"""

from __future__ import annotations

import json
from typing import Any

MODEL = "claude-opus-4-8"

SYSTEM_PROMPT = """You are ProcessMind, a digital operational consultant. You receive the
structured output of a process-mining analysis (findings with evidence, prediction results,
simulation options, work-design classification) for one organizational process.

Write a clear narrative for a manager who has NOT seen the diagrams:
1. "How the work actually happens" — 2-3 sentences on the real process vs. the likely intended one.
2. "The main problems" — for each significant finding: what the problem is, why it happens,
   and what it costs, citing the exact numbers from the evidence. Plain language, no jargon.
3. "What to do about it" — concrete, prioritized recommendations. Each must reference the data
   that justifies it (e.g. "30% of cases return to X because ... so require ... in the initial form").
   Where a step could be automated, say which execution mode fits (traditional automation /
   AI with human review / autonomous AI / human only) and why.
4. "What to test first with simulation" — 1-2 scenarios worth simulating before changing anything.

Rules: never invent numbers not present in the input; keep it under 600 words; use short
paragraphs and bullets; be direct and specific."""


def generate_insights(payload: dict[str, Any]) -> dict[str, Any]:
    """Return {'mode': 'ai'|'fallback', 'narrative': str}."""
    try:
        return {"mode": "ai", "narrative": _claude_narrative(payload)}
    except Exception as exc:  # no credentials, network down, refusal — degrade gracefully
        return {
            "mode": "fallback",
            "narrative": fallback_narrative(payload),
            "ai_error": f"{type(exc).__name__}: {exc}",
        }


def _claude_narrative(payload: dict[str, Any]) -> str:
    import anthropic

    client = anthropic.Anthropic()
    with client.messages.stream(
        model=MODEL,
        max_tokens=16000,
        thinking={"type": "adaptive"},
        system=SYSTEM_PROMPT,
        messages=[
            {
                "role": "user",
                "content": "Analysis results:\n" + json.dumps(payload, indent=2, default=str),
            }
        ],
    ) as stream:
        response = stream.get_final_message()
    if response.stop_reason == "refusal":
        raise RuntimeError("model refused the request")
    text = "".join(b.text for b in response.content if b.type == "text")
    if not text.strip():
        raise RuntimeError("empty model response")
    return text


# --------------------------------------------------------------------------
# Deterministic fallback
# --------------------------------------------------------------------------

_MODE_LABELS = {
    "traditional_automation": "traditional automation",
    "ai_with_review": "AI with human review",
    "ai_autonomous": "autonomous AI (low risk)",
    "human_only": "human only",
}


def fallback_narrative(payload: dict[str, Any]) -> str:
    parts: list[str] = []
    summary = payload.get("summary", {})
    var = payload.get("variants", {})

    parts.append("## How the work actually happens\n")
    if summary:
        d = summary.get("duration_hours", {})
        parts.append(
            f"The log covers {summary.get('cases', '?')} cases and {summary.get('events', '?')} events "
            f"across {summary.get('activities', '?')} distinct steps. A typical case takes "
            f"{d.get('median', '?')} hours (mean {d.get('mean', '?')}, and the slowest 10% exceed "
            f"{d.get('p90', '?')} hours), passing through {summary.get('mean_handoffs', '?')} handoffs "
            f"between people on average."
        )
    if var:
        parts.append(
            f"\nThe process runs in {var.get('total_variants', '?')} different orderings; only "
            f"{var.get('happy_path_share_pct', '?')}% of cases follow the most common path, so the "
            "official procedure describes a minority of the real work."
        )

    findings = payload.get("findings", [])
    if findings:
        parts.append("\n\n## The main problems\n")
        for f in findings[:6]:
            parts.append(f"- **{f['title']}** ({f['severity']}). {_explain_finding(f)}")

    parts.append("\n\n## What to do about it\n")
    recs = _recommendations(payload)
    for r in recs[:6]:
        parts.append(f"- {r}")

    sims = _simulation_suggestions(payload)
    if sims:
        parts.append("\n\n## What to test first with simulation\n")
        for s in sims:
            parts.append(f"- {s}")

    return "\n".join(parts)


def _explain_finding(f: dict[str, Any]) -> str:
    ev = f.get("evidence", {})
    t = f["type"]
    if t == "bottleneck":
        return (
            f"Cases wait {ev['mean_wait_hours']} hours on average between "
            f"'{ev['transition'][0]}' and '{ev['transition'][1]}' — about "
            f"{ev['share_of_mean_case_duration_pct']}% of the whole case duration, affecting "
            f"{ev['case_coverage_pct']}% of cases."
        )
    if t == "rework":
        return (
            f"{ev['affected_pct']}% of cases return to '{ev['activity']}' after already passing it, "
            f"adding roughly {ev['mean_extra_hours_per_loop']} hours per loop. Rework this common "
            "usually means information is missing or wrong the first time."
        )
    if t == "ping_pong":
        pairs = ", ".join(" & ".join(p["pair"]) for p in ev.get("top_pairs", [])[:2])
        return (
            f"{ev['affected_pct']}% of cases bounce back and forth between the same people "
            f"(mostly {pairs}), a sign of unclear ownership or incomplete handovers."
        )
    if t == "single_person_dependency":
        return (
            f"{ev['resource']} performs {ev['share_pct']}% of '{ev['activity']}' "
            f"({ev['executions']} executions). Any absence stalls every case at this stage."
        )
    if t == "duration_outliers":
        extra = ev.get("distinguishing_activity")
        hint = (
            f" Slow cases pass through '{extra['activity']}' {extra['rate_gap_pct']}% more often than fast ones."
            if extra
            else ""
        )
        return (
            f"The slowest cases take {ev['ratio']}x the median ({ev['p90_hours']}h vs {ev['median_hours']}h)."
            + hint
        )
    if t == "attribute_delay_driver":
        return (
            f"{ev['share_pct']}% of cases have {ev['attribute']}={ev['value']} and average "
            f"{ev['mean_duration_hours']} hours versus {ev['overall_mean_hours']} overall."
        )
    return json.dumps(ev)


def _recommendations(payload: dict[str, Any]) -> list[str]:
    recs: list[str] = []
    for f in payload.get("findings", []):
        ev = f.get("evidence", {})
        if f["type"] == "rework":
            recs.append(
                f"Prevent the loop back to '{ev['activity']}': make the inputs it needs mandatory at "
                f"case opening — {ev['affected_pct']}% of cases currently repeat it."
            )
        elif f["type"] == "bottleneck":
            recs.append(
                f"Attack the queue before '{ev['transition'][1]}': it alone accounts for "
                f"{ev['share_of_mean_case_duration_pct']}% of case duration. Consider capacity or automation there."
            )
        elif f["type"] == "single_person_dependency":
            recs.append(
                f"Authorize at least one more person for '{ev['activity']}' — today {ev['resource']} "
                f"handles {ev['share_pct']}% of it and is a single point of failure."
            )
        elif f["type"] == "attribute_delay_driver":
            recs.append(
                f"Investigate cases with {ev['attribute']}={ev['value']}: they run "
                f"{ev['mean_duration_hours']}h against {ev['overall_mean_hours']}h overall."
            )
    for w in payload.get("work_design", [])[:10]:
        if w["mode"] == "traditional_automation" and w["frequency"] >= 10:
            recs.append(
                f"'{w['activity']}' is a candidate for {_MODE_LABELS[w['mode']]}: " + "; ".join(w["rationale"]) + "."
            )
            break
    for w in payload.get("work_design", [])[:10]:
        if w["mode"] == "ai_with_review" and w["frequency"] >= 10:
            recs.append(
                f"'{w['activity']}' fits {_MODE_LABELS[w['mode']]}: " + "; ".join(w["rationale"]) + "."
            )
            break
    risk = payload.get("prediction", {}).get("risk_factors", [])
    if risk:
        recs.append(
            "Flag new cases early when they match known delay drivers, especially: "
            + "; ".join(r["factor"] for r in risk[:3]) + "."
        )
    return recs


def _simulation_suggestions(payload: dict[str, Any]) -> list[str]:
    out = []
    for f in payload.get("findings", []):
        ev = f.get("evidence", {})
        if f["type"] == "bottleneck":
            out.append(
                f"Simulate automating or adding capacity at '{ev['transition'][1]}' before hiring — "
                "the simulator will estimate the change in cycle time from historical data."
            )
            break
    for f in payload.get("findings", []):
        if f["type"] == "rework":
            ev = f["evidence"]
            out.append(
                f"Simulate the process without the loop back to '{ev['activity']}' to see the upper bound "
                "of fixing intake quality."
            )
            break
    return out
