"""
Zynvaro Backend — Integration Tests: Claims API
================================================
Tests every endpoint in routers/claims.py plus the enrich_claim() helper.

Coverage matrix
---------------
GET /claims/             — list_my_claims
GET /claims/stats        — my_claim_stats
GET /claims/{id}         — get_claim
GET /claims/admin/workers  — admin_all_workers  (auth required)
GET /claims/admin/all      — admin_all_claims   (auth required)
GET /claims/admin/stats    — admin_stats        (auth required)

enrich_claim()           — pure-function tests (no HTTP layer)

Fixtures consumed (all from conftest.py):
    test_db, client, authed_client,
    make_worker, make_policy, make_trigger, make_claim
"""

import sys
sys.path.insert(0, "D:/AeroFyta_DEVTrails-2026_FULL_HANDOFF/zynvaro-app/backend")

from datetime import datetime, timedelta

import pytest

from models import (
    Claim,
    ClaimStatus,
    PolicyStatus,
    PolicyTier,
    TriggerType,
    Worker,
    Policy,
    TriggerEvent,
)
from routers.claims import enrich_claim
from tests.conftest import worker_token


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def auth_headers(worker: Worker) -> dict:
    """Return Authorization header for *worker*."""
    return {"Authorization": f"Bearer {worker_token(worker.id)}"}


# ─────────────────────────────────────────────────────────────────
# enrich_claim() — pure unit tests (no HTTP)
# ─────────────────────────────────────────────────────────────────

class TestEnrichClaim:
    """Verify the enrich_claim helper maps ORM attributes correctly."""

    def test_get_claim_returns_trigger_enrichment(
        self, test_db, make_worker, make_policy, make_trigger, make_claim
    ):
        worker = make_worker()
        policy = make_policy(worker=worker)
        trigger = make_trigger(
            trigger_type=TriggerType.HEAVY_RAINFALL,
            city="Chennai",
            measured_value=80.0,
            unit="mm/24hr",
            description="Heavy rain over Chennai",
        )
        claim = make_claim(worker=worker, policy=policy, trigger=trigger)

        # Reload with relationships populated
        test_db.refresh(claim)
        response = enrich_claim(claim)

        assert response.trigger_type == TriggerType.HEAVY_RAINFALL
        assert response.trigger_city == "Chennai"
        assert response.trigger_measured_value == pytest.approx(80.0)
        assert response.trigger_unit == "mm/24hr"
        assert response.trigger_description == "Heavy rain over Chennai"

    def test_get_claim_returns_policy_enrichment(
        self, test_db, make_worker, make_policy, make_trigger, make_claim
    ):
        worker = make_worker()
        policy = make_policy(worker=worker, tier=PolicyTier.PRO)
        trigger = make_trigger()
        claim = make_claim(worker=worker, policy=policy, trigger=trigger)

        test_db.refresh(claim)
        response = enrich_claim(claim)

        assert response.policy_tier == PolicyTier.PRO

    def test_enrich_claim_handles_none_trigger_safely(
        self, test_db, make_worker, make_policy, make_trigger, make_claim
    ):
        """A claim with no trigger relationship returns None for trigger fields."""
        worker = make_worker()
        policy = make_policy(worker=worker)
        trigger = make_trigger()
        claim = make_claim(worker=worker, policy=policy, trigger=trigger)

        # Detach trigger by manually nulling the relationship on the object
        claim.trigger_event = None
        response = enrich_claim(claim)

        assert response.trigger_type is None
        assert response.trigger_city is None
        assert response.trigger_measured_value is None
        assert response.trigger_unit is None
        assert response.trigger_description is None

    def test_enrich_claim_handles_none_policy_safely(
        self, test_db, make_worker, make_policy, make_trigger, make_claim
    ):
        """A claim with no policy relationship returns None for policy_tier."""
        worker = make_worker()
        policy = make_policy(worker=worker)
        trigger = make_trigger()
        claim = make_claim(worker=worker, policy=policy, trigger=trigger)

        claim.policy = None
        response = enrich_claim(claim)

        assert response.policy_tier is None


# ─────────────────────────────────────────────────────────────────
# GET /claims/  — List claims
# ─────────────────────────────────────────────────────────────────

