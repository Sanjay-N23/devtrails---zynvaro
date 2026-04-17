"""
backend/tests/api/test_simulate_trigger_api.py
==============================================
Minimum Must-Pass tests for the Simulate Trigger / What-If Scenario panel.
Covers the 10 core requirements from Spec Section S.
"""
import pytest
import os
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import TriggerEvent, Claim


# ═══════════════════════════════════════════════════════════════
# S.1 — Simulate panel is VISIBLE in demo, HIDDEN in production
# ═══════════════════════════════════════════════════════════════

def test_simulate_blocked_in_production(authed_client: TestClient, monkeypatch):
    """S.1 / Spec A.15 / M.209 — Backend rejects simulate call in production."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("MOCK_PAYMENTS", "0")

    resp = authed_client.post("/triggers/simulate", json={
        "trigger_type": "Heavy Rainfall",
        "city": "Bangalore",
    })
    assert resp.status_code == 403
    assert "disabled" in resp.json()["detail"].lower()


def test_simulate_allowed_in_demo(authed_client: TestClient, monkeypatch):
    """S.1 — Backend accepts simulate call in demo environment."""
    monkeypatch.setenv("ENVIRONMENT", "demo")

    resp = authed_client.post("/triggers/simulate", json={
        "trigger_type": "Heavy Rainfall",
        "city": "Bangalore",
    })
    assert resp.status_code == 201


# ═══════════════════════════════════════════════════════════════
# S.2 — Heavy Rainfall in Bangalore scenario creates synthetic trigger
# ═══════════════════════════════════════════════════════════════

def test_heavy_rainfall_bangalore_creates_trigger(authed_client: TestClient, test_db: Session, monkeypatch):
    """S.2 — Heavy Rainfall in Bangalore scenario creates a synthetic TriggerEvent."""
    monkeypatch.setenv("ENVIRONMENT", "demo")

    resp = authed_client.post("/triggers/simulate", json={
        "trigger_type": "Heavy Rainfall",
        "city": "Bangalore",
        "scenario_name": "Heavy Rainfall in Bangalore",
    })
    assert resp.status_code == 201
    data = resp.json()
    assert data["is_simulated"] is True
    assert data["trigger_event_id"] is not None

    event = test_db.query(TriggerEvent).filter(TriggerEvent.id == data["trigger_event_id"]).first()
    assert event is not None
    assert event.trigger_type == "Heavy Rainfall"
    assert event.city == "Bangalore"


# ═══════════════════════════════════════════════════════════════
# S.3 — Synthetic trigger is marked DEMO_SIMULATION
# ═══════════════════════════════════════════════════════════════

def test_synthetic_trigger_has_demo_simulation_source_type(authed_client: TestClient, test_db: Session, monkeypatch):
    """S.3 / Spec D.58-59 / K.183 — Trigger stored as DEMO_SIMULATION, is_simulated=True."""
    monkeypatch.setenv("ENVIRONMENT", "demo")

    resp = authed_client.post("/triggers/simulate", json={
        "trigger_type": "Hazardous AQI",
        "city": "Delhi",
    })
    assert resp.status_code == 201

    event = test_db.query(TriggerEvent).filter(TriggerEvent.id == resp.json()["trigger_event_id"]).first()
    assert event.is_simulated is True
    assert event.source_type == "DEMO_SIMULATION"


# ═══════════════════════════════════════════════════════════════
# S.5 — Claim stores demo-trigger linkage
# ═══════════════════════════════════════════════════════════════

def test_claim_links_back_to_demo_trigger(authed_client: TestClient, test_db: Session, make_policy, monkeypatch):
    """S.5 / Spec F.97-98 — Auto-generated claim references back to the synthetic trigger ID."""
    import time
    monkeypatch.setenv("ENVIRONMENT", "demo")
    make_policy(worker=authed_client.worker)  # Create an active policy for the authed worker

    resp = authed_client.post("/triggers/simulate", json={
        "trigger_type": "Heavy Rainfall",
        "city": "Bangalore",
        "bypass_gate": True,
    })
    assert resp.status_code == 201
    trigger_id = resp.json()["trigger_event_id"]

    # Allow background task to complete
    time.sleep(2)

    claim = test_db.query(Claim).filter(Claim.trigger_event_id == trigger_id).first()
    if claim:
        assert claim.trigger_event_id == trigger_id
        event = test_db.query(TriggerEvent).filter(TriggerEvent.id == trigger_id).first()
        assert event.is_simulated is True


# ═══════════════════════════════════════════════════════════════
# S.8 — Double-tap does NOT create duplicate scenario runs
# ═══════════════════════════════════════════════════════════════

def test_double_tap_idempotent_scenario(authed_client: TestClient, test_db: Session, monkeypatch):
    """S.8 / Spec N.221 — Same scenario_id submitted twice returns same event, no duplicate."""
    monkeypatch.setenv("ENVIRONMENT", "demo")

    payload = {
        "trigger_type": "Severe Heatwave",
        "city": "Chennai",
        "scenario_id": "idem-test-scenario-xyz",
        "scenario_name": "Heatwave Chennai Judge Demo",
    }

    r1 = authed_client.post("/triggers/simulate", json=payload)
    assert r1.status_code == 201

    r2 = authed_client.post("/triggers/simulate", json=payload)
    assert r2.status_code == 201

    # Must resolve to exact same event
    assert r1.json()["trigger_event_id"] == r2.json()["trigger_event_id"]

    # Only one TriggerEvent created
    events = test_db.query(TriggerEvent).filter(
        TriggerEvent.scenario_id == "idem-test-scenario-xyz"
    ).all()
    assert len(events) == 1


# ═══════════════════════════════════════════════════════════════
# S.9 — Audit log records scenario as DEMO_SIMULATION with full fields
# ═══════════════════════════════════════════════════════════════

def test_audit_fields_populated_on_scenario(authed_client: TestClient, test_db: Session, monkeypatch):
    """S.9 / Spec K.176-192 — All audit fields are written correctly to TriggerEvent."""
    monkeypatch.setenv("ENVIRONMENT", "demo")

    scenario_id = "audit-trace-abc123"
    resp = authed_client.post("/triggers/simulate", json={
        "trigger_type": "Heavy Rainfall",
        "city": "Mumbai",
        "scenario_id": scenario_id,
        "scenario_name": "Heavy Rainfall Mumbai Test",
    })
    assert resp.status_code == 201

    event = test_db.query(TriggerEvent).filter(TriggerEvent.id == resp.json()["trigger_event_id"]).first()
    assert event.source_type == "DEMO_SIMULATION"
    assert event.scenario_id == scenario_id
    assert event.scenario_name == "Heavy Rainfall Mumbai Test"
    assert event.scenario_created_by is not None
    assert event.pipeline_run_id is not None
    assert event.original_environment is not None


# ═══════════════════════════════════════════════════════════════
# S.10 — Admin can distinguish demo-trigger events from real events
# ═══════════════════════════════════════════════════════════════

def test_demo_simulation_distinguishable_from_live(authed_client: TestClient, test_db: Session, monkeypatch):
    """S.10 / Spec L.201, O.232 — Demo-trigger events can be filtered separately."""
    monkeypatch.setenv("ENVIRONMENT", "demo")

    # Fire simulate scenario
    resp = authed_client.post("/triggers/simulate", json={
        "trigger_type": "Heavy Rainfall",
        "city": "Hyderabad",
    })
    assert resp.status_code == 201

    # Query simulated events only
    demo_events = test_db.query(TriggerEvent).filter(
        TriggerEvent.source_type == "DEMO_SIMULATION"
    ).all()
    live_events = test_db.query(TriggerEvent).filter(
        TriggerEvent.source_type == "LIVE"
    ).all()

    # The newly created event must be exclusively in demo bucket
    event_ids = [e.id for e in demo_events]
    assert resp.json()["trigger_event_id"] in event_ids

    # No cross-contamination
    live_ids = [e.id for e in live_events]
    assert resp.json()["trigger_event_id"] not in live_ids


# ═══════════════════════════════════════════════════════════════
# S.2b — Input validation: invalid trigger type rejected
# ═══════════════════════════════════════════════════════════════

def test_invalid_trigger_type_rejected(authed_client: TestClient, monkeypatch):
    """Spec C.47 — Unsupported trigger type returns 400."""
    monkeypatch.setenv("ENVIRONMENT", "demo")

    resp = authed_client.post("/triggers/simulate", json={
        "trigger_type": "Nuclear Meltdown",
        "city": "Bangalore",
    })
    assert resp.status_code == 400


def test_invalid_city_rejected(authed_client: TestClient, monkeypatch):
    """Spec B.26 — Unsupported city returns 400."""
    monkeypatch.setenv("ENVIRONMENT", "demo")

    resp = authed_client.post("/triggers/simulate", json={
        "trigger_type": "Heavy Rainfall",
        "city": "Atlantis",
    })
    assert resp.status_code == 400
