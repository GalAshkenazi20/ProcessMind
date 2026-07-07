"""End-to-end tests over the synthetic incident log and the HTTP API."""

import pytest
from fastapi.testclient import TestClient

from processmind import analysis, automation, discovery
from processmind.api import app
from processmind.impact import compare_logs, split_log
from processmind.ingest import load_csv
from processmind.insights import fallback_narrative
from processmind.prediction import DelayModel
from processmind.sample_data import generate_incident_log, to_csv
from processmind.simulation import Simulator


@pytest.fixture(scope="module")
def log():
    return generate_incident_log(n_cases=200)


def test_ingest_roundtrip(log):
    csv_text = to_csv(log)
    loaded = load_csv(csv_text, name="roundtrip")
    assert len(loaded.cases) == len(log.cases)
    assert set(loaded.activities) == set(log.activities)
    # attributes survive the trip
    assert loaded.cases[0].attributes.get("channel") in {"portal", "email", "phone"}


def test_discovery(log):
    dfg = discovery.directly_follows(log)
    assert dfg["edges"] and dfg["nodes"]
    names = {n["activity"] for n in dfg["nodes"]}
    assert "Open Request" in names and "Close Request" in names
    var = discovery.variants(log)
    assert var["total_variants"] > 5
    assert 0 < var["happy_path_share_pct"] < 100


def test_analysis_finds_baked_in_problems(log):
    findings = analysis.analyze(log)
    types = {f["type"] for f in findings}
    assert "rework" in types, "should detect the missing-info loop"
    assert "single_person_dependency" in types, "should detect Dana as approver"
    dep = next(f for f in findings if f["type"] == "single_person_dependency")
    assert dep["evidence"]["resource"] == "Dana"
    driver = [f for f in findings if f["type"] == "attribute_delay_driver"]
    assert any("missing_attachment" in f["evidence"]["attribute"] for f in driver)


def test_prediction(log):
    model = DelayModel(log)
    ev = model.evaluate()
    assert ev["accuracy_pct"] > 70
    factors = model.risk_factors()
    assert factors, "should surface at least one delay driver"


def test_simulation_directionality(log):
    sim = Simulator(log)
    result = sim.compare({"type": "automate_activity", "activity": "Approve Escalation", "reduction_pct": 90})
    assert result["results"]["mean_duration_hours"]["change_pct"] < 0
    removed = sim.compare({"type": "remove_activity", "activity": "Request Info from Customer"})
    assert removed["results"]["mean_duration_hours"]["change_pct"] < 0
    with pytest.raises(ValueError):
        sim.compare({"type": "remove_activity", "activity": "No Such Step"})


def test_work_design_modes(log):
    design = automation.design_work(log)
    modes = {d["activity"]: d["mode"] for d in design}
    assert modes["Approve Escalation"] == "human_only"
    assert modes["Open Request"] == "traditional_automation"
    # 'Assign' must not be misread as containing the risk keyword 'sign'
    assert modes["Assign Technician"] == "traditional_automation"
    assert modes["Classify Request"].startswith("ai")


def test_impact_split_shows_change(log):
    from datetime import datetime

    before, after = split_log(log, datetime(2026, 4, 10))
    result = compare_logs(before, after)
    assert result["metrics"]["rework_case_pct"]["improved"], "the simulated fix should reduce rework"


def test_fallback_narrative(log):
    payload = {
        "summary": log.summary(),
        "variants": discovery.variants(log, limit=5),
        "findings": analysis.analyze(log),
        "work_design": automation.design_work(log),
        "prediction": {"risk_factors": DelayModel(log).risk_factors()},
    }
    text = fallback_narrative(payload)
    assert "The main problems" in text
    assert "What to do about it" in text


def test_api_flow():
    client = TestClient(app)
    r = client.post("/api/demo")
    assert r.status_code == 200
    ds = r.json()["dataset"]

    assert client.get(f"/api/overview?dataset={ds}").status_code == 200
    assert client.get(f"/api/process-map?dataset={ds}").status_code == 200
    assert client.get(f"/api/findings?dataset={ds}").status_code == 200
    assert client.get(f"/api/work-design?dataset={ds}").status_code == 200

    sim = client.post("/api/simulate", json={"dataset": ds, "type": "automate_activity",
                                             "activity": "Approve Escalation", "reduction_pct": 90})
    assert sim.status_code == 200

    bad = client.post("/api/simulate", json={"dataset": ds, "type": "remove_activity", "activity": "Nope"})
    assert bad.status_code == 400

    imp = client.post("/api/impact", json={"dataset": ds, "split_date": "2026-04-10", "change_type": "intake_form_fix"})
    assert imp.status_code == 200
    assert "verdict" in imp.json()

    assert client.get("/api/knowledge").status_code == 200
    missing = client.get("/api/overview?dataset=nope")
    assert missing.status_code == 404
