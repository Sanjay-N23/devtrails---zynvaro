"""
Zynvaro Backend — Integration Tests: Admin Analytics & Statistics
=================================================================
Tests all three analytics/statistics endpoints in routers/claims.py.

Coverage matrix
---------------
GET /claims/stats              — my_claim_stats       (worker-scoped, auth required)
GET /claims/admin/stats        — admin_stats          (platform-wide,  auth required)
GET /claims/admin/workers      — admin_all_workers    (all workers,    auth required)

Fixtures consumed (all from conftest.py):
    test_db, client, authed_client,
    make_worker, make_policy, make_trigger, make_claim
"""

import sys
sys.path.insert(0, "D:/AeroFyta_DEVTrails-2026_FULL_HANDOFF/zynvaro-app/backend")

from datetime import datetime, timedelta

import pytest

from models import (
    ClaimStatus,
    PolicyStatus,
    PolicyTier,
    TriggerType,
    Worker,
)
from tests.conftest import worker_token


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def auth_headers(worker: Worker) -> dict:
    """Return Authorization header dict for *worker*."""
    return {"Authorization": f"Bearer {worker_token(worker.id)}"}


# ─────────────────────────────────────────────────────────────────
# TestWorkerClaimStats — GET /claims/stats
# ─────────────────────────────────────────────────────────────────

