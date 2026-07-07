"""HTTP API: exposes ingestion, discovery, analysis, prediction, simulation,
work design, insights and impact measurement, and serves the dashboard."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, UploadFile
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import analysis, automation, discovery, insights
from .impact import KnowledgeBase, compare_logs, split_log
from .ingest import IngestError, load_csv, load_json
from .prediction import DelayModel
from .sample_data import generate_incident_log
from .schema import EventLog
from .simulation import Simulator

ROOT = Path(__file__).resolve().parent.parent
app = FastAPI(title="ProcessMind", version="0.1.0")

# in-memory dataset store; caches for fitted models
DATASETS: dict[str, EventLog] = {}
_MODEL_CACHE: dict[str, DelayModel] = {}
_SIM_CACHE: dict[str, Simulator] = {}
KB = KnowledgeBase(ROOT / "data" / "knowledge.json")


def get_log(dataset: str) -> EventLog:
    if dataset not in DATASETS:
        raise HTTPException(404, f"Dataset {dataset!r} not found. Load one via /api/demo or /api/upload.")
    return DATASETS[dataset]


def _register(log: EventLog) -> dict[str, Any]:
    DATASETS[log.name] = log
    _MODEL_CACHE.pop(log.name, None)
    _SIM_CACHE.pop(log.name, None)
    return {"dataset": log.name, "summary": log.summary()}


@app.post("/api/demo")
def load_demo() -> dict[str, Any]:
    return _register(generate_incident_log())


@app.post("/api/upload")
async def upload(file: UploadFile) -> dict[str, Any]:
    content = await file.read()
    name = (file.filename or "upload").rsplit(".", 1)[0]
    try:
        if (file.filename or "").lower().endswith(".json"):
            log = load_json(content, name=name)
        else:
            log = load_csv(content, name=name)
    except IngestError as e:
        raise HTTPException(400, str(e))
    return _register(log)


@app.get("/api/datasets")
def datasets() -> list[dict[str, Any]]:
    return [log.summary() for log in DATASETS.values()]


@app.get("/api/overview")
def overview(dataset: str) -> dict[str, Any]:
    log = get_log(dataset)
    return {"summary": log.summary(), "variants": discovery.variants(log, limit=8)}


@app.get("/api/process-map")
def process_map(dataset: str) -> dict[str, Any]:
    return discovery.directly_follows(get_log(dataset))


@app.get("/api/findings")
def findings(dataset: str) -> list[dict[str, Any]]:
    return analysis.analyze(get_log(dataset))


@app.get("/api/predictions")
def predictions(dataset: str) -> dict[str, Any]:
    log = get_log(dataset)
    if dataset not in _MODEL_CACHE:
        _MODEL_CACHE[dataset] = DelayModel(log)
    model = _MODEL_CACHE[dataset]
    return {
        "model": model.evaluate(),
        "risk_factors": model.risk_factors(),
        "highest_risk_cases": model.risky_open_cases(),
    }


@app.get("/api/work-design")
def work_design(dataset: str) -> list[dict[str, Any]]:
    return automation.design_work(get_log(dataset))


class ScenarioRequest(BaseModel):
    dataset: str
    type: str
    activity: str | None = None
    resource: str | None = None
    to_resource: str | None = None
    reduction_pct: float = 80
    added_workers: int = 1
    current_workers: int = 2


@app.post("/api/simulate")
def simulate(req: ScenarioRequest) -> dict[str, Any]:
    log = get_log(req.dataset)
    if req.dataset not in _SIM_CACHE:
        _SIM_CACHE[req.dataset] = Simulator(log)
    scenario = {k: v for k, v in req.model_dump().items() if k != "dataset" and v is not None}
    try:
        result = _SIM_CACHE[req.dataset].compare(scenario)
    except ValueError as e:
        raise HTTPException(400, str(e))
    KB.record({"kind": "simulation", "dataset": req.dataset, "change_type": req.type, "scenario": scenario,
               "estimated_change_pct": result["results"]["mean_duration_hours"]["change_pct"]})
    return result


@app.get("/api/insights")
def get_insights(dataset: str) -> dict[str, Any]:
    log = get_log(dataset)
    if dataset not in _MODEL_CACHE:
        _MODEL_CACHE[dataset] = DelayModel(log)
    payload = {
        "summary": log.summary(),
        "variants": discovery.variants(log, limit=5),
        "findings": analysis.analyze(log),
        "prediction": {
            "model": _MODEL_CACHE[dataset].evaluate(),
            "risk_factors": _MODEL_CACHE[dataset].risk_factors(),
        },
        "work_design": automation.design_work(log),
    }
    return insights.generate_insights(payload)


class ImpactRequest(BaseModel):
    dataset: str
    split_date: str  # ISO date: cases starting before → "before", after → "after"
    change_type: str | None = None
    description: str | None = None


@app.post("/api/impact")
def impact(req: ImpactRequest) -> dict[str, Any]:
    log = get_log(req.dataset)
    try:
        split_at = datetime.fromisoformat(req.split_date)
        before, after = split_log(log, split_at)
        result = compare_logs(before, after)
    except ValueError as e:
        raise HTTPException(400, str(e))
    KB.record({"kind": "impact", "dataset": req.dataset, "change_type": req.change_type or "unknown",
               "description": req.description, "verdict": result["verdict"],
               "metrics": {k: v["change_pct"] for k, v in result["metrics"].items()}})
    return result


@app.get("/api/knowledge")
def knowledge() -> dict[str, Any]:
    return {"effectiveness": KB.effectiveness(), "entries": KB.entries[-50:]}


# -- dashboard --------------------------------------------------------------
STATIC = ROOT / "static"
if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.get("/", include_in_schema=False)
    def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")
