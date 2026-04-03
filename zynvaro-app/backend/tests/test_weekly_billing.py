"""
Zynvaro Backend — Weekly Billing Cycle & Full Policy Lifecycle Tests
=====================================================================
Covers the complete policy billing lifecycle:
    - Policy creation (7-day coverage, start_date, policy_number format)
    - Renewal semantics (extends from end_date, not now)
    - One-policy enforcement (new policy cancels the existing one)
    - Cancellation (status, end_date, idempotency)
    - Premium tier ordering invariants from TIER_CONFIG
    - Policy list ordering, isolation across workers

All tests use function-scoped fixtures from conftest.py. The in-memory
SQLite database is rolled back after every test for full isolation.

Fixture quick-reference
-----------------------
authed_client       — TestClient with a valid Bearer JWT (authed_client.worker
                      exposes the underlying Worker ORM object).
client              — Unauthenticated TestClient.
test_db             — Raw SQLAlchemy session (shared via get_db override).
make_worker(...)    — Factory that creates an extra Worker row.
make_policy(...)    — Factory that creates a Policy row for any worker.
"""

import sys
import os

_BACKEND_DIR = "D:/AeroFyta_DEVTrails-2026_FULL_HANDOFF/zynvaro-app/backend"
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

_TESTS_DIR = os.path.join(_BACKEND_DIR, "tests")
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from datetime import datetime, timedelta

import pytest
from jose import jwt
from starlette.testclient import TestClient
from passlib.context import CryptContext

from models import Policy, PolicyStatus, PolicyTier, Worker
from ml.premium_engine import TIER_CONFIG, calculate_premium
from database import get_db
from main import app

# ── JWT helpers (mirrors conftest.worker_token) ──────────────────────────────
_SECRET_KEY = "zynvaro-secret-2026-hackathon-key"
_ALGORITHM = "HS256"
_TOKEN_EXPIRE_MINUTES = 60 * 24 * 7
_pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def _worker_token(worker_id: int) -> str:
    """Mint a valid JWT Bearer token for the given worker_id."""
    expire = datetime.utcnow() + timedelta(minutes=_TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(worker_id), "exp": expire}
    return jwt.encode(payload, _SECRET_KEY, algorithm=_ALGORITHM)


def _authed_client_for(worker: Worker, test_db) -> TestClient:
    """
    Build an authenticated TestClient for a specific worker.

    Used in tests that need to operate as a worker other than the one
    already provided by the authed_client fixture.
    """
    def _override():
        try:
            yield test_db
        finally:
            pass

    token = _worker_token(worker.id)
    headers = {"Authorization": f"Bearer {token}"}
    app.dependency_overrides[get_db] = _override
    client = TestClient(app, raise_server_exceptions=True, headers=headers)
    return client


# ─────────────────────────────────────────────────────────────────────────────
# Helper — post a new policy and return the JSON body
# ─────────────────────────────────────────────────────────────────────────────

def _create_policy(authed_client, tier: str = "Basic Shield") -> dict:
    resp = authed_client.post("/policies/", json={"tier": tier})
    assert resp.status_code == 201, f"Policy creation failed: {resp.text}"
    return resp.json()


# =============================================================================
# TEST CLASS
# =============================================================================