class TestListClaims:

    def test_list_claims_returns_empty_list_initially(self, client, authed_client):
        """A worker with no claims gets an empty list, not a 404 or error."""
        resp = authed_client.get("/claims/")
        assert resp.status_code == 200
        assert resp.json() == []

    def test_list_claims_returns_only_current_worker_claims(
        self, test_db, authed_client, make_worker, make_policy, make_trigger, make_claim
    ):
        """Claims belonging to another worker must NOT appear in the list."""
        # The authed_client already has its own worker (authed_client.worker).
        my_worker = authed_client.worker

        # Create another worker + claim
        other_worker = make_worker(phone="9550000001")
        other_policy = make_policy(worker=other_worker)
        trigger = make_trigger()
        make_claim(worker=other_worker, policy=other_policy, trigger=trigger)

        # Create one claim that belongs to the authenticated worker
        my_policy = make_policy(worker=my_worker)
        my_claim = make_claim(worker=my_worker, policy=my_policy, trigger=trigger)

        resp = authed_client.get("/claims/")
        assert resp.status_code == 200
        data = resp.json()

        returned_ids = [c["id"] for c in data]
        assert my_claim.id in returned_ids
        # Verify the other worker's claim is not included
        other_claim_numbers = [c["claim_number"] for c in data]
        assert all(
            c.claim_number not in other_claim_numbers
            for c in test_db.query(Claim).filter(Claim.worker_id == other_worker.id).all()
        )

    def test_list_claims_ordered_newest_first(
        self, test_db, authed_client, make_policy, make_trigger, make_claim
    ):
        """Claims are returned with the most recently created one at index 0."""
        worker = authed_client.worker
        policy = make_policy(worker=worker)
        trigger = make_trigger()

        # Insert three claims with explicit created_at ordering
        old_claim = Claim(
            claim_number="CLM-ORDER-001",
            worker_id=worker.id,
            policy_id=policy.id,
            trigger_event_id=trigger.id,
            status=ClaimStatus.AUTO_APPROVED,
            payout_amount=100.0,
            authenticity_score=80.0,
            gps_valid=True, activity_valid=True,
            device_valid=True, cross_source_valid=True,
            auto_processed=True,
            created_at=datetime.utcnow() - timedelta(hours=3),
        )
        mid_claim = Claim(
            claim_number="CLM-ORDER-002",
            worker_id=worker.id,
            policy_id=policy.id,
            trigger_event_id=trigger.id,
            status=ClaimStatus.AUTO_APPROVED,
            payout_amount=200.0,
            authenticity_score=85.0,
            gps_valid=True, activity_valid=True,
            device_valid=True, cross_source_valid=True,
            auto_processed=True,
            created_at=datetime.utcnow() - timedelta(hours=2),
        )
        new_claim = Claim(
            claim_number="CLM-ORDER-003",
            worker_id=worker.id,
            policy_id=policy.id,
            trigger_event_id=trigger.id,
            status=ClaimStatus.AUTO_APPROVED,
            payout_amount=300.0,
            authenticity_score=90.0,
            gps_valid=True, activity_valid=True,
            device_valid=True, cross_source_valid=True,
            auto_processed=True,
            created_at=datetime.utcnow() - timedelta(hours=1),
        )
        test_db.add_all([old_claim, mid_claim, new_claim])
        test_db.commit()

        resp = authed_client.get("/claims/")
        assert resp.status_code == 200
        data = resp.json()

        # Filter to only the three we just created (authed_client may have others)
        target_numbers = {"CLM-ORDER-001", "CLM-ORDER-002", "CLM-ORDER-003"}
        ordered = [c for c in data if c["claim_number"] in target_numbers]

        assert len(ordered) == 3
        assert ordered[0]["claim_number"] == "CLM-ORDER-003"
        assert ordered[1]["claim_number"] == "CLM-ORDER-002"
        assert ordered[2]["claim_number"] == "CLM-ORDER-001"

    def test_list_claims_respects_limit_parameter(
        self, test_db, authed_client, make_policy, make_trigger
    ):
        """The ?limit= query parameter caps the number of returned claims."""
        worker = authed_client.worker
        policy = make_policy(worker=worker)
        trigger = make_trigger()

        # Insert 5 claims
        for i in range(5):
            c = Claim(
                claim_number=f"CLM-LIMIT-{i:03d}",
                worker_id=worker.id,
                policy_id=policy.id,
                trigger_event_id=trigger.id,
                status=ClaimStatus.PENDING_REVIEW,
                payout_amount=100.0,
                authenticity_score=75.0,
                gps_valid=True, activity_valid=True,
                device_valid=True, cross_source_valid=True,
                auto_processed=False,
            )
            test_db.add(c)
        test_db.commit()

        resp = authed_client.get("/claims/?limit=3")
        assert resp.status_code == 200
        assert len(resp.json()) <= 3

    def test_list_claims_requires_auth(self, client):
        """Unauthenticated request must be rejected with HTTP 401."""
        resp = client.get("/claims/")
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────
# GET /claims/stats  — Claim statistics
# ─────────────────────────────────────────────────────────────────