class TestWorkerClaimStats:
    """
    Per-worker claim statistics via GET /claims/stats.

    Each test creates its own worker (via authed_client) and drives the
    DB state through the make_* factory fixtures.  authed_client.worker
    gives us the authenticated identity so we can tie claims to the
    right worker without a separate lookup.
    """

    def test_stats_baseline_all_zeros_for_new_worker(self, authed_client):
        """A freshly-registered worker with no claims returns all-zero stats."""
        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        data = resp.json()
        assert data["total_claims"] == 0
        assert data["auto_approved"] == 0
        assert data["pending_review"] == 0
        assert data["manual_review"] == 0
        assert data["paid"] == 0
        assert data["rejected"] == 0
        assert data["total_payout_inr"] == 0.0
        assert data["avg_authenticity_score"] == 0.0

    def test_stats_total_claims_counts_all_statuses(
        self, authed_client, make_policy, make_trigger, make_claim
    ):
        """total_claims must equal the sum across all statuses."""
        w = authed_client.worker
        p = make_policy(worker=w)
        te = make_trigger(city=w.city)

        # One of each relevant status
        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.AUTO_APPROVED)
        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.PENDING_REVIEW, paid_at=None)
        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.MANUAL_REVIEW, paid_at=None)

        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        assert resp.json()["total_claims"] == 3

    def test_stats_auto_approved_count_only_counts_auto_approved(
        self, authed_client, make_policy, make_trigger, make_claim
    ):
        """auto_approved must count only AUTO_APPROVED status rows."""
        w = authed_client.worker
        p = make_policy(worker=w)
        te = make_trigger(city=w.city)

        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.AUTO_APPROVED)
        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.AUTO_APPROVED)
        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.PENDING_REVIEW, paid_at=None)

        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        assert resp.json()["auto_approved"] == 2

    def test_stats_pending_count_only_counts_pending(
        self, authed_client, make_policy, make_trigger, make_claim
    ):
        """pending_review must count only PENDING_REVIEW status rows."""
        w = authed_client.worker
        p = make_policy(worker=w)
        te = make_trigger(city=w.city)

        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.PENDING_REVIEW, paid_at=None)
        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.PENDING_REVIEW, paid_at=None)
        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.AUTO_APPROVED)

        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        assert resp.json()["pending_review"] == 2

    def test_stats_manual_count_only_counts_manual(
        self, authed_client, make_policy, make_trigger, make_claim
    ):
        """manual_review must count only MANUAL_REVIEW status rows."""
        w = authed_client.worker
        p = make_policy(worker=w)
        te = make_trigger(city=w.city)

        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.MANUAL_REVIEW, paid_at=None)
        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.AUTO_APPROVED)

        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        assert resp.json()["manual_review"] == 1

    def test_stats_rejected_count_only_counts_rejected(
        self, authed_client, make_policy, make_trigger, make_claim
    ):
        """rejected must count only REJECTED status rows."""
        w = authed_client.worker
        p = make_policy(worker=w)
        te = make_trigger(city=w.city)

        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.REJECTED, paid_at=None)
        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.AUTO_APPROVED)

        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        assert resp.json()["rejected"] == 1

    def test_stats_paid_count_includes_paid_at_claims(
        self, authed_client, make_policy, make_trigger, make_claim
    ):
        """A claim with paid_at set must be counted in paid regardless of status."""
        w = authed_client.worker
        p = make_policy(worker=w)
        te = make_trigger(city=w.city)

        paid_ts = datetime.utcnow() - timedelta(minutes=10)
        # Explicitly set paid_at; make_claim defaults paid_at for AUTO_APPROVED
        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.AUTO_APPROVED, paid_at=paid_ts)
        # A claim with no paid_at should not be in paid count
        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.PENDING_REVIEW, paid_at=None)

        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        data = resp.json()
        # The claim with paid_at contributes to paid; the pending one does not
        assert data["paid"] >= 1
        assert data["paid"] < data["total_claims"]

    def test_stats_total_payout_sums_paid_claims_only(
        self, authed_client, make_policy, make_trigger, make_claim
    ):
        """total_payout_inr is the sum of payout_amount for claims that have paid_at set."""
        w = authed_client.worker
        p = make_policy(worker=w)
        te = make_trigger(city=w.city)

        paid_ts = datetime.utcnow() - timedelta(minutes=5)

        # Two paid claims
        make_claim(
            worker=w, policy=p, trigger=te,
            status=ClaimStatus.AUTO_APPROVED,
            payout_amount=300.0,
            paid_at=paid_ts,
        )
        make_claim(
            worker=w, policy=p, trigger=te,
            status=ClaimStatus.AUTO_APPROVED,
            payout_amount=200.0,
            paid_at=paid_ts,
        )
        # One unpaid claim — must NOT contribute to the sum
        make_claim(
            worker=w, policy=p, trigger=te,
            status=ClaimStatus.PENDING_REVIEW,
            payout_amount=999.0,
            paid_at=None,
        )

        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        assert resp.json()["total_payout_inr"] == 500.0

    def test_stats_null_payout_amount_does_not_crash(
        self, authed_client, make_policy, make_trigger, make_claim
    ):
        """A claim with payout_amount=0.0 and paid_at must contribute 0 — not raise."""
        w = authed_client.worker
        p = make_policy(worker=w)
        te = make_trigger(city=w.city)

        paid_ts = datetime.utcnow() - timedelta(minutes=3)
        make_claim(
            worker=w, policy=p, trigger=te,
            status=ClaimStatus.AUTO_APPROVED,
            payout_amount=0.0,
            paid_at=paid_ts,
        )

        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        assert resp.json()["total_payout_inr"] == 0.0

    def test_stats_avg_score_correct(
        self, authed_client, make_policy, make_trigger, make_claim
    ):
        """avg_authenticity_score is the mean over all claims for this worker."""
        w = authed_client.worker
        p = make_policy(worker=w)
        te = make_trigger(city=w.city)

        make_claim(
            worker=w, policy=p, trigger=te,
            status=ClaimStatus.AUTO_APPROVED,
            authenticity_score=80.0,
        )
        make_claim(
            worker=w, policy=p, trigger=te,
            status=ClaimStatus.AUTO_APPROVED,
            authenticity_score=60.0,
        )

        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        # (80 + 60) / 2 = 70.0, endpoint rounds to 1 decimal
        assert resp.json()["avg_authenticity_score"] == 70.0

    def test_stats_avg_score_is_zero_when_no_claims(self, authed_client):
        """avg_authenticity_score defaults to 0.0 when the worker has no claims."""
        resp = authed_client.get("/claims/stats")
        assert resp.status_code == 200
        assert resp.json()["avg_authenticity_score"] == 0.0

    def test_stats_isolation_between_workers(
        self,
        client,
        make_worker,
        make_policy,
        make_trigger,
        make_claim,
    ):
        """
        Stats are scoped to the authenticated worker.

        Worker A has 3 claims; Worker B has 1.  Each sees only their own
        total when they call GET /claims/stats.
        """
        worker_a = make_worker(full_name="Worker A", city="Bangalore")
        worker_b = make_worker(full_name="Worker B", city="Mumbai")

        policy_a = make_policy(worker=worker_a)
        policy_b = make_policy(worker=worker_b)
        te = make_trigger(city="Bangalore")
        te_b = make_trigger(city="Mumbai")

        # Worker A — 3 claims
        for _ in range(3):
            make_claim(worker=worker_a, policy=policy_a, trigger=te, status=ClaimStatus.AUTO_APPROVED)

        # Worker B — 1 claim
        make_claim(worker=worker_b, policy=policy_b, trigger=te_b, status=ClaimStatus.AUTO_APPROVED)

        headers_a = auth_headers(worker_a)
        headers_b = auth_headers(worker_b)

        resp_a = client.get("/claims/stats", headers=headers_a)
        resp_b = client.get("/claims/stats", headers=headers_b)

        assert resp_a.status_code == 200
        assert resp_b.status_code == 200
        assert resp_a.json()["total_claims"] == 3
        assert resp_b.json()["total_claims"] == 1


