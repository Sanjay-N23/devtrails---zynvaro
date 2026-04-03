"""
Tests for orchestrator deduplication logic.

Coverage (5 tests):
  1. Same trigger+city twice in quick succession -> only 1 trigger event created (24h claim dedup)
  2. Different trigger types in same city -> both create events
  3. Worker claim_history_count increments after claim creation
  4. Trigger for city with no active policies -> no claims created
  5. /triggers/live endpoint with dedup (same type+city within 3h -> deduplicated)
"""

import sys
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, AsyncMock
from sqlalchemy.orm import sessionmaker

sys.path.insert(0, "D:/AeroFyta_DEVTrails-2026_FULL_HANDOFF/zynvaro-app/backend")

import database as _database_module
import routers.triggers as _triggers_module
from models import (
    TriggerEvent,
    Claim,
    Worker,
    Policy,
    PolicyStatus,
    PolicyTier,
    TriggerType,
)


# ---------------------------------------------------------------------------
# Helper — same pattern as test_triggers_api.py
# ---------------------------------------------------------------------------

def _patch_session_local(test_db):
    """
    Return a context manager that replaces database.SessionLocal (and the
    cached reference in routers.triggers) with a factory bound to the same
    connection as test_db, so _auto_generate_claims() writes into the test
    transaction.
    """
    bound_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_db.bind,
    )

    class _CM:
        def __enter__(self):
            _database_module.SessionLocal = bound_factory
            _triggers_module.SessionLocal = bound_factory
            return self

        def __exit__(self, *_):
            _database_module.SessionLocal = sessionmaker(
                autocommit=False, autoflush=False, bind=_database_module.engine
            )
            _triggers_module.SessionLocal = _database_module.SessionLocal

    return _CM()


# ===========================================================================
# Orchestrator dedup tests
# ===========================================================================

class TestOrchestratorDedup:

    def test_same_trigger_city_twice_only_one_claim(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """
        Simulate same trigger+city twice in quick succession.
        Only 1 claim should be created for the worker (24h claim dedup).
        """
        worker = make_worker(city="Mumbai")
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Mumbai"},
            )
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Mumbai"},
            )

        test_db.expire_all()
        claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
        assert len(claims) == 1, (
            f"Expected 1 claim after duplicate trigger, got {len(claims)}"
        )

    def test_different_trigger_types_same_city_both_create_events(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """
        Different trigger types in the same city should each create their
        own trigger event and claim.
        """
        worker = make_worker(city="Delhi")
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            resp1 = authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Hazardous AQI", "city": "Delhi"},
            )
            resp2 = authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Severe Heatwave", "city": "Delhi"},
            )

        assert resp1.status_code == 201
        assert resp2.status_code == 201

        test_db.expire_all()
        claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
        assert len(claims) == 2, (
            f"Expected 2 claims for different trigger types, got {len(claims)}"
        )

        # Verify different trigger event IDs
        trigger_ids = {c.trigger_event_id for c in claims}
        assert len(trigger_ids) == 2

    def test_claim_history_count_increments(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """
        After a claim is created, worker.claim_history_count should increment by 1.
        """
        worker = make_worker(city="Bangalore", claim_history_count=2)
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )

        test_db.expire_all()
        test_db.refresh(worker)
        assert worker.claim_history_count == 3

    def test_trigger_city_no_active_policies_no_claims(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """
        A trigger for a city with no active policies should create zero claims.
        """
        # Worker in Pune but no active policy
        worker_no_policy = make_worker(city="Pune")
        # Worker in Pune with expired policy
        worker_expired = make_worker(city="Pune")
        make_policy(worker=worker_expired, status=PolicyStatus.EXPIRED)

        with _patch_session_local(test_db):
            resp = authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Pune"},
            )

        assert resp.status_code == 201

        test_db.expire_all()
        claims_no_policy = test_db.query(Claim).filter(
            Claim.worker_id == worker_no_policy.id
        ).all()
        claims_expired = test_db.query(Claim).filter(
            Claim.worker_id == worker_expired.id
        ).all()
        assert claims_no_policy == []
        assert claims_expired == []

    def test_live_endpoint_dedup_same_type_city_within_3h(
        self, authed_client, test_db, make_trigger
    ):
        """
        /triggers/live should deduplicate: if same trigger type+city already
        exists within 3h, the fired event should NOT be saved again.
        """
        # Pre-seed a recent trigger event (detected 1 hour ago, expires in 5 hours)
        existing = make_trigger(
            trigger_type=TriggerType.HEAVY_RAINFALL,
            city="Bangalore",
            detected_at=datetime.utcnow() - timedelta(hours=1),
            expires_at=datetime.utcnow() + timedelta(hours=5),
        )

        # Count trigger events before live check
        count_before = test_db.query(TriggerEvent).filter(
            TriggerEvent.trigger_type == TriggerType.HEAVY_RAINFALL,
            TriggerEvent.city == "Bangalore",
        ).count()

        # Mock check_all_triggers to return a fired trigger of the same type+city
        mock_fired = [
            {
                "trigger_type": "Heavy Rainfall",
                "city": "Bangalore",
                "measured_value": 72.5,
                "threshold_value": 64.5,
                "unit": "mm/24hr",
                "source_primary": "OpenWeatherMap",
                "source_secondary": "IMD API (mock)",
                "is_validated": True,
                "severity": "high",
                "description": "Heavy Rainfall threshold exceeded",
                "detected_at": datetime.utcnow().isoformat(),
                "expires_at": (datetime.utcnow() + timedelta(hours=6)).isoformat(),
            }
        ]

        with patch("routers.triggers.check_all_triggers", new=AsyncMock(return_value=mock_fired)):
            resp = authed_client.get("/triggers/live", params={"city": "Bangalore"})

        assert resp.status_code == 200

        # Count after: should be the same (deduplicated, no new event saved)
        test_db.expire_all()
        count_after = test_db.query(TriggerEvent).filter(
            TriggerEvent.trigger_type == TriggerType.HEAVY_RAINFALL,
            TriggerEvent.city == "Bangalore",
        ).count()
        assert count_after == count_before, (
            f"Expected dedup to prevent new event. Before: {count_before}, after: {count_after}"
        )

        # saved_events in response should be empty (deduplicated)
        body = resp.json()
        assert body["events"] == []
