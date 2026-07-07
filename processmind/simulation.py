"""What-if simulation: estimate the effect of a change before implementing it.

The simulator replays the process as a Monte Carlo model fitted to history:
transition probabilities and waiting-time samples come from the actual event
log, so a scenario answers "what would past traffic have looked like under
this change" rather than relying on assumptions.

Supported scenarios:
  remove_activity   — skip a step entirely (its wait folds away)
  automate_activity — a step's waiting time shrinks by `reduction_pct`
  add_capacity      — waits before activities handled by `resource` (or all
                      activities if none given) shrink proportionally to the
                      added capacity share
  reassign_activity — move a step from one resource pool to another (waits
                      resample from the target resource's observed pace)
"""

from __future__ import annotations

import random
from statistics import mean, median
from typing import Any

from .schema import EventLog, percentile

N_RUNS = 2000


class Simulator:
    def __init__(self, log: EventLog, seed: int = 42):
        self.log = log
        self.rng = random.Random(seed)
        # transition samples: (src -> list[(dst, wait_hours)])
        self.transitions: dict[str, list[tuple[str, float]]] = {}
        # waits observed before an activity, per performing resource
        self.resource_pace: dict[tuple[str, str], list[float]] = {}
        for case in log.cases:
            evs = case.events
            self.transitions.setdefault("__start__", []).append((evs[0].activity, 0.0))
            for a, b in zip(evs, evs[1:]):
                gap = (b.timestamp - a.timestamp).total_seconds() / 3600.0
                self.transitions.setdefault(a.activity, []).append((b.activity, gap))
                self.resource_pace.setdefault((b.activity, b.resource), []).append(gap)
            self.transitions.setdefault(evs[-1].activity, []).append(("__end__", 0.0))

    # -- single replication -------------------------------------------------
    def _run_case(self, scenario: dict[str, Any] | None, max_steps: int = 60) -> tuple[float, int]:
        total = 0.0
        steps = 0
        current = "__start__"
        removed = scenario.get("activity") if scenario and scenario["type"] == "remove_activity" else None
        while steps < max_steps:
            options = self.transitions.get(current)
            if not options:
                break
            dst, wait = self.rng.choice(options)
            if dst == "__end__":
                break
            wait = self._adjust_wait(dst, wait, scenario)
            if dst == removed:
                # skip the removed step: hop onward from it without paying its wait
                current = dst
                steps += 1  # still consumes a step so self-loops can't run forever
                continue
            total += wait
            steps += 1
            current = dst
        return total, steps

    def _adjust_wait(self, activity: str, wait: float, scenario: dict[str, Any] | None) -> float:
        if not scenario:
            return wait
        stype = scenario["type"]
        if stype == "automate_activity" and activity == scenario["activity"]:
            return wait * (1 - scenario.get("reduction_pct", 80) / 100)
        if stype == "add_capacity":
            resource = scenario.get("resource")
            factor = 1 / (1 + scenario.get("added_workers", 1) / max(1, scenario.get("current_workers", 2)))
            if resource is None or self._handled_by(activity, resource):
                return wait * factor
        if stype == "reassign_activity" and activity == scenario["activity"]:
            samples = self.resource_pace.get((activity, scenario["to_resource"]))
            if samples:
                return self.rng.choice(samples)
            return wait  # no history for the target pool — assume same pace
        return wait

    def _handled_by(self, activity: str, resource: str) -> bool:
        return (activity, resource) in self.resource_pace

    # -- scenario evaluation -------------------------------------------------
    def evaluate(self, scenario: dict[str, Any] | None, runs: int = N_RUNS) -> dict[str, Any]:
        durations, steps = [], []
        for _ in range(runs):
            d, s = self._run_case(scenario)
            durations.append(d)
            steps.append(s)
        return {
            "mean_duration_hours": round(mean(durations), 2),
            "median_duration_hours": round(median(durations), 2),
            "p90_duration_hours": round(percentile(durations, 90), 2),
            "mean_steps": round(mean(steps), 2),
        }

    def compare(self, scenario: dict[str, Any]) -> dict[str, Any]:
        validate_scenario(scenario, self.log)
        self.rng.seed(42)
        baseline = self.evaluate(None)
        self.rng.seed(42)  # same random stream → paired comparison
        changed = self.evaluate(scenario)
        deltas = {
            k: {
                "baseline": baseline[k],
                "scenario": changed[k],
                "change_pct": round(100 * (changed[k] - baseline[k]) / baseline[k], 1) if baseline[k] else 0.0,
            }
            for k in baseline
        }
        return {
            "scenario": scenario,
            "results": deltas,
            "note": (
                "Estimates are based on replaying historical transition and waiting-time "
                "patterns; they show direction and rough magnitude, not guarantees."
            ),
        }


def validate_scenario(scenario: dict[str, Any], log: EventLog) -> None:
    stype = scenario.get("type")
    valid = {"remove_activity", "automate_activity", "add_capacity", "reassign_activity"}
    if stype not in valid:
        raise ValueError(f"Unknown scenario type {stype!r}. Valid types: {sorted(valid)}")
    if stype in {"remove_activity", "automate_activity", "reassign_activity"}:
        act = scenario.get("activity")
        if act not in log.activities:
            raise ValueError(f"Activity {act!r} does not exist in this process. Activities: {log.activities}")
    if stype == "reassign_activity" and not scenario.get("to_resource"):
        raise ValueError("reassign_activity requires 'to_resource'.")