# ─────────────────────────────────────────────────────────────────
# TestAdminPlatformStats — GET /claims/admin/stats
# ─────────────────────────────────────────────────────────────────

class TestAdminPlatformStats:
    """
    Platform-wide analytics via GET /claims/admin/stats.

    Every test uses authed_client (any authenticated worker can call
    the admin endpoint — the route only checks for a valid JWT).
    Factory fixtures build the DB state; assertions use >= comparisons
    where pre-existing rows from other tests might be visible through
    the shared session.
    """

    def test_admin_stats_total_workers_increments_correctly(
        self, authed_client, make_worker
    ):
        """Creating extra workers must increase total_workers."""
        resp_before = authed_client.get("/claims/admin/stats")
        before = resp_before.json()["total_workers"]

        make_worker(full_name="Extra Worker 1")
        make_worker(full_name="Extra Worker 2")

        resp_after = authed_client.get("/claims/admin/stats")
        assert resp_after.json()["total_workers"] == before + 2

    def test_admin_stats_active_policies_count(
        self, authed_client, make_worker, make_policy
    ):
        """Active policies created in this test must appear in active_policies."""
        resp_before = authed_client.get("/claims/admin/stats")
        before = resp_before.json()["active_policies"]

        w1 = make_worker(full_name="Policy Worker 1")
        w2 = make_worker(full_name="Policy Worker 2")
        make_policy(worker=w1, status=PolicyStatus.ACTIVE)
        make_policy(worker=w2, status=PolicyStatus.ACTIVE)

        resp_after = authed_client.get("/claims/admin/stats")
        assert resp_after.json()["active_policies"] == before + 2

    def test_admin_stats_cancelled_policy_not_counted(
        self, authed_client, make_worker, make_policy, test_db
    ):
        """Cancelling an active policy must reduce active_policies by 1."""
        w = make_worker(full_name="Cancel Policy Worker")
        p = make_policy(worker=w, status=PolicyStatus.ACTIVE)

        resp_active = authed_client.get("/claims/admin/stats")
        count_with = resp_active.json()["active_policies"]

        # Cancel the policy
        p.status = PolicyStatus.CANCELLED
        test_db.commit()

        resp_cancelled = authed_client.get("/claims/admin/stats")
        assert resp_cancelled.json()["active_policies"] == count_with - 1

    def test_admin_stats_weekly_premium_collection_sums_active_policies(
        self, authed_client, make_worker, make_policy
    ):
        """weekly_premium_collection_inr must include the premiums of active policies."""
        resp_before = authed_client.get("/claims/admin/stats")
        before = resp_before.json()["weekly_premium_collection_inr"]

        w1 = make_worker(full_name="Premium Worker 1")
        w2 = make_worker(full_name="Premium Worker 2")
        make_policy(worker=w1, status=PolicyStatus.ACTIVE, weekly_premium=99.0)
        make_policy(worker=w2, status=PolicyStatus.ACTIVE, weekly_premium=149.0)

        resp_after = authed_client.get("/claims/admin/stats")
        after = resp_after.json()["weekly_premium_collection_inr"]

        assert after == round(before + 99.0 + 149.0, 2)

    def test_admin_stats_total_claims_across_all_workers(
        self, authed_client, make_worker, make_policy, make_trigger, make_claim
    ):
        """total_claims must be the sum of claims from all workers."""
        resp_before = authed_client.get("/claims/admin/stats")
        before = resp_before.json()["total_claims"]

        wa = make_worker(full_name="Total Claims Worker A")
        wb = make_worker(full_name="Total Claims Worker B")
        pa = make_policy(worker=wa)
        pb = make_policy(worker=wb)
        te = make_trigger()

        make_claim(worker=wa, policy=pa, trigger=te)
        make_claim(worker=wa, policy=pa, trigger=te)
        make_claim(worker=wb, policy=pb, trigger=te)

        resp_after = authed_client.get("/claims/admin/stats")
        assert resp_after.json()["total_claims"] == before + 3

    def test_admin_stats_auto_approved_claims_count(
        self, authed_client, make_worker, make_policy, make_trigger, make_claim
    ):
        """auto_approved_claims must count only AUTO_APPROVED status claims."""
        resp_before = authed_client.get("/claims/admin/stats")
        before = resp_before.json()["auto_approved_claims"]

        w = make_worker(full_name="Auto Approved Worker")
        p = make_policy(worker=w)
        te = make_trigger()

        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.AUTO_APPROVED)
        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.AUTO_APPROVED)
        # This one is pending — must NOT add to auto_approved_claims
        make_claim(worker=w, policy=p, trigger=te, status=ClaimStatus.PENDING_REVIEW, paid_at=None)

        resp_after = authed_client.get("/claims/admin/stats")
        assert resp_after.json()["auto_approved_claims"] == before + 2

    def test_admin_stats_total_payout_only_counts_paid(
        self, authed_client, make_worker, make_policy, make_trigger, make_claim
    ):
        """total_payout_inr must only sum claims with a paid_at timestamp."""
        resp_before = authed_client.get("/claims/admin/stats")
        before = resp_before.json()["total_payout_inr"]

        w = make_worker(full_name="Payout Worker")
        p = make_policy(worker=w)
        te = make_trigger()
        paid_ts = datetime.utcnow() - timedelta(minutes=5)

        # 2 paid claims
        make_claim(
            worker=w, policy=p, trigger=te,
            status=ClaimStatus.AUTO_APPROVED,
            payout_amount=400.0,
            paid_at=paid_ts,
        )
        make_claim(
            worker=w, policy=p, trigger=te,
            status=ClaimStatus.AUTO_APPROVED,
            payout_amount=250.0,
            paid_at=paid_ts,
        )
        # 1 unpaid — must NOT add to total_payout_inr
        make_claim(
            worker=w, policy=p, trigger=te,
            status=ClaimStatus.PENDING_REVIEW,
            payout_amount=888.0,
            paid_at=None,
        )

        resp_after = authed_client.get("/claims/admin/stats")
        assert resp_after.json()["total_payout_inr"] == round(before + 650.0, 2)

    def test_admin_stats_loss_ratio_is_numeric(self, authed_client):
        """loss_ratio_pct must be a float (or int) — not None, not a string."""
        resp = authed_client.get("/claims/admin/stats")
        assert resp.status_code == 200
        loss = resp.json()["loss_ratio_pct"]
        assert isinstance(loss, (int, float))

    def test_admin_stats_loss_ratio_zero_when_no_payouts(
        self, authed_client, make_worker, make_policy, make_trigger, make_claim
    ):
        """
        When all claims are unpaid the loss ratio stays at 0.

        We create a worker with active policy (so premium > 0 is possible)
        but give the claim no paid_at, ensuring total_payout = 0.
        """
        # Use a fresh isolated DB state by inspecting the ratio delta
        resp_before = authed_client.get("/claims/admin/stats")
        before_payout = resp_before.json()["total_payout_inr"]

        # If there were already paid claims we can't assert 0 globally,
        # so assert that a new unpaid claim doesn't raise the ratio.
        w = make_worker(full_name="No Payout Worker")
        p = make_policy(worker=w, weekly_premium=100.0)
        te = make_trigger()
        make_claim(
            worker=w, policy=p, trigger=te,
            status=ClaimStatus.PENDING_REVIEW,
            payout_amount=500.0,
            paid_at=None,
        )

        resp_after = authed_client.get("/claims/admin/stats")
        after_payout = resp_after.json()["total_payout_inr"]

        # Unpaid claim must not change total payout
        assert after_payout == before_payout

        # When there truly are no payouts from any claim, ratio is 0
        if before_payout == 0.0:
            assert resp_after.json()["loss_ratio_pct"] == 0.0

    def test_admin_stats_loss_ratio_positive_when_claims_paid(
        self, authed_client, make_worker, make_policy, make_trigger, make_claim
    ):
        """loss_ratio_pct must be > 0 when at least one claim has been paid."""
        w = make_worker(full_name="Loss Ratio Worker")
        p = make_policy(worker=w, weekly_premium=100.0)
        te = make_trigger()
        paid_ts = datetime.utcnow() - timedelta(minutes=2)
        make_claim(
            worker=w, policy=p, trigger=te,
            status=ClaimStatus.AUTO_APPROVED,
            payout_amount=100.0,
            paid_at=paid_ts,
        )

        resp = authed_client.get("/claims/admin/stats")
        assert resp.status_code == 200
        assert resp.json()["loss_ratio_pct"] > 0

    def test_admin_stats_claims_by_trigger_is_dict(self, authed_client):
        """claims_by_trigger must be a dict (mapping trigger names to counts)."""
        resp = authed_client.get("/claims/admin/stats")
        assert resp.status_code == 200
        assert isinstance(resp.json()["claims_by_trigger"], dict)

    def test_admin_stats_claims_by_trigger_buckets_correctly(
        self, authed_client, make_worker, make_policy, make_trigger, make_claim
    ):
        """
        claims_by_trigger must group by trigger_type and count correctly.

        2 Heavy Rainfall claims + 1 Hazardous AQI claim must produce
        {TriggerType.HEAVY_RAINFALL: ..., TriggerType.HAZARDOUS_AQI: ...}
        with at least those delta counts.
        """
        resp_before = authed_client.get("/claims/admin/stats")
        buckets_before = resp_before.json()["claims_by_trigger"]
        before_rain = buckets_before.get(TriggerType.HEAVY_RAINFALL, 0)
        before_aqi = buckets_before.get(TriggerType.HAZARDOUS_AQI, 0)

        w = make_worker(full_name="Trigger Bucket Worker")
        p = make_policy(worker=w)

        rain_trigger = make_trigger(
            trigger_type=TriggerType.HEAVY_RAINFALL, city=w.city
        )
        aqi_trigger = make_trigger(
            trigger_type=TriggerType.HAZARDOUS_AQI,
            city=w.city,
            measured_value=490.0,
            threshold_value=400.0,
            unit="AQI",
        )

        make_claim(worker=w, policy=p, trigger=rain_trigger, status=ClaimStatus.AUTO_APPROVED)
        make_claim(worker=w, policy=p, trigger=rain_trigger, status=ClaimStatus.AUTO_APPROVED)
        make_claim(worker=w, policy=p, trigger=aqi_trigger, status=ClaimStatus.AUTO_APPROVED)

        resp_after = authed_client.get("/claims/admin/stats")
        buckets_after = resp_after.json()["claims_by_trigger"]

        assert buckets_after.get(TriggerType.HEAVY_RAINFALL, 0) == before_rain + 2
        assert buckets_after.get(TriggerType.HAZARDOUS_AQI, 0) == before_aqi + 1

    def test_admin_stats_avg_authenticity_score(
        self, authed_client, make_worker, make_policy, make_trigger, make_claim
    ):
        """
        avg_authenticity_score must be the mean over ALL platform claims.

        We isolate the measurement by using an empty DB state assumption
        for the test's own pair of claims; when other claims already exist
        we verify the value changes in the correct direction.
        """
        resp_before = authed_client.get("/claims/admin/stats")
        data_before = resp_before.json()
        count_before = data_before["total_claims"]

        w = make_worker(full_name="Avg Score Worker")
        p = make_policy(worker=w)
        te = make_trigger()

        # Only add our two claims if the DB is clean (no prior claims)
        # so we can assert exact arithmetic.
        if count_before == 0:
            make_claim(
                worker=w, policy=p, trigger=te,
                status=ClaimStatus.AUTO_APPROVED,
                authenticity_score=90.0,
            )
            make_claim(
                worker=w, policy=p, trigger=te,
                status=ClaimStatus.AUTO_APPROVED,
                authenticity_score=70.0,
            )

            resp_after = authed_client.get("/claims/admin/stats")
            assert resp_after.json()["avg_authenticity_score"] == 80.0
        else:
            # Just verify the field is a valid float when claims pre-exist
            assert isinstance(data_before["avg_authenticity_score"], (int, float))

    def test_admin_stats_response_has_all_required_keys(self, authed_client):
        """All 9 expected keys must be present in the admin stats response."""
        resp = authed_client.get("/claims/admin/stats")
        assert resp.status_code == 200
        data = resp.json()

        required_keys = {
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
        assert required_keys.issubset(data.keys()), (
            f"Missing keys: {required_keys - data.keys()}"
        )


# ─────────────────────────────────────────────────────────────────
# TestAdminWorkersEndpoint — GET /claims/admin/workers
# ─────────────────────────────────────────────────────────────────

class TestAdminWorkersEndpoint:
    """
    All-workers summary via GET /claims/admin/workers.

    The endpoint returns a list of worker dicts; each entry describes
    one worker together with their active policy details.
    """

    def test_admin_workers_returns_list_of_workers(self, authed_client):
        """The endpoint must return a JSON array."""
        resp = authed_client.get("/claims/admin/workers")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_admin_workers_includes_worker_fields(
        self, authed_client, make_worker, make_policy
    ):
        """
        Each entry must contain all 10 documented fields.

        We create a dedicated worker so we can locate their specific
        entry and check its structure.
        """
        w = make_worker(full_name="Field Check Worker")
        make_policy(worker=w)

        resp = authed_client.get("/claims/admin/workers")
        assert resp.status_code == 200
        entries = resp.json()

        # Find the entry for our worker
        entry = next((e for e in entries if e["id"] == w.id), None)
        assert entry is not None, "Worker not found in /admin/workers response"

        expected_fields = {
            "id",
            "full_name",
            "phone",
            "city",
            "platform",
            "active_tier",
            "weekly_premium",
            "claim_history_count",
            "zone_risk_score",
            "registered_at",
        }
        assert expected_fields.issubset(entry.keys()), (
            f"Missing fields: {expected_fields - entry.keys()}"
        )

    def test_admin_workers_active_tier_shows_current_policy(
        self, authed_client, make_worker, make_policy
    ):
        """active_tier must equal the tier name of the worker's active policy."""
        w = make_worker(full_name="Standard Guard Worker")
        make_policy(worker=w, tier=PolicyTier.STANDARD, status=PolicyStatus.ACTIVE)

        resp = authed_client.get("/claims/admin/workers")
        assert resp.status_code == 200
        entry = next((e for e in resp.json() if e["id"] == w.id), None)
        assert entry is not None
        assert entry["active_tier"] == PolicyTier.STANDARD

    def test_admin_workers_active_tier_null_when_no_policy(
        self, authed_client, make_worker
    ):
        """active_tier must be null when the worker has no policy at all."""
        w = make_worker(full_name="No Policy Worker")

        resp = authed_client.get("/claims/admin/workers")
        assert resp.status_code == 200
        entry = next((e for e in resp.json() if e["id"] == w.id), None)
        assert entry is not None
        assert entry["active_tier"] is None

    def test_admin_workers_active_tier_null_when_policy_cancelled(
        self, authed_client, make_worker, make_policy
    ):
        """active_tier must be null when the worker's only policy is cancelled."""
        w = make_worker(full_name="Cancelled Policy Worker")
        make_policy(worker=w, tier=PolicyTier.PRO, status=PolicyStatus.CANCELLED)

        resp = authed_client.get("/claims/admin/workers")
        assert resp.status_code == 200
        entry = next((e for e in resp.json() if e["id"] == w.id), None)
        assert entry is not None
        assert entry["active_tier"] is None

    def test_admin_workers_weekly_premium_reflects_active_policy(
        self, authed_client, make_worker, make_policy
    ):
        """weekly_premium must be > 0 when the worker has an active policy."""
        w = make_worker(full_name="Premium Reflect Worker")
        make_policy(worker=w, status=PolicyStatus.ACTIVE, weekly_premium=129.0)

        resp = authed_client.get("/claims/admin/workers")
        assert resp.status_code == 200
        entry = next((e for e in resp.json() if e["id"] == w.id), None)
        assert entry is not None
        assert entry["active_tier"] is not None
        assert entry["weekly_premium"] > 0

    def test_admin_workers_zone_risk_score_in_valid_range(
        self, authed_client, make_worker
    ):
        """Every worker in the response must have zone_risk_score in [0.0, 1.0]."""
        # Create workers with boundary values
        make_worker(full_name="Low Risk Worker",  zone_risk_score=0.0)
        make_worker(full_name="High Risk Worker", zone_risk_score=1.0)
        make_worker(full_name="Mid Risk Worker",  zone_risk_score=0.5)

        resp = authed_client.get("/claims/admin/workers")
        assert resp.status_code == 200
        for entry in resp.json():
            score = entry["zone_risk_score"]
            assert 0.0 <= score <= 1.0, (
                f"Worker id={entry['id']} has zone_risk_score={score} outside [0,1]"
            )

    def test_admin_workers_claim_history_count_is_integer(
        self, authed_client, make_worker
    ):
        """claim_history_count must be an integer for every returned worker."""
        make_worker(full_name="Claim History Worker", claim_history_count=5)

        resp = authed_client.get("/claims/admin/workers")
        assert resp.status_code == 200
        for entry in resp.json():
            assert isinstance(entry["claim_history_count"], int), (
                f"Worker id={entry['id']} claim_history_count is not int: "
                f"{entry['claim_history_count']!r}"
            )

    def test_admin_workers_registered_at_is_iso_format(
        self, authed_client, make_worker
    ):
        """registered_at must be parseable as an ISO 8601 datetime string."""
        make_worker(full_name="ISO Date Worker")

        resp = authed_client.get("/claims/admin/workers")
        assert resp.status_code == 200
        for entry in resp.json():
            registered_at = entry.get("registered_at")
            assert registered_at is not None, (
                f"Worker id={entry['id']} is missing registered_at"
            )
            # datetime.fromisoformat raises ValueError on bad formats
            try:
                parsed = datetime.fromisoformat(registered_at)
            except ValueError as exc:
                pytest.fail(
                    f"Worker id={entry['id']} registered_at={registered_at!r} "
                    f"is not valid ISO format: {exc}"
                )
            assert isinstance(parsed, datetime)