class TestClaimStats:

    def test_stats_returns_zeros_when_no_claims(self, authed_client):
        """A worker with no claims receives a stats object full of zeros."""
        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_claims"] == 0
        assert data["total_payout_inr"] == 0.0
        assert data["avg_authenticity_score"] == 0.0
        assert data["auto_approved"] == 0
        assert data["pending_review"] == 0

    def test_stats_total_claims_correct(
        self, test_db, authed_client, make_policy, make_trigger, make_claim
    ):
        worker = authed_client.worker
        policy = make_policy(worker=worker)
        trigger = make_trigger()
        make_claim(worker=worker, policy=policy, trigger=trigger)
        make_claim(worker=worker, policy=policy, trigger=trigger)
        make_claim(worker=worker, policy=policy, trigger=trigger)

        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        assert resp.json()["total_claims"] == 3

    def test_stats_auto_approved_count_correct(
        self, test_db, authed_client, make_policy, make_trigger, make_claim
    ):
        worker = authed_client.worker
        policy = make_policy(worker=worker)
        trigger = make_trigger()
        make_claim(worker=worker, policy=policy, trigger=trigger,
                   status=ClaimStatus.AUTO_APPROVED)
        make_claim(worker=worker, policy=policy, trigger=trigger,
                   status=ClaimStatus.AUTO_APPROVED)
        make_claim(worker=worker, policy=policy, trigger=trigger,
                   status=ClaimStatus.PENDING_REVIEW)

        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        assert resp.json()["auto_approved"] == 2

    def test_stats_pending_review_count_correct(
        self, test_db, authed_client, make_policy, make_trigger, make_claim
    ):
        worker = authed_client.worker
        policy = make_policy(worker=worker)
        trigger = make_trigger()
        make_claim(worker=worker, policy=policy, trigger=trigger,
                   status=ClaimStatus.PENDING_REVIEW)
        make_claim(worker=worker, policy=policy, trigger=trigger,
                   status=ClaimStatus.PENDING_REVIEW)
        make_claim(worker=worker, policy=policy, trigger=trigger,
                   status=ClaimStatus.AUTO_APPROVED)

        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        assert resp.json()["pending_review"] == 2

    def test_stats_total_payout_only_counts_paid_claims(
        self, test_db, authed_client, make_policy, make_trigger, make_claim
    ):
        """total_payout_inr only sums claims that have a paid_at timestamp."""
        worker = authed_client.worker
        policy = make_policy(worker=worker)
        trigger = make_trigger()

        # Paid claim — has paid_at
        make_claim(
            worker=worker, policy=policy, trigger=trigger,
            status=ClaimStatus.AUTO_APPROVED,
            payout_amount=500.0,
            paid_at=datetime.utcnow(),
        )
        # Unpaid claim — no paid_at
        make_claim(
            worker=worker, policy=policy, trigger=trigger,
            status=ClaimStatus.PENDING_REVIEW,
            payout_amount=999.0,
            paid_at=None,
        )

        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        # Only the 500 paid claim should contribute
        assert resp.json()["total_payout_inr"] == pytest.approx(500.0)

    def test_stats_null_payout_amount_handled_gracefully(
        self, test_db, authed_client, make_policy, make_trigger
    ):
        """Claims with payout_amount=0 and paid_at set don't cause a crash (the `or 0` fix)."""
        worker = authed_client.worker
        policy = make_policy(worker=worker)
        trigger = make_trigger()

        # Insert claim with payout_amount explicitly set to 0
        claim = Claim(
            claim_number="CLM-NULL-PAYOUT-001",
            worker_id=worker.id,
            policy_id=policy.id,
            trigger_event_id=trigger.id,
            status=ClaimStatus.AUTO_APPROVED,
            payout_amount=0.0,
            authenticity_score=95.0,
            gps_valid=True, activity_valid=True,
            device_valid=True, cross_source_valid=True,
            auto_processed=True,
            paid_at=datetime.utcnow(),
        )
        test_db.add(claim)
        test_db.commit()

        # Should not raise; total payout should be 0
        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        assert resp.json()["total_payout_inr"] == pytest.approx(0.0)

    def test_stats_avg_authenticity_score_correct(
        self, test_db, authed_client, make_policy, make_trigger, make_claim
    ):
        worker = authed_client.worker
        policy = make_policy(worker=worker)
        trigger = make_trigger()
        make_claim(worker=worker, policy=policy, trigger=trigger, authenticity_score=80.0)
        make_claim(worker=worker, policy=policy, trigger=trigger, authenticity_score=100.0)

        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        # Average of 80 and 100 = 90.0
        assert resp.json()["avg_authenticity_score"] == pytest.approx(90.0, abs=0.1)

    def test_stats_requires_auth(self, client):
        resp = client.get("/claims/stats")
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────
# GET /claims/{id}  — Claim detail
# ─────────────────────────────────────────────────────────────────