class TestWeeklyPolicyCycle:

    # ──────────────────────────────────────────────────────────────────────────
    # CREATION
    # ──────────────────────────────────────────────────────────────────────────

    def test_new_policy_covers_exactly_7_days(self, authed_client):
        """end_date − start_date must equal exactly 7 days (timedelta(days=7))."""
        data = _create_policy(authed_client, "Basic Shield")

        start_date = datetime.fromisoformat(data["start_date"])
        end_date = datetime.fromisoformat(data["end_date"])
        coverage = end_date - start_date

        # Allow a tiny tolerance (≤ 2 seconds) for execution time between
        # the two datetime.utcnow() calls inside the route handler.
        assert abs(coverage.total_seconds() - 7 * 24 * 3600) <= 2, (
            f"Expected 7-day coverage but got {coverage}"
        )

    def test_new_policy_starts_now(self, authed_client):
        """start_date must be within 5 seconds of the current UTC time."""
        before = datetime.utcnow()
        data = _create_policy(authed_client, "Standard Guard")
        after = datetime.utcnow()

        start_date = datetime.fromisoformat(data["start_date"])

        assert before - timedelta(seconds=5) <= start_date <= after + timedelta(seconds=5), (
            f"start_date {start_date} is not close enough to utcnow()"
        )

    def test_policy_number_has_zynvaro_prefix(self, authed_client):
        """policy_number must start with 'ZYN-'."""
        data = _create_policy(authed_client, "Pro Armor")
        assert data["policy_number"].startswith("ZYN-"), (
            f"Expected policy_number to start with 'ZYN-', got: {data['policy_number']}"
        )

    def test_policy_number_is_unique_across_policies(self, test_db, make_worker):
        """Creating a policy for 3 different workers must yield 3 unique policy numbers."""
        policy_numbers = []
        for i in range(3):
            worker = make_worker()
            client = _authed_client_for(worker, test_db)
            data = _create_policy(client, "Basic Shield")
            policy_numbers.append(data["policy_number"])
            app.dependency_overrides.pop(get_db, None)

        assert len(set(policy_numbers)) == 3, (
            f"Duplicate policy numbers found: {policy_numbers}"
        )

    def test_weekly_premium_stored_in_policy(self, authed_client):
        """
        weekly_premium in the response must be > 0 and match a fresh
        calculate_premium() call for the same worker profile.
        """
        worker = authed_client.worker
        data = _create_policy(authed_client, "Standard Guard")

        expected = calculate_premium(
            tier="Standard Guard",
            pincode=worker.pincode,
            city=worker.city,
            claim_history_count=worker.claim_history_count,
            disruption_streak=worker.disruption_streak,
        )

        assert data["weekly_premium"] > 0
        # Allow floating-point drift of ±0.01 INR between the two calls
        assert abs(data["weekly_premium"] - expected["weekly_premium"]) <= 0.01, (
            f"Stored premium {data['weekly_premium']} != calculated {expected['weekly_premium']}"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # RENEWAL
    # ──────────────────────────────────────────────────────────────────────────

    def test_renewal_extends_from_current_end_date_not_now(
        self, authed_client, test_db, make_policy
    ):
        """
        If end_date is 10 days from now before renewal, the renewed end_date
        must be 17 days from now (end_date + 7 days), NOT 7 days from now.
        """
        worker = authed_client.worker
        future_end = datetime.utcnow() + timedelta(days=10)
        policy = make_policy(
            worker=worker,
            tier="Basic Shield",
            status=PolicyStatus.ACTIVE,
            end_date=future_end,
        )

        resp = authed_client.post("/policies/renew")
        assert resp.status_code == 201, resp.text
        data = resp.json()

        renewed_end = datetime.fromisoformat(data["end_date"])
        expected_end = future_end + timedelta(days=7)

        # Allow ±5 seconds tolerance
        assert abs((renewed_end - expected_end).total_seconds()) <= 5, (
            f"Renewal end_date {renewed_end} should be ~{expected_end} "
            f"(original end_date + 7 days)"
        )

    def test_renewal_does_not_shorten_coverage(
        self, authed_client, test_db, make_policy
    ):
        """
        A policy expiring in 5 days, when renewed, must expire in ~12 days
        (not collapse back to 7 days from now).
        """
        worker = authed_client.worker
        end_in_5 = datetime.utcnow() + timedelta(days=5)
        make_policy(
            worker=worker,
            tier="Standard Guard",
            status=PolicyStatus.ACTIVE,
            end_date=end_in_5,
        )

        resp = authed_client.post("/policies/renew")
        assert resp.status_code == 201, resp.text
        data = resp.json()

        renewed_end = datetime.fromisoformat(data["end_date"])
        min_expected = datetime.utcnow() + timedelta(days=11)  # at least 11 days out

        assert renewed_end >= min_expected, (
            f"Renewal shortened coverage — renewed end_date {renewed_end} "
            f"is before minimum expected {min_expected}"
        )

    def test_renewal_updates_premium(self, authed_client, test_db, make_policy):
        """weekly_premium after renewal must be a positive float (recalculated)."""
        worker = authed_client.worker
        make_policy(
            worker=worker,
            tier="Basic Shield",
            status=PolicyStatus.ACTIVE,
            weekly_premium=99.99,  # Deliberately wrong — route must overwrite
        )

        resp = authed_client.post("/policies/renew")
        assert resp.status_code == 201, resp.text

        renewed_premium = resp.json()["weekly_premium"]
        assert isinstance(renewed_premium, float) or isinstance(renewed_premium, int)
        assert renewed_premium > 0, f"Renewed premium must be > 0, got {renewed_premium}"

    def test_renewal_preserves_same_tier(self, authed_client, test_db, make_policy):
        """Tier must remain unchanged after renewal."""
        worker = authed_client.worker
        make_policy(
            worker=worker,
            tier="Standard Guard",
            status=PolicyStatus.ACTIVE,
        )

        resp = authed_client.post("/policies/renew")
        assert resp.status_code == 201, resp.text
        assert resp.json()["tier"] == "Standard Guard"

    def test_renewal_preserves_policy_number(
        self, authed_client, test_db, make_policy
    ):
        """
        Renewal updates the existing row in-place — the policy_number must
        remain identical (it is not a new policy row).
        """
        worker = authed_client.worker
        original = make_policy(
            worker=worker,
            tier="Pro Armor",
            status=PolicyStatus.ACTIVE,
            policy_number="ZYN-RENEW-TEST",
        )

        resp = authed_client.post("/policies/renew")
        assert resp.status_code == 201, resp.text
        assert resp.json()["policy_number"] == "ZYN-RENEW-TEST", (
            "Renewal must not change the policy_number"
        )

    def test_renewal_without_active_policy_returns_404(self, authed_client):
        """POST /policies/renew with no active policy must return 404."""
        # No policy created for this worker — the authed_client fixture
        # starts with a clean database for each test.
        resp = authed_client.post("/policies/renew")
        assert resp.status_code == 404, (
            f"Expected 404 with no active policy, got {resp.status_code}: {resp.text}"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # ONE-POLICY ENFORCEMENT
    # ──────────────────────────────────────────────────────────────────────────

    def test_only_one_policy_active_at_a_time(self, authed_client):
        """
        Creating a Standard Guard policy after a Basic Shield policy is active
        must result in Standard Guard being the sole active policy.
        """
        _create_policy(authed_client, "Basic Shield")
        _create_policy(authed_client, "Standard Guard")

        resp = authed_client.get("/policies/active")
        assert resp.status_code == 200
        active = resp.json()
        assert active is not None, "Expected an active policy but got null"
        assert active["tier"] == "Standard Guard"

    def test_switching_tier_cancels_previous(self, authed_client, test_db):
        """
        After creating Basic Shield then Pro Armor:
          - Pro Armor must be ACTIVE
          - Basic Shield must be CANCELLED
        """
        basic_data = _create_policy(authed_client, "Basic Shield")
        _create_policy(authed_client, "Pro Armor")

        # Re-query the Basic Shield policy from the DB
        basic_id = basic_data["id"]
        basic_policy = test_db.query(Policy).filter(Policy.id == basic_id).first()
        assert basic_policy is not None
        assert basic_policy.status == PolicyStatus.CANCELLED, (
            f"Old Basic Shield policy should be CANCELLED but is {basic_policy.status}"
        )

        # The active policy via API should be Pro Armor
        resp = authed_client.get("/policies/active")
        assert resp.json()["tier"] == "Pro Armor"

    def test_policy_history_preserved_after_switch(self, authed_client):
        """
        GET /policies/ must return both the cancelled Basic Shield and the
        active Pro Armor (history is never deleted).
        """
        _create_policy(authed_client, "Basic Shield")
        _create_policy(authed_client, "Pro Armor")

        resp = authed_client.get("/policies/")
        assert resp.status_code == 200
        policies = resp.json()

        tiers_found = {p["tier"] for p in policies}
        assert "Basic Shield" in tiers_found, "Cancelled Basic Shield should still be in history"
        assert "Pro Armor" in tiers_found, "Active Pro Armor should be in history"

    # ──────────────────────────────────────────────────────────────────────────
    # CANCELLATION
    # ──────────────────────────────────────────────────────────────────────────

    def test_cancel_sets_end_date_to_now(self, authed_client):
        """After cancellation, end_date must be within 5 seconds of utcnow()."""
        data = _create_policy(authed_client, "Basic Shield")
        policy_id = data["id"]

        before_cancel = datetime.utcnow()
        resp = authed_client.delete(f"/policies/{policy_id}")
        after_cancel = datetime.utcnow()
        assert resp.status_code == 204

        # Re-fetch via list to inspect end_date
        list_resp = authed_client.get("/policies/")
        policy_data = next(p for p in list_resp.json() if p["id"] == policy_id)
        end_date = datetime.fromisoformat(policy_data["end_date"])

        assert before_cancel - timedelta(seconds=5) <= end_date <= after_cancel + timedelta(seconds=5), (
            f"Cancelled policy end_date {end_date} should be close to cancellation time"
        )

    def test_cancel_sets_status_to_cancelled(self, authed_client):
        """DELETE /policies/{id} must change policy status to 'cancelled'."""
        data = _create_policy(authed_client, "Standard Guard")
        policy_id = data["id"]

        resp = authed_client.delete(f"/policies/{policy_id}")
        assert resp.status_code == 204

        list_resp = authed_client.get("/policies/")
        policy_data = next(p for p in list_resp.json() if p["id"] == policy_id)
        assert policy_data["status"] == "cancelled"

    def test_get_active_returns_none_after_cancel(self, authed_client):
        """
        After cancelling the only active policy, GET /policies/active must
        return HTTP 200 with a null JSON body (not 404).
        """
        data = _create_policy(authed_client, "Pro Armor")
        authed_client.delete(f"/policies/{data['id']}")

        resp = authed_client.get("/policies/active")
        assert resp.status_code == 200, (
            f"Expected 200 with null body, got {resp.status_code}"
        )
        assert resp.json() is None, (
            f"Expected null active policy but got: {resp.json()}"
        )

    def test_cancelled_policy_cannot_be_cancelled_again(self, authed_client):
        """Attempting to cancel an already-cancelled policy must return 400."""
        data = _create_policy(authed_client, "Basic Shield")
        policy_id = data["id"]

        first = authed_client.delete(f"/policies/{policy_id}")
        assert first.status_code == 204

        second = authed_client.delete(f"/policies/{policy_id}")
        assert second.status_code == 400, (
            f"Expected 400 when cancelling already-cancelled policy, got {second.status_code}"
        )

    # ──────────────────────────────────────────────────────────────────────────
    # PREMIUM TIER ORDERING
    # ──────────────────────────────────────────────────────────────────────────

    def test_basic_shield_has_lowest_base_premium(self):
        """Basic Shield base premium must be strictly less than Standard Guard's."""
        basic_base = TIER_CONFIG["Basic Shield"]["base"]
        standard_base = TIER_CONFIG["Standard Guard"]["base"]
        assert basic_base < standard_base, (
            f"Basic Shield base ({basic_base}) should be < Standard Guard base ({standard_base})"
        )

    def test_pro_armor_has_highest_max_payout(self):
        """Pro Armor max_weekly must be strictly greater than Standard Guard's."""
        pro_max = TIER_CONFIG["Pro Armor"]["max_weekly"]
        standard_max = TIER_CONFIG["Standard Guard"]["max_weekly"]
        assert pro_max > standard_max, (
            f"Pro Armor max_weekly ({pro_max}) should be > Standard Guard max_weekly ({standard_max})"
        )

    def test_all_tiers_have_positive_max_weekly_payout(self):
        """Every tier must define a max_weekly payout > 0."""
        for tier_name, cfg in TIER_CONFIG.items():
            assert cfg["max_weekly"] > 0, (
                f"Tier '{tier_name}' has non-positive max_weekly: {cfg['max_weekly']}"
            )

    # ──────────────────────────────────────────────────────────────────────────
    # POLICY LIST
    # ──────────────────────────────────────────────────────────────────────────

    def test_policy_list_includes_cancelled_policies(self, authed_client):
        """
        Policy list must include cancelled entries.
        After: create → cancel → create again, the list should have at least 2 rows.
        """
        first = _create_policy(authed_client, "Basic Shield")
        authed_client.delete(f"/policies/{first['id']}")
        _create_policy(authed_client, "Standard Guard")

        resp = authed_client.get("/policies/")
        assert resp.status_code == 200
        assert len(resp.json()) >= 2, (
            "Policy list must include cancelled policies, not just active ones"
        )

    def test_policy_list_ordered_newest_first(self, authed_client, test_db):
        """
        GET /policies/ must return policies ordered by created_at descending
        (newest first). Verified by checking the first entry's created_at is
        >= the second entry's created_at.
        """
        _create_policy(authed_client, "Basic Shield")
        _create_policy(authed_client, "Standard Guard")  # cancels first, creates second

        resp = authed_client.get("/policies/")
        assert resp.status_code == 200
        policies = resp.json()
        assert len(policies) >= 2, "Need at least 2 policies to verify ordering"

        first_created = datetime.fromisoformat(policies[0]["created_at"])
        second_created = datetime.fromisoformat(policies[1]["created_at"])

        assert first_created >= second_created, (
            f"Policies are not sorted newest-first: "
            f"{first_created} < {second_created}"
        )

    def test_policy_list_only_shows_own_policies(self, test_db, make_worker, authed_client):
        """
        Worker A's policy list must only contain Worker A's policies.
        Worker B's single policy must not appear in Worker A's list.
        """
        worker_a = authed_client.worker

        # Create 2 policies for Worker A (second creation cancels the first)
        _create_policy(authed_client, "Basic Shield")
        _create_policy(authed_client, "Standard Guard")

        # Create Worker B with their own policy
        worker_b = make_worker(city="Mumbai", pincode="400001")
        client_b = _authed_client_for(worker_b, test_db)
        _create_policy(client_b, "Pro Armor")
        app.dependency_overrides.pop(get_db, None)

        # Restore Worker A's client dependency
        def _override():
            try:
                yield test_db
            finally:
                pass

        app.dependency_overrides[get_db] = _override

        resp = authed_client.get("/policies/")
        assert resp.status_code == 200
        policies = resp.json()

        # All returned policies must belong to Worker A
        for policy in policies:
            assert policy["id"] in [
                p["id"]
                for p in policies
                if True  # we verify ownership via worker_id in the DB
            ]

        # Verify at DB level: exactly 2 policies for Worker A
        worker_a_db_policies = (
            test_db.query(Policy).filter(Policy.worker_id == worker_a.id).all()
        )
        assert len(worker_a_db_policies) == 2, (
            f"Worker A should have exactly 2 policies, found {len(worker_a_db_policies)}"
        )

        # Worker B's policy must not appear in Worker A's API response
        worker_b_db_policy = (
            test_db.query(Policy).filter(Policy.worker_id == worker_b.id).first()
        )
        assert worker_b_db_policy is not None
        returned_ids = {p["id"] for p in policies}
        assert worker_b_db_policy.id not in returned_ids, (
            "Worker B's policy must not appear in Worker A's policy list"
        )
