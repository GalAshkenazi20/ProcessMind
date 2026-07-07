"""Synthetic incident-handling event log for demos and tests.

The official procedure is: Open Request → Classify Request → Assign Technician
→ Resolve Incident → Close Request. The generated reality deliberately differs:

  * ~30% of cases return to the customer because an attachment is missing
    (rework loop back through 'Request Info from Customer').
  * High-priority cases need 'Approve Escalation', done almost only by Dana —
    a single-person dependency and a queue.
  * Two support agents bounce classification work between each other.
  * Cases opened by email are slower than portal cases.
  * The final quarter of the timeline simulates a partial fix (fewer
    missing-attachment loops), so before/after impact comparison shows change.
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

from .schema import Event, EventLog

AGENTS = ["Avi", "Noa", "Maya", "Tom"]
TECHS = ["Lior", "Dana", "Omer", "Shira"]


def generate_incident_log(n_cases: int = 400, seed: int = 7, start: datetime | None = None) -> EventLog:
    rng = random.Random(seed)
    start = start or datetime(2026, 1, 5, 8, 0)
    events: list[Event] = []

    for i in range(n_cases):
        case_id = f"INC-{1000 + i}"
        t = start + timedelta(hours=rng.uniform(0, 24 * 120))
        late_period = (t - start).days > 90  # after the "fix"

        channel = rng.choices(["portal", "email", "phone"], weights=[5, 3, 2])[0]
        priority = rng.choices(["low", "medium", "high"], weights=[5, 3, 2])[0]
        missing = rng.random() < (0.12 if late_period else 0.32)
        attrs = {"channel": channel, "priority": priority, "missing_attachment": "yes" if missing else "no"}

        def emit(activity: str, resource: str, wait_hours: float) -> None:
            nonlocal t
            t += timedelta(hours=max(0.05, rng.gauss(wait_hours, wait_hours * 0.35)))
            events.append(Event(case_id, activity, t, resource, dict(attrs)))

        opener = rng.choice(AGENTS)
        emit("Open Request", opener, 0.1)

        # classification sometimes ping-pongs between Avi and Noa
        classifier = rng.choice(AGENTS[:2])
        emit("Classify Request", classifier, 1.5 if channel == "portal" else 6.0)
        if rng.random() < 0.25:
            other = "Noa" if classifier == "Avi" else "Avi"
            emit("Classify Request", other, 2.0)
            if rng.random() < 0.5:
                emit("Classify Request", classifier, 2.0)

        # missing information → back to the customer, sometimes twice
        loops = (1 + (rng.random() < 0.3)) if missing else 0
        for _ in range(loops):
            emit("Request Info from Customer", classifier, 3.0)
            emit("Receive Customer Info", classifier, 30.0)  # customers are slow
            emit("Classify Request", classifier, 1.0)

        if priority == "high":
            # Dana is the only approver 92% of the time → queue + key-person risk
            approver = "Dana" if rng.random() < 0.92 else "Lior"
            emit("Approve Escalation", approver, 20.0 if approver == "Dana" else 4.0)

        tech = rng.choice(TECHS)
        emit("Assign Technician", rng.choice(AGENTS), 2.5)
        emit("Resolve Incident", tech, 10.0 if priority != "low" else 5.0)

        # failed resolutions loop back to the technician
        if rng.random() < 0.12:
            emit("Reopen Incident", rng.choice(AGENTS), 12.0)
            emit("Resolve Incident", tech, 8.0)

        emit("Close Request", rng.choice(AGENTS), 1.0)

    return EventLog(events, name="incident-handling-demo")


def to_csv(log: EventLog) -> str:
    lines = ["case_id,activity,timestamp,resource,channel,priority,missing_attachment"]
    for e in sorted(log.events, key=lambda e: (e.case_id, e.timestamp)):
        a = e.attributes
        lines.append(
            f"{e.case_id},{e.activity},{e.timestamp.isoformat()},{e.resource},"
            f"{a.get('channel', '')},{a.get('priority', '')},{a.get('missing_attachment', '')}"
        )
    return "\n".join(lines)