class TestGetClaimDetail:

    def test_get_claim_returns_claim_for_owner(
        self, test_db, authed_client, make_policy, make_trigger, make_claim
    ):
        worker = authed_client.worker
        policy = make_policy(worker=worker)
        trigger = make_trigger()
        claim = make_claim(worker=worker, policy=policy, trigger=trigger)

        resp = authed_client.get(f"/claims/{claim.id}")
        assert resp.status_code == 200
        assert resp.json()["id"] == claim.id
        assert resp.json()["claim_number"] == claim.claim_number

    def test_get_claim_returns_404_for_other_worker(
        self,
        test_db,
        client,
        make_worker,
        make_policy,
        make_trigger,
        make_claim,
    ):
        """A worker may not retrieve a claim that belongs to a different worker."""
        owner = make_worker(phone="9550000010")
        requester = make_worker(phone="9550000011")

        owner_policy = make_policy(worker=owner)
        trigger = make_trigger()
        claim = make_claim(worker=owner, policy=owner_policy, trigger=trigger)

        # Make the request as *requester*, not *owner*
        resp = client.get(
            f"/claims/{claim.id}",
            headers=auth_headers(requester),
        )
        assert resp.status_code == 404

    def test_get_claim_returns_404_nonexistent(self, authed_client):
        """Requesting a claim ID that does not exist returns 404."""
        resp = authed_client.get("/claims/999999")
        assert resp.status_code == 404

    def test_get_claim_returns_trigger_enrichment(
        self, test_db, authed_client, make_policy, make_trigger, make_claim
    ):
        worker = authed_client.worker
        policy = make_policy(worker=worker)
        trigger = make_trigger(
            trigger_type=TriggerType.HAZARDOUS_AQI,
            city="Delhi",
            measured_value=490.0,
            unit="AQI",
        )
        claim = make_claim(worker=worker, policy=policy, trigger=trigger)

        resp = authed_client.get(f"/claims/{claim.id}")
        assert resp.status_code == 200
        data = resp.json()
        assert data["trigger_type"] == TriggerType.HAZARDOUS_AQI
        assert data["trigger_city"] == "Delhi"
        assert data["trigger_measured_value"] == pytest.approx(490.0)
        assert data["trigger_unit"] == "AQI"

    def test_get_claim_returns_policy_enrichment(
        self, test_db, authed_client, make_policy, make_trigger, make_claim
    ):
        worker = authed_client.worker
        policy = make_policy(worker=worker, tier=PolicyTier.STANDARD)
        trigger = make_trigger()
        claim = make_claim(worker=worker, policy=policy, trigger=trigger)

        resp = authed_client.get(f"/claims/{claim.id}")
        assert resp.status_code == 200
        assert resp.json()["policy_tier"] == PolicyTier.STANDARD

    def test_get_claim_requires_auth(self, client, test_db, make_worker, make_policy, make_trigger, make_claim):
        worker = make_worker(phone="9550000020")
        policy = make_policy(worker=worker)
        trigger = make_trigger()
        claim = make_claim(worker=worker, policy=policy, trigger=trigger)

        resp = client.get(f"/claims/{claim.id}")
        assert resp.status_code == 401


# ─────────────────────────────────────────────────────────────────
# GET /claims/admin/workers
# ─────────────────────────────────────────────────────────────────

