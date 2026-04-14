"""
Zynvaro Backend — Integration tests for /policies/ endpoints
=============================================================
Tests cover:
    POST   /policies/           — create policy
    POST   /policies/renew      — renew active policy
    GET    /policies/active     — fetch active policy
    GET    /policies/quote/all  — all-tier premium quotes
    DELETE /policies/{id}       — cancel policy

Each test function receives function-scoped fixtures from conftest.py,
which roll back the in-memory SQLite database after every test.

Fixture quick-reference
-----------------------
authed_client       — TestClient with a valid Bearer JWT pre-attached.
                      The underlying worker is available as
                      ``authed_client.worker``.
client              — Unauthenticated TestClient (used for auth-required
                      negative tests).
test_db             — The raw SQLAlchemy session (shared with both clients
                      through the get_db dependency override).
make_worker(...)    — Factory that inserts an extra Worker row.
make_policy(...)    — Factory that inserts a Policy row for any worker.
"""

import sys
import os

# Ensure the backend package root is importable when pytest is invoked
# from outside the backend directory (e.g. from the repo root).
_BACKEND_DIR = "D:/AeroFyta_DEVTrails-2026_FULL_HANDOFF/zynvaro-app/backend"
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

# Also add the tests directory so conftest helpers are importable as a module
_TESTS_DIR = os.path.join(_BACKEND_DIR, "tests")
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

from datetime import datetime, timedelta

import pytest
from jose import jwt
from models import Policy, PolicyStatus, Worker
from passlib.context import CryptContext

# ── Re-use the same JWT helper defined in conftest so tokens are always
#    generated with matching SECRET_KEY / algorithm. ──────────────────
SECRET_KEY = "zynvaro-secret-2026-hackathon-key"
ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 60 * 24 * 7


def worker_token(worker_id: int) -> str:
    """Mint a valid JWT for worker_id (mirrors conftest.worker_token)."""
    expire = datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(worker_id), "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)

# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

ALL_TIERS = ["Basic Shield", "Standard Guard", "Pro Armor"]

_pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


def _create_second_authed_client(test_db, app, get_db):
    """
    Build a second authenticated client backed by the *same* test_db session.
    Used for tests that need to verify cross-worker policy isolation.
    Returns (TestClient, Worker).
    """
    from starlette.testclient import TestClient

    hashed_pw = _pwd_context.hash("testpassword123")
    other_worker = Worker(
        full_name="Other Worker",
        phone="9111111111",
        email="other.worker@zynvaro.test",
        password_hash=hashed_pw,
        city="Mumbai",
        pincode="400001",
        platform="Zepto",
        vehicle_type="2-Wheeler",
        shift="Evening Peak (6PM-2AM)",
        zone_risk_score=0.85,
        claim_history_count=0,
        disruption_streak=0,
        is_active=True,
    )
    test_db.add(other_worker)
    test_db.commit()
    test_db.refresh(other_worker)

    token = worker_token(other_worker.id)
    headers = {"Authorization": f"Bearer {token}"}

    def _override():
        try:
            yield test_db
        finally:
            pass

    app.dependency_overrides[get_db] = _override
    c = TestClient(app, raise_server_exceptions=True, headers=headers)
    c.worker = other_worker  # type: ignore[attr-defined]
    return c, other_worker


# ═════════════════════════════════════════════════════════════════
# CREATE POLICY
# ═════════════════════════════════════════════════════════════════


