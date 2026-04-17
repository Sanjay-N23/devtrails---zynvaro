"""
backend/tests/api/test_waiting_period_api.py
=============================================
API contract tests for waiting-period / cooling-off logic.

Covers spec section K — API / Backend Contract (items 146-158):
  - Policy endpoint returns cooling_off_active, claim_eligible_at
  - Worker does NOT see internal rule config/debug fields
  - Admin sees full continuity metadata
  - Null-safe serialization for renewal policies
  - Legacy policies without waiting fields handled gracefully
  - Server-side timestamps only

All tests use the authed_client fixture + in-memory SQLite.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from models import PolicyStatus


_NOW = datetime.utcnow()


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _create_policy(client, tier="Standard Guard"):
    resp = client.post("/policies/", json={"tier": tier})
    assert resp.status_code == 201, f"create_policy failed: {resp.text}"
    return resp.json()


def _make_worker_and_login(client):
    """Register a fresh worker and return authed client headers."""
    resp = client.post("/auth/register", json={
        "full_name": "Waiting Test Worker",
        "phone": "9111111110",
        "email": "waiting.test@zynvaro.test",
        "password": "testpassword123",
        "city": "Bangalore",
        "pincode": "560001",
        "platform": "Blinkit",
        "vehicle_type": "2-Wheeler",
        "shift": "Evening Peak (6PM-2AM)",
    })
    assert resp.status_code == 200
    token = resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────────────────────
# K.146 — Policy API returns claim_eligible_at and cooling_off fields
# ─────────────────────────────────────────────────────────────────────────────

class TestPolicyApiWaitingFields:

    def test_create_policy_returns_cooling_off_active_true(self, authed_client):
        """A brand-new policy should have cooling_off_active=True."""
        data = _create_policy(authed_client)
        assert "cooling_off_active" in data
        assert data["cooling_off_active"] is True

    def test_create_policy_returns_cooling_off_eligible_at(self, authed_client):
        """claim_eligible_at (as cooling_off_eligible_at) should be start + 24h."""
        data = _create_policy(authed_client)
        assert "cooling_off_eligible_at" in data
        assert data["cooling_off_eligible_at"] is not None

    def test_create_policy_cooling_off_hours_is_24(self, authed_client):
        data = _create_policy(authed_client)
        assert data.get("cooling_off_hours") == 24

    def test_create_policy_cooling_off_remaining_hours_is_near_24(self, authed_client):
        data = _create_policy(authed_client)
        remaining = data.get("cooling_off_remaining_hours")
        assert remaining is not None
        assert 23.0 <= remaining <= 24.0

    def test_active_policy_endpoint_includes_cooling_off_fields(self, authed_client):
        _create_policy(authed_client)
        resp = authed_client.get("/policies/active")
        assert resp.status_code == 200
        data = resp.json()
        assert "cooling_off_active" in data
        assert "cooling_off_eligible_at" in data
        assert "cooling_off_remaining_hours" in data

    def test_list_policies_each_has_cooling_off_fields(self, authed_client):
        _create_policy(authed_client)
        resp = authed_client.get("/policies/")
        assert resp.status_code == 200
        policies = resp.json()
        assert len(policies) >= 1
        for p in policies:
            assert "cooling_off_active" in p
            assert "cooling_off_eligible_at" in p

    def test_is_renewal_false_on_new_policy(self, authed_client):
        data = _create_policy(authed_client)
        assert data.get("is_renewal") is False

    def test_renew_policy_returns_cooling_off_active_false(self, authed_client, test_db):
        """Renewal immediately grants eligibility (is_renewal=True → 0h wait)."""
        # First create a policy
        _create_policy(authed_client)

        # Manually set it as if it was purchased 25h ago so it can be renewed
        from models import Policy
        policy = test_db.query(Policy).filter(
            Policy.status == PolicyStatus.ACTIVE
        ).first()
        if policy:
            policy.start_date = datetime.utcnow() - timedelta(hours=25)
            policy.end_date = datetime.utcnow() + timedelta(hours=1)
            test_db.commit()

        resp = authed_client.post("/policies/renew")
        if resp.status_code == 201:
            data = resp.json()
            assert data.get("is_renewal") is True
            assert data.get("cooling_off_active") is False

    def test_policy_api_returns_numeric_cooling_off_hours(self, authed_client):
        data = _create_policy(authed_client)
        hours = data.get("cooling_off_hours")
        assert isinstance(hours, (int, float))
        assert hours >= 0


# ─────────────────────────────────────────────────────────────────────────────
# K.149 — Worker does NOT see internal rule config / debug fields
# ─────────────────────────────────────────────────────────────────────────────

class TestWorkerApiFieldVisibility:

    def test_worker_does_not_see_waiting_rule_type_raw(self, authed_client):
        """waiting_rule_type is internal — policy response must not expose it."""
        data = _create_policy(authed_client)
        # These are internal service fields, not in PolicyResponse schema
        assert "waiting_rule_type" not in data
        assert "waiting_rule_version" not in data

    def test_worker_sees_worker_friendly_cooling_off_fields_only(self, authed_client):
        data = _create_policy(authed_client)
        # Only these user-friendly fields should be exposed
        assert "cooling_off_active" in data
        assert "cooling_off_eligible_at" in data
        assert "cooling_off_remaining_hours" in data

    def test_policy_response_has_no_debug_fraud_fields(self, authed_client):
        data = _create_policy(authed_client)
        internal_fields = [
            "previous_policy_id", "previous_policy_end",
            "fraud_flags", "ml_fraud_probability",
        ]
        for f in internal_fields:
            assert f not in data, f"Internal field {f!r} leaked to worker API"


# ─────────────────────────────────────────────────────────────────────────────
# K.151 — Null-safe serialization
# ─────────────────────────────────────────────────────────────────────────────

class TestNullSafeSerialization:

    def test_active_policy_returns_200_not_500_on_missing_nullable_fields(
        self, authed_client
    ):
        _create_policy(authed_client)
        resp = authed_client.get("/policies/active")
        assert resp.status_code == 200

    def test_active_policy_none_returns_null_not_500(self, authed_client):
        """Worker with no active policy should get null/200, not 500."""
        resp = authed_client.get("/policies/active")
        assert resp.status_code == 200
        # Either null body or a policy — must not crash
        assert resp.text is not None

    def test_list_policies_empty_returns_empty_list(self, authed_client):
        resp = authed_client.get("/policies/")
        assert resp.status_code == 200
        assert isinstance(resp.json(), list)


# ─────────────────────────────────────────────────────────────────────────────
# K.154–155 — Server-side timestamps only
# ─────────────────────────────────────────────────────────────────────────────

class TestServerSideTimestamps:

    def test_policy_start_date_is_server_set_iso8601(self, authed_client):
        data = _create_policy(authed_client)
        start = data.get("start_date")
        assert start is not None
        # Should be parseable ISO8601
        dt = datetime.fromisoformat(start.replace("Z", "+00:00"))
        # Must be recent (within last 60s) — compare as naive UTC
        now_naive = datetime.utcnow()
        dt_naive = dt.replace(tzinfo=None)
        assert abs((now_naive - dt_naive).total_seconds()) < 60

    def test_cooling_off_eligible_at_is_start_date_plus_cooling_hours(self, authed_client):
        data = _create_policy(authed_client)
        start = data["start_date"]
        eligible = data["cooling_off_eligible_at"]
        hours = data["cooling_off_hours"]
        start_dt = datetime.fromisoformat(start.replace("Z", ""))
        eligible_dt = datetime.fromisoformat(eligible.replace("Z", ""))
        diff_hours = (eligible_dt - start_dt).total_seconds() / 3600
        assert abs(diff_hours - hours) < 0.01

    def test_worker_cannot_set_start_date_via_api(self, authed_client):
        """Create policy request body has no start_date field — server sets it."""
        resp = authed_client.post(
            "/policies/",
            json={"tier": "Standard Guard", "start_date": "2020-01-01T00:00:00"},
        )
        # Either the server ignores start_date (extra fields ignored) or 422
        # It must NOT use 2020-01-01 as the start_date
        if resp.status_code == 201:
            data = resp.json()
            start = datetime.fromisoformat(data["start_date"].replace("Z", ""))
            # Must be recent, not 2020
            assert start.year >= 2026


# ─────────────────────────────────────────────────────────────────────────────
# K.156 — Batch claim generation respects waiting period per worker
# ─────────────────────────────────────────────────────────────────────────────

class TestBatchClaimGenWaitingRespect:

    def test_new_policy_gets_no_claim_from_batch_generation(
        self, authed_client, make_worker, make_policy, make_trigger, test_db
    ):
        """
        A 1h-old policy (within 24h wait) must get 0 claims from _auto_generate_claims.
        """
        from models import Claim
        from routers.triggers import _auto_generate_claims

        worker = make_worker(city="Bangalore")
        make_policy(
            worker=worker,
            status=PolicyStatus.ACTIVE,
            start_date=datetime.utcnow() - timedelta(hours=1),
        )
        trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")

        _auto_generate_claims(
            event_id=trigger.id,
            city="Bangalore",
            trigger_type="Heavy Rainfall",
            db=test_db,
            is_simulated=False,
            bypass_gate=False,
        )
        claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
        assert len(claims) == 0

    def test_old_policy_gets_claim_from_batch_generation(
        self, make_worker, make_policy, make_trigger, test_db
    ):
        """A 25h-old policy must get claims from _auto_generate_claims."""
        from models import Claim
        from routers.triggers import _auto_generate_claims

        worker = make_worker(city="Bangalore")
        make_policy(
            worker=worker,
            status=PolicyStatus.ACTIVE,
            start_date=datetime.utcnow() - timedelta(hours=25),
        )
        trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")

        _auto_generate_claims(
            event_id=trigger.id,
            city="Bangalore",
            trigger_type="Heavy Rainfall",
            db=test_db,
            is_simulated=False,
            bypass_gate=False,
        )
        claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
        assert len(claims) >= 1