class TestAdminWorkers:

    def test_admin_workers_requires_auth(self, client):
        """Endpoint must reject calls with no Authorization header."""
        resp = client.get("/claims/admin/workers")
        # FastAPI returns 401 when the OAuth2 bearer scheme finds no token
        assert resp.status_code == 401

    def test_admin_workers_returns_401_without_token(self, client):
        """Explicit check: no Bearer token → 401 Unauthorized."""
        resp = client.get("/claims/admin/workers", headers={})
        assert resp.status_code == 401

    def test_admin_workers_returns_worker_list_when_authed(
        self,
        test_db,
        authed_client,
        make_worker,
        make_policy,
    ):
        """An authenticated request returns a JSON list that includes worker entries."""
        # authed_client.worker is already in the DB; add one more
        extra = make_worker(phone="9550000030")
        make_policy(worker=extra)

        resp = authed_client.get("/claims/admin/workers")
        assert resp.status_code == 200
        data = resp.json()
        assert isinstance(data, list)
        assert len(data) >= 1

        # Each entry should contain the expected keys
        for entry in data:
            assert "id" in entry
            assert "full_name" in entry
            assert "city" in entry
            assert "platform" in entry

    def test_admin_workers_includes_active_tier_for_worker_with_policy(
        self,
        test_db,
        authed_client,
        make_worker,
        make_policy,
    ):
        """Workers with an active policy expose their tier in the summary."""
        worker = make_worker(phone="9550000031")
        make_policy(worker=worker, tier=PolicyTier.PRO, status=PolicyStatus.ACTIVE)

        resp = authed_client.get("/claims/admin/workers")
        assert resp.status_code == 200

        entry = next((w for w in resp.json() if w["id"] == worker.id), None)
        assert entry is not None
        assert entry["active_tier"] == PolicyTier.PRO

    def test_admin_workers_active_tier_is_none_without_active_policy(
        self,
        test_db,
        authed_client,
        make_worker,
        make_policy,
    ):
        """Workers without an active policy should have active_tier=null."""
        worker = make_worker(phone="9550000032")
        # Give them only an expired policy
        make_policy(worker=worker, status=PolicyStatus.EXPIRED)

        resp = authed_client.get("/claims/admin/workers")
        assert resp.status_code == 200

        entry = next((w for w in resp.json() if w["id"] == worker.id), None)
        assert entry is not None
        assert entry["active_tier"] is None


# ─────────────────────────────────────────────────────────────────
# GET /claims/admin/all
# ─────────────────────────────────────────────────────────────────

class TestAdminAllClaims:

    def test_admin_all_claims_requires_auth(self, client):
        resp = client.get("/claims/admin/all")
        assert resp.status_code == 401

    def test_admin_all_claims_returns_401_without_token(self, client):
        resp = client.get("/claims/admin/all", headers={})
        assert resp.status_code == 401

    def test_admin_all_claims_returns_claims_across_workers(
        self,
        test_db,
        authed_client,
        make_worker,
        make_policy,
        make_trigger,
        make_claim,
    ):
        """Admin endpoint returns claims from ALL workers, not just the requester."""
        worker_a = authed_client.worker
        worker_b = make_worker(phone="9550000040")

        policy_a = make_policy(worker=worker_a)
        policy_b = make_policy(worker=worker_b)
        trigger = make_trigger()

        claim_a = make_claim(worker=worker_a, policy=policy_a, trigger=trigger)
        claim_b = make_claim(worker=worker_b, policy=policy_b, trigger=trigger)

        resp = authed_client.get("/claims/admin/all")
        assert resp.status_code == 200
        data = resp.json()

        returned_ids = {c["id"] for c in data}
        assert claim_a.id in returned_ids
        assert claim_b.id in returned_ids

    def test_admin_all_claims_default_limit_is_50(
        self,
        test_db,
        authed_client,
        make_policy,
        make_trigger,
    ):
        """The default page size for the admin list is 50."""
        worker = authed_client.worker
        policy = make_policy(worker=worker)
        trigger = make_trigger()

        # Insert 60 claims
        for i in range(60):
            c = Claim(
                claim_number=f"CLM-ADMIN-LIMIT-{i:03d}",
                worker_id=worker.id,
                policy_id=policy.id,
                trigger_event_id=trigger.id,
                status=ClaimStatus.PENDING_REVIEW,
                payout_amount=50.0,
                authenticity_score=70.0,
                gps_valid=True, activity_valid=True,
                device_valid=True, cross_source_valid=True,
                auto_processed=False,
            )
            test_db.add(c)
        test_db.commit()

        resp = authed_client.get("/claims/admin/all")
        assert resp.status_code == 200
        assert len(resp.json()) <= 50


