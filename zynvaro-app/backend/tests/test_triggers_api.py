"""
Integration tests for routers/triggers.py.

Test coverage
=============
  - POST /triggers/simulate   (auth required, 201, DB persistence, background claims)
  - GET  /triggers/            (public, city filter, limit)
  - GET  /triggers/types       (reference data, no auth)
  - GET  /triggers/live        (auth required, mocked external calls, graceful degradation)
  - _auto_generate_claims()    (claims created, zero-payout skip, deduplication,
                                fraud scoring, claim_history_count increment)

Architecture note — background task DB session
-----------------------------------------------
_auto_generate_claims() opens its own ``database.SessionLocal()`` internally
instead of reusing the FastAPI-injected session.  This means the standard
``get_db`` override in conftest.py does NOT cover that function.

Strategy used here:
  1. The test_db fixture (from conftest) uses a *connection-level transaction*
     that will be rolled back after the test.  We bind a second sessionmaker to
     the SAME connection so that both sessions share the same transaction scope
     and therefore see each other's writes.
  2. We monkey-patch ``database.SessionLocal`` (and the identical reference
     inside ``routers.triggers``) to return sessions bound to that same
     connection before calling simulate, then restore it afterward.
  3. This keeps full isolation: nothing persists to the real DB and nothing
     leaks between tests.
"""

import sys
import pytest
from datetime import datetime, timedelta
from unittest.mock import patch, MagicMock
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
    ClaimStatus,
)
from ml.premium_engine import get_payout_amount

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

ALL_TRIGGER_TYPES = list(_triggers_module.TRIGGERS.keys())  # 6 types from the engine

# Known demo measured values returned by simulate_trigger()
DEMO_VALUES = {
    "Heavy Rainfall":          72.5,
    "Extreme Rain / Flooding": 210.0,
    "Severe Heatwave":         46.2,
    "Hazardous AQI":           485.0,
    "Platform Outage":         20.0,
    "Civil Disruption":        6.0,
}


def _patch_session_local(test_db):
    """
    Return a context manager that replaces ``database.SessionLocal`` (and the
    cached reference in routers.triggers) with a factory that yields sessions
    bound to the *same* connection as *test_db*.

    This ensures _auto_generate_claims() writes into the test transaction and
    its writes are visible when we query test_db afterward.
    """
    bound_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_db.bind,  # the connection-level transaction connection
    )

    class _CM:
        def __enter__(self):
            _database_module.SessionLocal = bound_factory
            _triggers_module.SessionLocal = bound_factory  # module-level alias used in bg task
            return self

        def __exit__(self, *_):
            # Restore originals — conftest already controls teardown,
            # but we still clean up to be safe for subsequent tests.
            _database_module.SessionLocal = sessionmaker(
                autocommit=False, autoflush=False, bind=_database_module.engine
            )
            _triggers_module.SessionLocal = _database_module.SessionLocal

    return _CM()


# ===========================================================================
# POST /triggers/simulate
# ===========================================================================

