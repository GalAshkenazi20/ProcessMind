"""Ingestion adapters: turn CSV / JSON exports from source systems into an EventLog.

Column names vary between ticketing systems, task managers and databases, so a
ColumnMapping describes which source field maps to each unified-event field.
Common aliases are auto-detected when no explicit mapping is given.
"""

from __future__ import annotations

import csv
import io
import json
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from .schema import Event, EventLog

CASE_ALIASES = ["case_id", "case", "ticket_id", "ticket", "id", "incident_id", "order_id", "claim_id"]
ACTIVITY_ALIASES = ["activity", "action", "event", "status", "step", "task", "state"]
TIMESTAMP_ALIASES = ["timestamp", "time", "datetime", "date", "created_at", "occurred_at", "event_time"]
RESOURCE_ALIASES = ["resource", "user", "agent", "assignee", "operator", "performed_by", "employee"]

TIME_FORMATS = [
    "%Y-%m-%d %H:%M:%S",
    "%Y-%m-%dT%H:%M:%S",
    "%Y-%m-%d %H:%M",
    "%Y-%m-%d",
    "%d/%m/%Y %H:%M:%S",
    "%d/%m/%Y %H:%M",
    "%m/%d/%Y %H:%M:%S",
    "%m/%d/%Y %H:%M",
]


@dataclass
class ColumnMapping:
    case_id: str
    activity: str
    timestamp: str
    resource: str | None = None
    attribute_columns: list[str] = field(default_factory=list)


class IngestError(ValueError):
    pass


def parse_timestamp(raw: Any) -> datetime:
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, (int, float)):
        return datetime.fromtimestamp(raw)
    text = str(raw).strip()
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).replace(tzinfo=None)
    except ValueError:
        pass
    for fmt in TIME_FORMATS:
        try:
            return datetime.strptime(text, fmt)
        except ValueError:
            continue
    raise IngestError(f"Unrecognized timestamp format: {text!r}")


def detect_mapping(columns: list[str]) -> ColumnMapping:
    lowered = {c.lower().strip(): c for c in columns}

    def find(aliases: list[str]) -> str | None:
        for a in aliases:
            if a in lowered:
                return lowered[a]
        return None

    case_col = find(CASE_ALIASES)
    act_col = find(ACTIVITY_ALIASES)
    ts_col = find(TIMESTAMP_ALIASES)
    if not (case_col and act_col and ts_col):
        raise IngestError(
            "Could not auto-detect case/activity/timestamp columns from "
            f"{columns}. Provide an explicit column mapping."
        )
    res_col = find(RESOURCE_ALIASES)
    core = {case_col, act_col, ts_col, res_col}
    extras = [c for c in columns if c not in core]
    return ColumnMapping(case_col, act_col, ts_col, res_col, extras)


def rows_to_log(rows: list[dict[str, Any]], name: str, mapping: ColumnMapping | None = None) -> EventLog:
    if not rows:
        raise IngestError("No rows found in the input.")
    mapping = mapping or detect_mapping(list(rows[0].keys()))
    events: list[Event] = []
    errors = 0
    for row in rows:
        try:
            events.append(
                Event(
                    case_id=str(row[mapping.case_id]).strip(),
                    activity=str(row[mapping.activity]).strip(),
                    timestamp=parse_timestamp(row[mapping.timestamp]),
                    resource=str(row.get(mapping.resource, "unknown") or "unknown").strip()
                    if mapping.resource
                    else "unknown",
                    attributes={
                        k: row.get(k) for k in mapping.attribute_columns if row.get(k) not in (None, "")
                    },
                )
            )
        except (KeyError, IngestError):
            errors += 1
    if not events:
        raise IngestError("No valid events could be parsed from the input.")
    if errors > len(rows) * 0.5:
        raise IngestError(f"More than half of the rows ({errors}/{len(rows)}) failed to parse.")
    return EventLog(events, name=name)


def load_csv(content: str | bytes, name: str = "csv", mapping: ColumnMapping | None = None) -> EventLog:
    text = content.decode("utf-8-sig") if isinstance(content, bytes) else content
    reader = csv.DictReader(io.StringIO(text))
    return rows_to_log(list(reader), name, mapping)


def load_json(content: str | bytes, name: str = "json", mapping: ColumnMapping | None = None) -> EventLog:
    data = json.loads(content)
    if isinstance(data, dict):
        # accept {"events": [...]} envelopes
        for key in ("events", "rows", "data"):
            if key in data and isinstance(data[key], list):
                data = data[key]
                break
        else:
            raise IngestError("JSON must be a list of events or contain an 'events' list.")
    return rows_to_log(data, name, mapping)