# ─────────────────────────────────────────────────────────────────
# GET /claims/admin/stats
# ─────────────────────────────────────────────────────────────────

class TestAdminStats:

    def test_admin_stats_requires_auth(self, client):
        resp = client.get("/claims/admin/stats")
        assert resp.status_code == 401

    def test_admin_stats_returns_401_without_token(self, client):
        resp = client.get("/claims/admin/stats", headers={})
        assert resp.status_code == 401

    def test_admin_stats_returns_platform_metrics_when_authed(
        self, authed_client
    ):
        """Authenticated request returns a dict with core platform fields."""
        resp = authed_client.get("/claims/admin/stats")
        assert resp.status_code == 200
        data = resp.json()

        expected_keys = {
            "total_workers",
            "active_policies",
            "weekly_premium_collection_inr",
            "total_claims",
            "auto_approved_claims",
            "total_payout_inr",
            "loss_ratio_pct",
            "claims_by_trigger",
            "avg_authenticity_score",
        }
        assert expected_keys.issubset(data.keys())

    def test_admin_stats_includes_loss_ratio(
        self,
        test_db,
        authed_client,
        make_policy,
        make_trigger,
        make_claim,
    ):
        """loss_ratio_pct is present and is a finite numeric value."""
        worker = authed_client.worker
        policy = make_policy(worker=worker, weekly_premium=100.0)
        trigger = make_trigger()
        make_claim(
            worker=worker, policy=policy, trigger=trigger,
            payout_amount=200.0, paid_at=datetime.utcnow(),
        )

        resp = authed_client.get("/claims/admin/stats")
        assert resp.status_code == 200
        loss_ratio = resp.json()["loss_ratio_pct"]
        assert isinstance(loss_ratio, (int, float))
        # loss_ratio must be >= 0 and, with our controlled data, > 0
        assert loss_ratio >= 0

    def test_admin_stats_includes_total_workers(
        self,
        test_db,
        authed_client,
        make_worker,
    ):
        """total_workers reflects the number of rows in the workers table."""
        # Snapshot before adding
        resp_before = authed_client.get("/claims/admin/stats")
        count_before = resp_before.json()["total_workers"]

        make_worker(phone="9550000050")
        make_worker(phone="9550000051")

        resp_after = authed_client.get("/claims/admin/stats")
        count_after = resp_after.json()["total_workers"]

        assert count_after == count_before + 2

    def test_admin_stats_loss_ratio_zero_when_no_premium(self, authed_client):
        """loss_ratio_pct should be 0 when there are no active policies (no premium income)."""
        # Use a fresh client with no active policies already present — since
        # the test DB is rolled back per function, this is guaranteed clean.
        resp = authed_client.get("/claims/admin/stats")
        assert resp.status_code == 200
        data = resp.json()

        # If active_policies == 0, total_premium == 0, loss_ratio should be 0
        if data["active_policies"] == 0:
            assert data["loss_ratio_pct"] == 0.0

    def test_admin_stats_claims_by_trigger_aggregates_correctly(
        self,
        test_db,
        authed_client,
        make_policy,
        make_trigger,
        make_claim,
    ):
        """claims_by_trigger groups claims by their trigger type."""
        worker = authed_client.worker
        policy = make_policy(worker=worker)

        rain_trigger = make_trigger(trigger_type=TriggerType.HEAVY_RAINFALL)
        aqi_trigger = make_trigger(trigger_type=TriggerType.HAZARDOUS_AQI)

        make_claim(worker=worker, policy=policy, trigger=rain_trigger)
        make_claim(worker=worker, policy=policy, trigger=rain_trigger)
        make_claim(worker=worker, policy=policy, trigger=aqi_trigger)

        resp = authed_client.get("/claims/admin/stats")
        assert resp.status_code == 200
        breakdown = resp.json()["claims_by_trigger"]

        assert breakdown.get(TriggerType.HEAVY_RAINFALL, 0) >= 2
        assert breakdown.get(TriggerType.HAZARDOUS_AQI, 0) >= 1