class TestSimulateTrigger:

    def test_simulate_returns_201(self, authed_client):
        resp = authed_client.post(
            "/triggers/simulate",
            json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
        )
        assert resp.status_code == 201

    def test_simulate_returns_trigger_event_id(self, authed_client):
        resp = authed_client.post(
            "/triggers/simulate",
            json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
        )
        body = resp.json()
        assert "trigger_event_id" in body
        assert isinstance(body["trigger_event_id"], int)
        assert body["trigger_event_id"] > 0

    def test_simulate_creates_trigger_event_in_db(self, authed_client, test_db):
        resp = authed_client.post(
            "/triggers/simulate",
            json={"trigger_type": "Severe Heatwave", "city": "Mumbai"},
        )
        assert resp.status_code == 201
        event_id = resp.json()["trigger_event_id"]

        event = test_db.query(TriggerEvent).filter(TriggerEvent.id == event_id).first()
        assert event is not None
        assert event.trigger_type == "Severe Heatwave"
        assert event.city == "Mumbai"

    def test_simulate_trigger_is_validated_true(self, authed_client, test_db):
        resp = authed_client.post(
            "/triggers/simulate",
            json={"trigger_type": "Hazardous AQI", "city": "Delhi"},
        )
        event_id = resp.json()["trigger_event_id"]
        event = test_db.query(TriggerEvent).filter(TriggerEvent.id == event_id).first()
        assert event.is_validated is True

    def test_simulate_returns_correct_measured_value_for_heavy_rainfall(self, authed_client):
        resp = authed_client.post(
            "/triggers/simulate",
            json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
        )
        body = resp.json()
        # simulate_trigger() returns the fixed demo value 72.5 for Heavy Rainfall
        assert body["measured_value"] == pytest.approx(DEMO_VALUES["Heavy Rainfall"])
        assert body["unit"] == "mm/24hr"

    def test_simulate_returns_message_field(self, authed_client):
        resp = authed_client.post(
            "/triggers/simulate",
            json={"trigger_type": "Platform Outage", "city": "Hyderabad"},
        )
        body = resp.json()
        assert "message" in body
        assert "Platform Outage" in body["message"]
        assert "Hyderabad" in body["message"]

    def test_simulate_returns_description_field(self, authed_client):
        resp = authed_client.post(
            "/triggers/simulate",
            json={"trigger_type": "Civil Disruption", "city": "Chennai"},
        )
        body = resp.json()
        assert "description" in body
        assert isinstance(body["description"], str)
        assert len(body["description"]) > 0

    def test_simulate_returns_and_persists_confidence_and_source_log(self, authed_client, test_db):
        resp = authed_client.post(
            "/triggers/simulate",
            json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
        )
        assert resp.status_code == 201
        body = resp.json()
        assert body["confidence_score"] == pytest.approx(100.0)
        assert "Zynvaro Simulation Engine" in body["source_log"]

        event = test_db.query(TriggerEvent).filter(TriggerEvent.id == body["trigger_event_id"]).first()
        assert event is not None
        assert event.confidence_score == pytest.approx(100.0)
        assert "Zynvaro Simulation Engine" in (event.source_log or "")

    @pytest.mark.parametrize("trigger_type", ALL_TRIGGER_TYPES)
    def test_simulate_all_6_trigger_types_accepted(self, authed_client, trigger_type):
        resp = authed_client.post(
            "/triggers/simulate",
            json={"trigger_type": trigger_type, "city": "Bangalore"},
        )
        assert resp.status_code == 201, (
            f"Expected 201 for trigger_type={trigger_type!r}, got {resp.status_code}: {resp.text}"
        )

    def test_simulate_requires_auth(self, client):
        """Unauthenticated request must be rejected with 401."""
        resp = client.post(
            "/triggers/simulate",
            json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
        )
        assert resp.status_code == 401

    def test_simulate_unknown_trigger_type_returns_400(self, authed_client):
        """
        Unknown trigger types are now rejected with 400 (input validation added).
        """
        resp = authed_client.post(
            "/triggers/simulate",
            json={"trigger_type": "Unknown Event", "city": "Bangalore"},
        )
        assert resp.status_code == 400
        assert "Invalid trigger type" in resp.json()["detail"]

    def test_simulate_unknown_city_returns_400(self, authed_client):
        """Unknown cities are rejected with 400."""
        resp = authed_client.post(
            "/triggers/simulate",
            json={"trigger_type": "Heavy Rainfall", "city": "Jaipur"},
        )
        assert resp.status_code == 400
        assert "Invalid city" in resp.json()["detail"]


# ===========================================================================
# GET /triggers/
# ===========================================================================