def test_create_policy_basic_shield_returns_201(authed_client):
    resp = authed_client.post("/policies/", json={"tier": "Basic Shield"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["tier"] == "Basic Shield"
    assert data["status"] == "active"


def test_create_policy_standard_guard_returns_201(authed_client):
    resp = authed_client.post("/policies/", json={"tier": "Standard Guard"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["tier"] == "Standard Guard"
    assert data["status"] == "active"


def test_create_policy_pro_armor_returns_201(authed_client):
    resp = authed_client.post("/policies/", json={"tier": "Pro Armor"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["tier"] == "Pro Armor"
    assert data["status"] == "active"


def test_create_policy_sets_7_day_window(authed_client):
    """end_date must be approximately start_date + 7 days (allow ±5 s clock drift)."""
    before = datetime.utcnow()
    resp = authed_client.post("/policies/", json={"tier": "Basic Shield"})
    after = datetime.utcnow()

    assert resp.status_code == 201
    data = resp.json()

    start = datetime.fromisoformat(data["start_date"].replace("Z", ""))
    end = datetime.fromisoformat(data["end_date"].replace("Z", ""))

    # start_date should be very close to now
    assert before - timedelta(seconds=5) <= start <= after + timedelta(seconds=5)

    delta = end - start
    # Allow a 10-second tolerance around exactly 7 days
    assert abs(delta.total_seconds() - 7 * 86400) < 10, (
        f"Expected ~7-day window, got {delta}"
    )


def test_create_policy_calculates_premium_not_zero(authed_client):
    """weekly_premium must be a positive number calculated by the engine."""
    resp = authed_client.post("/policies/", json={"tier": "Basic Shield"})
    assert resp.status_code == 201
    assert resp.json()["weekly_premium"] > 0


def test_create_policy_invalid_tier_returns_400(authed_client):
    resp = authed_client.post("/policies/", json={"tier": "Gold Plus"})
    assert resp.status_code == 400


def test_create_policy_invalid_tier_error_lists_valid_tiers(authed_client):
    """Error detail must mention every valid tier so the client knows what to pass."""
    resp = authed_client.post("/policies/", json={"tier": "Platinum"})
    assert resp.status_code == 400
    detail = resp.json()["detail"]
    for tier in ALL_TIERS:
        assert tier in detail, f"Valid tier '{tier}' missing from error: {detail}"


def test_create_policy_cancels_existing_active_policy(authed_client, test_db):
    """Creating a new policy must set the previous active policy to CANCELLED."""
    # Create first policy
    r1 = authed_client.post("/policies/", json={"tier": "Basic Shield"})
    assert r1.status_code == 201
    first_id = r1.json()["id"]

    # Create second policy — should auto-cancel the first
    r2 = authed_client.post("/policies/", json={"tier": "Standard Guard"})
    assert r2.status_code == 201

    # Verify the first policy is now CANCELLED in the DB
    first_policy = test_db.query(Policy).filter(Policy.id == first_id).first()
    assert first_policy is not None
    assert first_policy.status == PolicyStatus.CANCELLED


def test_create_policy_only_one_active_at_a_time(authed_client, test_db):
    """After creating two policies back-to-back, only the latest must be ACTIVE."""
    authed_client.post("/policies/", json={"tier": "Basic Shield"})
    authed_client.post("/policies/", json={"tier": "Pro Armor"})

    worker_id = authed_client.worker.id
    active_count = (
        test_db.query(Policy)
        .filter(
            Policy.worker_id == worker_id,
            Policy.status == PolicyStatus.ACTIVE,
        )
        .count()
    )
    assert active_count == 1


def test_create_policy_requires_auth(client):
    """POST /policies/ without a token must return 401."""
    resp = client.post("/policies/", json={"tier": "Basic Shield"})
    assert resp.status_code == 401


# ═════════════════════════════════════════════════════════════════
# RENEW POLICY
# ═════════════════════════════════════════════════════════════════


def test_renew_policy_extends_end_date_by_7_days(authed_client):
    """Renewing must extend end_date by exactly 7 days."""
    # Create a fresh policy
    create_resp = authed_client.post("/policies/", json={"tier": "Basic Shield"})
    assert create_resp.status_code == 201
    original_end = datetime.fromisoformat(
        create_resp.json()["end_date"].replace("Z", "")
    )

    renew_resp = authed_client.post("/policies/renew")
    assert renew_resp.status_code == 201
    new_end = datetime.fromisoformat(renew_resp.json()["end_date"].replace("Z", ""))

    delta = new_end - original_end
    assert abs(delta.total_seconds() - 7 * 86400) < 10, (
        f"Expected +7 days from original end, got {delta}"
    )


def test_renew_policy_extends_from_current_end_not_now(authed_client, test_db):
    """
    The 7-day extension starts from the policy's current end_date, not from
    datetime.utcnow() — this prevents coverage gaps when policies lapse briefly.
    """
    create_resp = authed_client.post("/policies/", json={"tier": "Basic Shield"})
    assert create_resp.status_code == 201
    policy_id = create_resp.json()["id"]

    # Push end_date 10 days into the future (simulates a policy renewed early)
    future_end = datetime.utcnow() + timedelta(days=10)
    policy = test_db.query(Policy).filter(Policy.id == policy_id).first()
    policy.end_date = future_end
    test_db.commit()
    test_db.refresh(policy)

    renew_resp = authed_client.post("/policies/renew")
    assert renew_resp.status_code == 201
    new_end = datetime.fromisoformat(renew_resp.json()["end_date"].replace("Z", ""))

    expected_end = future_end + timedelta(days=7)
    # Allow 10-second tolerance for DB round-trip and microsecond precision
    assert abs((new_end - expected_end).total_seconds()) < 10, (
        f"Renewal should extend from future_end ({future_end}), "
        f"but got {new_end} (expected ~{expected_end})"
    )


def test_renew_policy_recalculates_premium(authed_client, test_db):
    """
    After renewal the weekly_premium field must reflect a freshly computed value
    (not necessarily changed, but the field must be populated and positive).
    """
    authed_client.post("/policies/", json={"tier": "Standard Guard"})
    renew_resp = authed_client.post("/policies/renew")
    assert renew_resp.status_code == 201
    assert renew_resp.json()["weekly_premium"] > 0


def test_renew_no_active_policy_returns_404(authed_client):
    """POST /policies/renew with no active policy must return 404."""
    # No policy created — should fail immediately
    resp = authed_client.post("/policies/renew")
    assert resp.status_code == 404


def test_renew_policy_requires_auth(client):
    """POST /policies/renew without a token must return 401."""
    resp = client.post("/policies/renew")
    assert resp.status_code == 401


# ═════════════════════════════════════════════════════════════════
# GET ACTIVE POLICY
# ═════════════════════════════════════════════════════════════════


def test_get_active_policy_returns_null_when_none(authed_client):
    """GET /policies/active with no policy must return 200 with null body."""
    resp = authed_client.get("/policies/active")
    assert resp.status_code == 200
    assert resp.json() is None


def test_get_active_policy_returns_policy_when_active(authed_client):
    """After creating a policy, GET /policies/active must return it."""
    create_resp = authed_client.post("/policies/", json={"tier": "Pro Armor"})
    assert create_resp.status_code == 201
    created_id = create_resp.json()["id"]

    active_resp = authed_client.get("/policies/active")
    assert active_resp.status_code == 200
    data = active_resp.json()
    assert data is not None
    assert data["id"] == created_id
    assert data["status"] == "active"
    assert data["tier"] == "Pro Armor"


def test_get_active_policy_requires_auth(client):
    """GET /policies/active without a token must return 401."""
    resp = client.get("/policies/active")
    assert resp.status_code == 401


# ═════════════════════════════════════════════════════════════════
# ALL-TIER QUOTES
# ═════════════════════════════════════════════════════════════════


def test_quote_all_returns_three_tiers(authed_client):
    resp = authed_client.get("/policies/quote/all")
    assert resp.status_code == 200
    data = resp.json()
    assert "tiers" in data
    assert len(data["tiers"]) == 3


def test_quote_all_tiers_have_correct_names(authed_client):
    """Each tier quote must carry its canonical name string."""
    resp = authed_client.get("/policies/quote/all")
    assert resp.status_code == 200
    returned_names = {t["tier"] for t in resp.json()["tiers"]}
    assert returned_names == set(ALL_TIERS)


def test_quote_premium_is_positive_for_all_tiers(authed_client):
    resp = authed_client.get("/policies/quote/all")
    assert resp.status_code == 200
    for tier_quote in resp.json()["tiers"]:
        assert tier_quote["weekly_premium"] > 0, (
            f"weekly_premium is zero/negative for {tier_quote['tier']}"
        )


def test_quote_basic_shield_cheapest(authed_client):
    """Basic Shield must have a lower or equal weekly_premium than Standard Guard."""
    resp = authed_client.get("/policies/quote/all")
    assert resp.status_code == 200
    premiums = {t["tier"]: t["weekly_premium"] for t in resp.json()["tiers"]}
    assert premiums["Basic Shield"] <= premiums["Standard Guard"], (
        f"Basic Shield ({premiums['Basic Shield']}) should be ≤ "
        f"Standard Guard ({premiums['Standard Guard']})"
    )


def test_quote_pro_armor_most_expensive(authed_client):
    """Pro Armor must have a higher or equal weekly_premium than Standard Guard."""
    resp = authed_client.get("/policies/quote/all")
    assert resp.status_code == 200
    premiums = {t["tier"]: t["weekly_premium"] for t in resp.json()["tiers"]}
    assert premiums["Pro Armor"] >= premiums["Standard Guard"], (
        f"Pro Armor ({premiums['Pro Armor']}) should be ≥ "
        f"Standard Guard ({premiums['Standard Guard']})"
    )


def test_quote_includes_breakdown_with_zone_risk(authed_client):
    """Each tier quote must include a breakdown dict containing zone_risk_score."""
    resp = authed_client.get("/policies/quote/all")
    assert resp.status_code == 200
    for tier_quote in resp.json()["tiers"]:
        assert "breakdown" in tier_quote, (
            f"'breakdown' key missing from {tier_quote['tier']} quote"
        )
        assert "zone_risk_score" in tier_quote["breakdown"], (
            f"'zone_risk_score' missing from breakdown of {tier_quote['tier']}"
        )


def test_quote_requires_auth(client):
    """GET /policies/quote/all without a token must return 401."""
    resp = client.get("/policies/quote/all")
    assert resp.status_code == 401


# ═════════════════════════════════════════════════════════════════
# CANCEL POLICY
# ═════════════════════════════════════════════════════════════════


def test_cancel_policy_returns_204(authed_client):
    """DELETE /policies/{id} on an active policy must return 204 with no body."""
    create_resp = authed_client.post("/policies/", json={"tier": "Basic Shield"})
    policy_id = create_resp.json()["id"]

    cancel_resp = authed_client.delete(f"/policies/{policy_id}")
    assert cancel_resp.status_code == 204
    # 204 responses must have no body
    assert cancel_resp.content == b""


def test_cancel_policy_sets_status_cancelled(authed_client, test_db):
    """After a successful DELETE, the policy row in the DB must be CANCELLED."""
    create_resp = authed_client.post("/policies/", json={"tier": "Standard Guard"})
    policy_id = create_resp.json()["id"]

    authed_client.delete(f"/policies/{policy_id}")

    test_db.expire_all()  # force re-read from DB
    policy = test_db.query(Policy).filter(Policy.id == policy_id).first()
    assert policy is not None
    assert policy.status == PolicyStatus.CANCELLED


def test_cancel_nonexistent_policy_returns_404(authed_client):
    """Deleting a policy ID that does not exist must return 404."""
    resp = authed_client.delete("/policies/999999")
    assert resp.status_code == 404


def test_cancel_other_workers_policy_returns_404(authed_client, test_db):
    """
    A worker must not be able to cancel another worker's policy.
    The endpoint filters by both policy ID *and* worker ID, so a mismatch
    must surface as 404 (not 403) — leaking nothing about the policy's
    existence to the requesting worker.
    """
    from database import get_db
    from main import app

    # Build a second worker + client sharing the same test_db
    other_client, other_worker = _create_second_authed_client(test_db, app, get_db)

    # Other worker creates their own policy
    other_create = other_client.post("/policies/", json={"tier": "Basic Shield"})
    assert other_create.status_code == 201
    other_policy_id = other_create.json()["id"]

    # Original authed_client tries to cancel the other worker's policy
    resp = authed_client.delete(f"/policies/{other_policy_id}")
    assert resp.status_code == 404


def test_cancel_already_cancelled_returns_400(authed_client):
    """Attempting to cancel a policy that is already CANCELLED must return 400."""
    create_resp = authed_client.post("/policies/", json={"tier": "Pro Armor"})
    policy_id = create_resp.json()["id"]

    # First cancel — should succeed
    first_cancel = authed_client.delete(f"/policies/{policy_id}")
    assert first_cancel.status_code == 204

    # Second cancel — must be rejected
    second_cancel = authed_client.delete(f"/policies/{policy_id}")
    assert second_cancel.status_code == 400


def test_cancel_requires_auth(client, authed_client):
    """DELETE /policies/{id} without a token must return 401."""
    # Create a real policy via the authed client so we have a valid ID
    create_resp = authed_client.post("/policies/", json={"tier": "Basic Shield"})
    policy_id = create_resp.json()["id"]

    # Attempt to delete without auth
    resp = client.delete(f"/policies/{policy_id}")
    assert resp.status_code == 401


# ─── Razorpay Checkout Flow Tests (Phase 3: Premium Payment Gateway) ──

class TestRazorpayCreateOrder:
    def test_create_order_returns_200(self, authed_client):
        resp = authed_client.post("/policies/create-order", json={"tier": "Basic Shield"})
        assert resp.status_code == 200

    def test_create_order_has_all_fields(self, authed_client):
        resp = authed_client.post("/policies/create-order", json={"tier": "Basic Shield"})
        data = resp.json()
        for key in ["order_id", "amount", "currency", "key_id", "tier", "weekly_premium"]:
            assert key in data, f"Missing: {key}"

    def test_create_order_amount_in_paise(self, authed_client):
        resp = authed_client.post("/policies/create-order", json={"tier": "Basic Shield"})
        data = resp.json()
        assert data["amount"] == int(data["weekly_premium"] * 100)

    def test_create_order_mock_when_no_keys(self, authed_client):
        # No RAZORPAY_KEY_ID in test env → returns MOCK_ORDER
        resp = authed_client.post("/policies/create-order", json={"tier": "Basic Shield"})
        assert resp.json()["order_id"] == "MOCK_ORDER"

    def test_create_order_invalid_tier(self, authed_client):
        resp = authed_client.post("/policies/create-order", json={"tier": "Invalid Tier"})
        assert resp.status_code == 400

    def test_create_order_unauthorized(self, client):
        resp = client.post("/policies/create-order", json={"tier": "Basic Shield"})
        assert resp.status_code == 401


class TestRazorpayVerifyPayment:
    def test_verify_payment_in_mock_mode_activates_policy(self, authed_client):
        # In mock mode (no keys), signature verification auto-passes
        resp = authed_client.post("/policies/verify-payment", json={
            "razorpay_payment_id": "pay_MOCK123",
            "razorpay_order_id": "MOCK_ORDER",
            "razorpay_signature": "mock_signature",
            "tier": "Basic Shield",
        })
        assert resp.status_code == 201
        assert resp.json()["status"] == "active"
        assert resp.json()["tier"] == "Basic Shield"

    def test_verify_payment_invalid_tier(self, authed_client):
        resp = authed_client.post("/policies/verify-payment", json={
            "razorpay_payment_id": "pay_MOCK", "razorpay_order_id": "ord_MOCK",
            "razorpay_signature": "sig_MOCK", "tier": "Invalid Tier",
        })
        assert resp.status_code == 400

    def test_verify_payment_unauthorized(self, client):
        resp = client.post("/policies/verify-payment", json={
            "razorpay_payment_id": "p", "razorpay_order_id": "o",
            "razorpay_signature": "s", "tier": "Basic Shield",
        })
        assert resp.status_code == 401

    def test_verify_payment_creates_transaction_record(self, authed_client, test_db):
        from models import PayoutTransaction, TransactionType
        before = test_db.query(PayoutTransaction).count()
        authed_client.post("/policies/verify-payment", json={
            "razorpay_payment_id": "pay_TEST123",
            "razorpay_order_id": "order_TEST123",
            "razorpay_signature": "sig_TEST",
            "tier": "Standard Guard",
        })
        test_db.commit()
        after = test_db.query(PayoutTransaction).count()
        assert after == before + 1
        txn = test_db.query(PayoutTransaction).order_by(PayoutTransaction.id.desc()).first()
        assert txn.transaction_type == TransactionType.PREMIUM_PAYMENT
        assert txn.razorpay_payment_id == "pay_TEST123"


class TestRazorpayRenewFlow:
    def test_renew_order_requires_active_policy(self, authed_client):
        resp = authed_client.post("/policies/renew-order")
        assert resp.status_code == 404  # No active policy

    def test_renew_order_with_active_policy(self, authed_client):
        authed_client.post("/policies/", json={"tier": "Basic Shield"})
        resp = authed_client.post("/policies/renew-order")
        assert resp.status_code == 200
        assert "order_id" in resp.json()

    def test_verify_renewal_extends_policy(self, authed_client):
        authed_client.post("/policies/", json={"tier": "Basic Shield"})
        orig = authed_client.get("/policies/active").json()
        orig_end = orig["end_date"]
        resp = authed_client.post("/policies/verify-renewal", json={
            "razorpay_payment_id": "pay_RENEW", "razorpay_order_id": "order_RENEW",
            "razorpay_signature": "sig", "tier": "Basic Shield",
        })
        assert resp.status_code == 201
        new_end = resp.json()["end_date"]
        assert new_end > orig_end  # Extended


class TestAdminTransactionsEndpoint:
    def test_admin_transactions_200(self, authed_client):
        resp = authed_client.get("/claims/admin/transactions")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)

    def test_admin_transactions_unauthorized(self, client):
        assert client.get("/claims/admin/transactions").status_code == 401

    def test_admin_transactions_shows_premium_payment(self, authed_client):
        authed_client.post("/policies/verify-payment", json={
            "razorpay_payment_id": "pay_AUDIT",
            "razorpay_order_id": "order_AUDIT",
            "razorpay_signature": "sig", "tier": "Basic Shield",
        })
        resp = authed_client.get("/claims/admin/transactions")
        txns = resp.json()
        premium_txns = [t for t in txns if t["transaction_type"] == "premium_payment"]
        assert len(premium_txns) >= 1
        assert premium_txns[0]["razorpay_payment_id"] == "pay_AUDIT"
