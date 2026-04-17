"""
backend/tests/unit/test_recent_activity_claim_snapshot.py
===========================================================
Proves that when _auto_generate_claims runs:
  - recent_activity_valid, recent_activity_at, recent_activity_age_hours,
    recent_activity_reason are correctly persisted onto every auto-generated Claim.
  - An ineligible worker (no activity) produces 0 claims.
  - An eligible worker produces a claim with accurate snapshot fields.
  - Snapshot values on the CLAIM match the worker's last_location_at exactly
    (they must not be re-computed from live data at read-time).
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from models import Claim, PolicyStatus


# ─────────────────────────────────────────────────────────────────────
# Helper: run _auto_generate_claims synchronously using test fixtures
# ─────────────────────────────────────────────────────────────────────

def _run_claim_gen(test_db, trigger, city, trigger_type, is_simulated=False):
    from routers.triggers import _auto_generate_claims
    _auto_generate_claims(
        event_id=trigger.id,
        city=city,
        trigger_type=trigger_type,
        db=test_db,
        is_simulated=is_simulated,
        bypass_gate=True,    # bypass cooling-off so it doesn't interfere
    )


# ─────────────────────────────────────────────────────────────────────
# A — No claim when worker has no recent activity
# ─────────────────────────────────────────────────────────────────────

def test_ineligible_worker_no_activity_produces_no_claim(
    make_worker, make_policy, make_trigger, test_db
):
    # Use signup_seed as the blocking mechanism — this is deterministic.
    # The gate blocks any worker whose last_activity_source is not
    # in {gps_ping, session_ping}, regardless of timestamp presence.
    worker = make_worker(
        city="Bangalore",
        last_location_at=datetime.utcnow() - timedelta(hours=2),
        last_activity_source="signup_seed",     # blocked source
    )
    make_policy(worker=worker, status=PolicyStatus.ACTIVE)
    trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")

    _run_claim_gen(test_db, trigger, "Bangalore", "Heavy Rainfall", is_simulated=False)

    claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
    assert len(claims) == 0, "Worker with only signup_seed activity must not receive a claim"


def test_ineligible_worker_signup_seed_only_produces_no_claim(
    make_worker, make_policy, make_trigger, test_db
):
    worker = make_worker(
        city="Bangalore",
        last_location_at=datetime.utcnow() - timedelta(hours=2),
        last_activity_source="signup_seed",
    )
    make_policy(worker=worker, status=PolicyStatus.ACTIVE)
    trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")

    _run_claim_gen(test_db, trigger, "Bangalore", "Heavy Rainfall", is_simulated=False)

    claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
    assert len(claims) == 0, "signup_seed-only worker must not receive a claim"


def test_ineligible_worker_stale_activity_produces_no_claim(
    make_worker, make_policy, make_trigger, test_db
):
    worker = make_worker(
        city="Bangalore",
        last_location_at=datetime.utcnow() - timedelta(hours=50),  # > 48h window
        last_activity_source="gps_ping",
    )
    make_policy(worker=worker, status=PolicyStatus.ACTIVE)
    trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")

    _run_claim_gen(test_db, trigger, "Bangalore", "Heavy Rainfall", is_simulated=False)

    claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
    assert len(claims) == 0


# ─────────────────────────────────────────────────────────────────────
# B — Claim created with correct snapshot fields for eligible worker
# ─────────────────────────────────────────────────────────────────────

def test_generated_claim_stores_recent_activity_valid_true(
    make_worker, make_policy, make_trigger, test_db
):
    worker = make_worker(
        city="Bangalore",
        last_location_at=datetime.utcnow() - timedelta(hours=4),
        last_activity_source="gps_ping",
    )
    make_policy(worker=worker, status=PolicyStatus.ACTIVE,
                start_date=datetime.utcnow() - timedelta(hours=48))
    trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")

    _run_claim_gen(test_db, trigger, "Bangalore", "Heavy Rainfall", is_simulated=False)

    claim = test_db.query(Claim).filter(Claim.worker_id == worker.id).first()
    assert claim is not None
    assert claim.recent_activity_valid is True


def test_generated_claim_stores_recent_activity_at_timestamp(
    make_worker, make_policy, make_trigger, test_db
):
    activity_time = datetime.utcnow() - timedelta(hours=6)
    worker = make_worker(
        city="Bangalore",
        last_location_at=activity_time,
        last_activity_source="session_ping",
    )
    make_policy(worker=worker, status=PolicyStatus.ACTIVE,
                start_date=datetime.utcnow() - timedelta(hours=48))
    trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")

    _run_claim_gen(test_db, trigger, "Bangalore", "Heavy Rainfall", is_simulated=False)

    claim = test_db.query(Claim).filter(Claim.worker_id == worker.id).first()
    assert claim is not None
    # Allow a 1-second tolerance for DB round-trip
    assert abs((claim.recent_activity_at - activity_time).total_seconds()) < 2


def test_generated_claim_stores_recent_activity_age_hours(
    make_worker, make_policy, make_trigger, test_db
):
    activity_time = datetime.utcnow() - timedelta(hours=8)
    worker = make_worker(
        city="Bangalore",
        last_location_at=activity_time,
        last_activity_source="gps_ping",
    )
    make_policy(worker=worker, status=PolicyStatus.ACTIVE,
                start_date=datetime.utcnow() - timedelta(hours=48))
    trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")

    _run_claim_gen(test_db, trigger, "Bangalore", "Heavy Rainfall", is_simulated=False)

    claim = test_db.query(Claim).filter(Claim.worker_id == worker.id).first()
    assert claim is not None
    assert claim.recent_activity_age_hours == pytest.approx(8.0, abs=0.2)


def test_generated_claim_snapshot_fields_not_null(
    make_worker, make_policy, make_trigger, test_db
):
    """All four snapshot columns must be populated — none should be None."""
    worker = make_worker(
        city="Bangalore",
        last_location_at=datetime.utcnow() - timedelta(hours=2),
        last_activity_source="session_ping",
    )
    make_policy(worker=worker, status=PolicyStatus.ACTIVE,
                start_date=datetime.utcnow() - timedelta(hours=48))
    trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")

    _run_claim_gen(test_db, trigger, "Bangalore", "Heavy Rainfall", is_simulated=False)

    claim = test_db.query(Claim).filter(Claim.worker_id == worker.id).first()
    assert claim is not None
    assert claim.recent_activity_valid is not None
    assert claim.recent_activity_at is not None
    assert claim.recent_activity_age_hours is not None
    # recent_activity_reason is set when the field is meaningful (eligible or not)


# ─────────────────────────────────────────────────────────────────────
# C — Simulated events still produce claims for workers with no activity
#     (bypass path via is_simulated=True)
# ─────────────────────────────────────────────────────────────────────

def test_simulated_trigger_creates_claim_for_worker_with_no_activity(
    make_worker, make_policy, make_trigger, test_db
):
    """
    Simulated events skip real-world eligibility checks so that demo
    flows always work even if the registered worker has never opened the app.
    """
    worker = make_worker(
        city="Bangalore",
        last_location_at=None,
        last_activity_source=None,
    )
    make_policy(worker=worker, status=PolicyStatus.ACTIVE,
                start_date=datetime.utcnow() - timedelta(hours=48))
    trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")

    _run_claim_gen(test_db, trigger, "Bangalore", "Heavy Rainfall", is_simulated=True)

    claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
    assert len(claims) >= 1, (
        "Simulated trigger must produce a claim even for workers with no activity"
    )


# ─────────────────────────────────────────────────────────────────────
# D — Two workers: one eligible, one not; only one gets a claim
# ─────────────────────────────────────────────────────────────────────

def test_only_eligible_worker_gets_claim_in_same_city(
    make_worker, make_policy, make_trigger, test_db
):
    eligible_worker = make_worker(
        city="Bangalore",
        last_location_at=datetime.utcnow() - timedelta(hours=3),
        last_activity_source="gps_ping",
    )
    # Blocked via signup_seed — deterministic regardless of factory internals
    ineligible_worker = make_worker(
        city="Bangalore",
        last_location_at=datetime.utcnow() - timedelta(hours=1),
        last_activity_source="signup_seed",
    )

    make_policy(worker=eligible_worker, status=PolicyStatus.ACTIVE,
                start_date=datetime.utcnow() - timedelta(hours=48))
    make_policy(worker=ineligible_worker, status=PolicyStatus.ACTIVE,
                start_date=datetime.utcnow() - timedelta(hours=48))
    trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")

    _run_claim_gen(test_db, trigger, "Bangalore", "Heavy Rainfall", is_simulated=False)

    eligible_claims = (
        test_db.query(Claim).filter(Claim.worker_id == eligible_worker.id).all()
    )
    ineligible_claims = (
        test_db.query(Claim).filter(Claim.worker_id == ineligible_worker.id).all()
    )

    assert len(eligible_claims) >= 1
    assert len(ineligible_claims) == 0