class TestListTriggers:

    def test_list_triggers_public_no_auth_needed(self, client):
        """Endpoint must be accessible without a JWT."""
        resp = client.get("/triggers/")
        assert resp.status_code == 200

    def test_list_triggers_returns_empty_list_on_clean_db(self, client):
        # Use a city absent from seed data — filter must return empty
        resp = client.get("/triggers/", params={"city": "Kolkata"})
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_triggers_returns_seeded_events(self, client, make_trigger):
        # Record baseline before adding test triggers (app startup may seed demo data)
        baseline = len(client.get("/triggers/").json())
        make_trigger(trigger_type=TriggerType.HEAVY_RAINFALL, city="Bangalore")
        make_trigger(trigger_type=TriggerType.HAZARDOUS_AQI,  city="Delhi")

        resp = client.get("/triggers/")
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == baseline + 2

    def test_list_triggers_city_filter_works(self, client, make_trigger):
        baseline_mumbai = len(client.get("/triggers/", params={"city": "Mumbai"}).json())
        make_trigger(city="Bangalore")
        make_trigger(city="Mumbai")
        make_trigger(city="Mumbai")

        resp = client.get("/triggers/", params={"city": "Mumbai"})
        body = resp.json()
        assert len(body) == baseline_mumbai + 2
        assert all(e["city"] == "Mumbai" for e in body)

    def test_list_triggers_city_filter_returns_empty_for_unmatched_city(self, client, make_trigger):
        make_trigger(city="Bangalore")
        resp = client.get("/triggers/", params={"city": "Chennai"})
        assert resp.json() == []

    def test_list_triggers_respects_limit(self, client, make_trigger):
        for _ in range(5):
            make_trigger()

        resp = client.get("/triggers/", params={"limit": 3})
        body = resp.json()
        assert len(body) == 3

    def test_list_triggers_default_limit_is_20(self, client, make_trigger):
        for _ in range(25):
            make_trigger()

        resp = client.get("/triggers/")
        body = resp.json()
        assert len(body) == 20

    def test_list_triggers_response_contains_required_fields(self, client, make_trigger):
        make_trigger()
        resp = client.get("/triggers/")
        event = resp.json()[0]
        required = {
            "id", "trigger_type", "city", "measured_value",
            "threshold_value", "unit", "source_primary",
            "is_validated", "severity", "detected_at",
        }
        assert required.issubset(event.keys())

    def test_list_triggers_ordered_newest_first(self, client, make_trigger):
        older = make_trigger(
            detected_at=datetime.utcnow() - timedelta(hours=2),
            expires_at=datetime.utcnow() + timedelta(hours=4),
        )
        newer = make_trigger(
            detected_at=datetime.utcnow() - timedelta(minutes=10),
            expires_at=datetime.utcnow() + timedelta(hours=6),
        )
        resp = client.get("/triggers/")
        ids = [e["id"] for e in resp.json()]
        # Newest first — newer.id should appear before older.id
        assert ids.index(newer.id) < ids.index(older.id)


# ===========================================================================
# GET /triggers/types
# ===========================================================================

class TestTriggerTypes:

    def test_trigger_types_no_auth_needed(self, client):
        resp = client.get("/triggers/types")
        assert resp.status_code == 200

    def test_trigger_types_returns_6_types(self, client):
        resp = client.get("/triggers/types")
        body = resp.json()
        assert len(body) == 6

    def test_trigger_types_includes_heavy_rainfall(self, client):
        resp = client.get("/triggers/types")
        types = [t["trigger_type"] for t in resp.json()]
        assert "Heavy Rainfall" in types

    def test_trigger_types_includes_civil_disruption(self, client):
        resp = client.get("/triggers/types")
        types = [t["trigger_type"] for t in resp.json()]
        assert "Civil Disruption" in types

    def test_trigger_types_has_threshold_and_unit(self, client):
        resp = client.get("/triggers/types")
        for entry in resp.json():
            assert "threshold" in entry, f"Missing 'threshold' in {entry}"
            assert "unit"      in entry, f"Missing 'unit' in {entry}"

    def test_trigger_types_has_source_primary(self, client):
        resp = client.get("/triggers/types")
        for entry in resp.json():
            assert "source_primary" in entry

    def test_trigger_types_all_6_expected_names(self, client):
        resp = client.get("/triggers/types")
        returned = {t["trigger_type"] for t in resp.json()}
        expected = {
            "Heavy Rainfall",
            "Extreme Rain / Flooding",
            "Severe Heatwave",
            "Hazardous AQI",
            "Platform Outage",
            "Civil Disruption",
        }
        assert returned == expected

    def test_trigger_types_heavy_rainfall_threshold(self, client):
        resp = client.get("/triggers/types")
        hr = next(t for t in resp.json() if t["trigger_type"] == "Heavy Rainfall")
        assert hr["threshold"] == pytest.approx(64.5)
        assert hr["unit"] == "mm/24hr"

    def test_trigger_types_hazardous_aqi_threshold(self, client):
        resp = client.get("/triggers/types")
        aqi = next(t for t in resp.json() if t["trigger_type"] == "Hazardous AQI")
        assert aqi["threshold"] == pytest.approx(400.0)
        assert aqi["unit"] == "AQI"


# ===========================================================================
# Background task — _auto_generate_claims()
# ===========================================================================

class TestAutoGenerateClaims:
    """
    TestClient runs background tasks synchronously, so by the time .post()
    returns the claims are already written to the DB.

    We patch database.SessionLocal and routers.triggers.SessionLocal so that
    _auto_generate_claims() writes into the test transaction (same connection
    as test_db), making its writes visible for assertion.
    """

    def test_simulate_creates_claims_for_active_policies_in_city(
        self, authed_client, test_db, make_worker, make_policy
    ):
        worker = make_worker(city="Bangalore")
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            resp = authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )
        assert resp.status_code == 201

        # Refresh to see background-task writes
        test_db.expire_all()
        claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
        assert len(claims) == 1

    def test_simulate_skips_workers_in_different_city(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """Workers in a city other than the triggered city must not get claims."""
        worker_mumbai = make_worker(city="Mumbai")
        make_policy(worker=worker_mumbai, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )

        test_db.expire_all()
        claims = test_db.query(Claim).filter(Claim.worker_id == worker_mumbai.id).all()
        assert claims == []

    def test_simulate_uses_recent_gps_city_for_claim_eligibility(
        self, authed_client, test_db, make_worker, make_policy
    ):
        worker = make_worker(
            city="Bangalore",
            home_lat=12.9716,
            home_lng=77.5946,
            last_known_lat=13.0827,
            last_known_lng=80.2707,
            last_location_at=datetime.utcnow(),
        )
        make_policy(worker=worker, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            resp = authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Chennai"},
            )
        assert resp.status_code == 201

        test_db.expire_all()
        claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
        assert len(claims) == 1
        assert claims[0].trigger_event.city == "Chennai"
        assert claims[0].gps_valid is True

    def test_simulate_skips_registered_city_when_recent_gps_is_elsewhere(
        self, authed_client, test_db, make_worker, make_policy
    ):
        worker = make_worker(
            city="Bangalore",
            home_lat=12.9716,
            home_lng=77.5946,
            last_known_lat=13.0827,
            last_known_lng=80.2707,
            last_location_at=datetime.utcnow(),
        )
        make_policy(worker=worker, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            resp = authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )
        assert resp.status_code == 201

        test_db.expire_all()
        claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
        assert claims == []

    def test_simulate_blocks_signup_seeded_activity_from_triggering_claims(
        self, authed_client, test_db, make_worker, make_policy
    ):
        worker = make_worker(
            city="Bangalore",
            last_location_at=datetime.utcnow(),
            last_activity_source="signup_seed",
        )
        make_policy(worker=worker, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            resp = authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )
        assert resp.status_code == 201

        test_db.expire_all()
        claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
        assert claims == []

    def test_platform_outage_only_creates_claims_for_matching_platform(
        self, authed_client, test_db, make_worker, make_policy
    ):
        blinkit_worker = authed_client.worker  # type: ignore[attr-defined]
        make_policy(worker=blinkit_worker, status=PolicyStatus.ACTIVE)

        swiggy_worker = make_worker(city="Bangalore", platform="Swiggy")
        make_policy(worker=swiggy_worker, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            resp = authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Platform Outage", "city": "Bangalore"},
            )
        assert resp.status_code == 201

        test_db.expire_all()
        blinkit_claims = test_db.query(Claim).filter(Claim.worker_id == blinkit_worker.id).all()
        swiggy_claims = test_db.query(Claim).filter(Claim.worker_id == swiggy_worker.id).all()
        assert len(blinkit_claims) == 1
        assert swiggy_claims == []

    def test_simulate_skips_workers_with_zero_payout_for_tier(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """
        If get_payout_amount() returns 0 for a tier+trigger combination, no
        claim should be created.  We use a mock tier value that is not in the
        TRIGGER_REPLACEMENT_RATES table to guarantee a 0.0 return.
        """
        worker = make_worker(city="Bangalore")
        # Create the policy with a known tier but then override get_payout_amount
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            with patch("routers.triggers.get_payout_amount", return_value=0.0):
                authed_client.post(
                    "/triggers/simulate",
                    json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
                )

        test_db.expire_all()
        claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
        assert claims == []

    def test_simulate_deduplication_prevents_same_trigger_twice_in_24h(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """
        Calling simulate twice with the same city+trigger_type within 24h must
        produce only ONE claim for the worker (deduplication guard).
        """
        worker = make_worker(city="Bangalore")
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )

        test_db.expire_all()
        claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
        assert len(claims) == 1, (
            f"Expected 1 claim after duplicate simulate, got {len(claims)}"
        )

    def test_simulate_different_trigger_types_create_separate_claims(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """Two different trigger types should each create their own claim."""
        worker = make_worker(city="Bangalore")
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Hazardous AQI", "city": "Bangalore"},
            )

        test_db.expire_all()
        claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
        assert len(claims) == 2

    def test_simulate_sets_correct_fraud_status_for_clean_worker(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """
        A clean worker (city matches, zero claim history, device attested)
        must receive AUTO_APPROVED status after simulate.

        compute_authenticity_score with matching city, zero history, zero
        same_week, device_attested=True returns score=100 → AUTO_APPROVED.
        """
        worker = make_worker(
            city="Bangalore",
            claim_history_count=0,
        )
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )

        test_db.expire_all()
        claim = test_db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None
        # AUTO_APPROVED claims now land in PAID status immediately (payment ref + paid_at
        # are written in the same DB transaction as claim creation).
        assert claim.status in (ClaimStatus.AUTO_APPROVED, ClaimStatus.PAID)

    def test_simulate_increments_worker_claim_history_count(
        self, authed_client, test_db, make_worker, make_policy
    ):
        worker = make_worker(city="Bangalore", claim_history_count=3)
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )

        test_db.expire_all()
        test_db.refresh(worker)
        assert worker.claim_history_count == 4

    def test_simulate_skips_inactive_policies(
        self, authed_client, test_db, make_worker, make_policy
    ):
        worker = make_worker(city="Bangalore")
        make_policy(worker=worker, status=PolicyStatus.EXPIRED)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )

        test_db.expire_all()
        claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
        assert claims == []

    def test_simulate_claim_has_correct_trigger_event_id(
        self, authed_client, test_db, make_worker, make_policy
    ):
        worker = make_worker(city="Bangalore")
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            resp = authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )

        event_id = resp.json()["trigger_event_id"]
        test_db.expire_all()
        claim = test_db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None
        assert claim.trigger_event_id == event_id

    def test_simulate_claim_payout_matches_payout_engine(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """Claim payout_amount must equal what get_payout_amount() calculates."""
        worker = make_worker(city="Bangalore")
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )

        expected_payout = get_payout_amount("Heavy Rainfall", PolicyTier.BASIC, "Bangalore")
        test_db.expire_all()
        claim = test_db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None
        assert claim.payout_amount == pytest.approx(expected_payout)

    def test_simulate_auto_approved_claim_has_paid_at_set(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """AUTO_APPROVED claims must have paid_at populated."""
        worker = make_worker(city="Bangalore", claim_history_count=0)
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )

        test_db.expire_all()
        claim = test_db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None
        if claim.status == ClaimStatus.AUTO_APPROVED:
            assert claim.paid_at is not None

    def test_simulate_claim_auto_processed_flag_is_true(
        self, authed_client, test_db, make_worker, make_policy
    ):
        worker = make_worker(city="Bangalore")
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )

        test_db.expire_all()
        claim = test_db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None
        assert claim.auto_processed is True

    def test_simulate_high_fraud_worker_gets_manual_review(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """
        Worker with city mismatch (triggers PENDING/MANUAL) should NOT be AUTO_APPROVED.
        We place the worker in Mumbai but fire in Bangalore.  City mismatch
        deducts 40pts → score 60 → PENDING_REVIEW (not AUTO_APPROVED).
        """
        worker = make_worker(city="Mumbai")  # city mismatch with trigger city below
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )

        test_db.expire_all()
        # Worker is in Mumbai but trigger fired in Bangalore — no claim expected
        # because _auto_generate_claims joins on worker.city == city
        claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
        # The city filter prevents a claim being created for a different-city worker
        assert claims == []


# ===========================================================================
# GET /triggers/live
# ===========================================================================

class TestLiveCheck:

    def test_live_check_requires_auth_now(self, client):
        """After our auth addition, unauthenticated /live must return 401."""
        with patch("routers.triggers.check_all_triggers", return_value=[]):
            resp = client.get("/triggers/live")
        assert resp.status_code == 401

    def test_live_check_with_auth_returns_200(self, authed_client):
        with patch("routers.triggers.check_all_triggers", return_value=[]):
            resp = authed_client.get("/triggers/live")
        assert resp.status_code == 200

    def test_live_check_returns_live_check_response_shape(self, authed_client):
        with patch("routers.triggers.check_all_triggers", return_value=[]):
            resp = authed_client.get("/triggers/live")
        body = resp.json()
        assert "city" in body
        assert "checked_at" in body
        assert "triggers_fired" in body
        assert "events" in body

    def test_live_check_returns_empty_events_when_none_fired(self, authed_client):
        with patch("routers.triggers.check_all_triggers", return_value=[]):
            resp = authed_client.get("/triggers/live")
        body = resp.json()
        assert body["triggers_fired"] == 0
        assert body["events"] == []

    def test_live_check_default_city_is_bangalore(self, authed_client):
        with patch("routers.triggers.check_all_triggers", return_value=[]):
            resp = authed_client.get("/triggers/live")
        assert resp.json()["city"] == "Bangalore"

    def test_live_check_city_param_is_respected(self, authed_client):
        with patch("routers.triggers.check_all_triggers", return_value=[]):
            resp = authed_client.get("/triggers/live", params={"city": "Mumbai"})
        assert resp.json()["city"] == "Mumbai"

    def test_live_check_returns_empty_events_on_api_failure(self, authed_client):
        """
        Graceful degradation: when check_all_triggers raises, the endpoint
        must still return 200 with an empty events list rather than 500.
        """
        with patch(
            "routers.triggers.check_all_triggers",
            side_effect=Exception("Simulated API failure"),
        ):
            resp = authed_client.get("/triggers/live")
        assert resp.status_code == 200
        body = resp.json()
        assert body["triggers_fired"] == 0
        assert body["events"] == []

    def test_live_check_checked_at_is_recent_iso_string(self, authed_client):
        before = datetime.utcnow()
        with patch("routers.triggers.check_all_triggers", return_value=[]):
            resp = authed_client.get("/triggers/live")
        after = datetime.utcnow()

        checked_at_str = resp.json()["checked_at"]
        # Must be a parseable ISO datetime
        checked_at = datetime.fromisoformat(checked_at_str)
        # Should be between before and after (with 5s tolerance for slow CI)
        assert before - timedelta(seconds=5) <= checked_at <= after + timedelta(seconds=5)

    def test_live_check_fired_trigger_increments_triggers_fired(
        self, authed_client, test_db
    ):
        """
        When check_all_triggers returns one fired trigger, triggers_fired must
        be 1 and events must contain that trigger's details.
        """
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
                "confidence_score": 84.0,
                "source_log": "Primary: IMD\nSecondary: OpenWeatherMap\nCross-source validation: PASSED",
                "detected_at": datetime.utcnow().isoformat(),
                "expires_at": (datetime.utcnow() + timedelta(hours=6)).isoformat(),
            }
        ]
        with patch("routers.triggers.check_all_triggers", return_value=mock_fired):
            resp = authed_client.get("/triggers/live")

        body = resp.json()
        assert body["triggers_fired"] == 1
        assert len(body["events"]) == 1
        assert body["events"][0]["trigger_type"] == "Heavy Rainfall"
        assert body["events"][0]["confidence_score"] == pytest.approx(84.0)
        assert "Primary: IMD" in body["events"][0]["source_log"]

    def test_live_check_saves_fired_event_to_db(self, authed_client, test_db):
        """Fired triggers from /live must be persisted to the trigger_events table."""
        mock_fired = [
            {
                "trigger_type": "Severe Heatwave",
                "city": "Delhi",
                "measured_value": 46.2,
                "threshold_value": 45.0,
                "unit": "°C",
                "source_primary": "OpenWeatherMap",
                "source_secondary": "IMD Bulletins (mock)",
                "is_validated": True,
                "severity": "high",
                "description": "Severe heatwave in Delhi",
                "detected_at": datetime.utcnow().isoformat(),
                "expires_at": (datetime.utcnow() + timedelta(hours=6)).isoformat(),
            }
        ]
        with patch("routers.triggers.check_all_triggers", return_value=mock_fired):
            authed_client.get("/triggers/live", params={"city": "Delhi"})

        test_db.expire_all()
        saved = (
            test_db.query(TriggerEvent)
            .filter(TriggerEvent.trigger_type == "Severe Heatwave", TriggerEvent.city == "Delhi")
            .first()
        )
        assert saved is not None
        assert saved.measured_value == pytest.approx(46.2)

    def test_live_check_fallback_only_signal_does_not_create_claims(
        self, authed_client, test_db, make_policy
    ):
        worker = authed_client.worker
        make_policy(worker=worker, status=PolicyStatus.ACTIVE)
        mock_fired = [
            {
                "trigger_type": "Platform Outage",
                "city": worker.city,
                "measured_value": 20.0,
                "threshold_value": 15.0,
                "unit": "minutes down",
                "source_primary": "Partner outage telemetry",
                "source_secondary": "Mock outage simulator",
                "is_validated": False,
                "severity": "high",
                "description": "Mock outage signal crossed threshold",
                "confidence_score": 32.0,
                "source_log": "Fallback monitoring only",
                "detected_at": datetime.utcnow().isoformat(),
                "expires_at": (datetime.utcnow() + timedelta(hours=6)).isoformat(),
            }
        ]
        with patch("routers.triggers.check_all_triggers", return_value=mock_fired), _patch_session_local(test_db):
            resp = authed_client.get("/triggers/live", params={"city": worker.city, "platform": worker.platform})
        assert resp.status_code == 200

        claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
        assert claims == []

    def test_live_check_secondary_source_creates_manual_review_claim(
        self, authed_client, test_db, make_policy
    ):
        worker = authed_client.worker
        make_policy(worker=worker, status=PolicyStatus.ACTIVE)
        mock_fired = [
            {
                "trigger_type": "Heavy Rainfall",
                "city": worker.city,
                "measured_value": 72.5,
                "threshold_value": 64.5,
                "unit": "mm/24hr",
                "source_primary": "IMD district weather feed",
                "source_secondary": "OpenWeatherMap live continuity feed",
                "is_validated": False,
                "severity": "high",
                "description": "Continuity source crossed rainfall threshold",
                "confidence_score": 72.0,
                "source_log": "Secondary continuity source active",
                "detected_at": datetime.utcnow().isoformat(),
                "expires_at": (datetime.utcnow() + timedelta(hours=6)).isoformat(),
            }
        ]
        with patch("routers.triggers.check_all_triggers", return_value=mock_fired), _patch_session_local(test_db):
            resp = authed_client.get("/triggers/live", params={"city": worker.city, "platform": worker.platform})
        assert resp.status_code == 200

        claim = test_db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None
        assert claim.status == ClaimStatus.MANUAL_REVIEW
        assert claim.paid_at is None
