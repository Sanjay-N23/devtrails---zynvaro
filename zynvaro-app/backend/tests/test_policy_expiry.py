"""
Zynvaro Backend — Policy Auto-Expiry Tests
============================================
Tests lazy expiry via GET /policies/active, the expire_stale_policies()
helper, and downstream effects on claims, renewals, and cancellations
when a policy has passed its end_date.

7 cases:
    1. Past end_date policy returns null from GET /policies/active (lazy expiry)
    2. expire_stale_policies() transitions past-due ACTIVE policies to EXPIRED
    3. Active policy within end_date is NOT expired by expire_stale_policies()
    4. Expired policy worker does NOT receive claims when trigger fires
    5. Renew on expired policy returns error
    6. Cancel on expired policy returns error
    7. Multiple stale policies all expire in single expire_stale_policies() call
"""

import sys
import os

BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

from datetime import datetime, timedelta
from unittest.mock import patch
from sqlalchemy.orm import sessionmaker

from tests.conftest import worker_token
from models import Policy, PolicyStatus, Claim, Worker
from routers.policies import expire_stale_policies


# ===========================================================================
# 1. GET /policies/active returns null for past-end_date policy (lazy expiry)
# ===========================================================================

class TestLazyExpiryViaActiveEndpoint:

    def test_past_end_date_returns_null(self, client, test_db, make_worker, make_policy):
        """
        When a policy's end_date is in the past, GET /policies/active should
        trigger lazy expiry and return null (no active policy).
        """
        w = make_worker()
        policy = make_policy(worker=w)
        # Push end_date into the past
        policy.end_date = datetime.utcnow() - timedelta(days=1)
        test_db.commit()

        token = worker_token(w.id)
        resp = client.get("/policies/active", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 200
        assert resp.json() is None

        # Verify the policy was actually transitioned in the DB
        test_db.refresh(policy)
        assert policy.status == PolicyStatus.EXPIRED


# ===========================================================================
# 2. expire_stale_policies() transitions past-due ACTIVE policies to EXPIRED
# ===========================================================================

class TestExpireStaleTransition:

    def test_past_due_becomes_expired(self, test_db, make_worker, make_policy):
        """
        expire_stale_policies() should set status=EXPIRED on ACTIVE policies
        whose end_date is in the past.
        """
        w = make_worker()
        policy = make_policy(worker=w)
        policy.end_date = datetime.utcnow() - timedelta(days=1)
        test_db.commit()

        expire_stale_policies(test_db)

        test_db.refresh(policy)
        assert policy.status == PolicyStatus.EXPIRED


# ===========================================================================
# 3. Active policy within end_date is NOT expired
# ===========================================================================

class TestActiveWithinEndDateNotExpired:

    def test_valid_policy_untouched(self, test_db, make_worker, make_policy):
        """
        A policy whose end_date is in the future should remain ACTIVE after
        expire_stale_policies() runs.
        """
        w = make_worker()
        policy = make_policy(worker=w)
        # end_date defaults to 30 days in the future via make_policy
        assert policy.end_date > datetime.utcnow()

        expire_stale_policies(test_db)

        test_db.refresh(policy)
        assert policy.status == PolicyStatus.ACTIVE


# ===========================================================================
# 4. Expired policy worker does NOT get claims when trigger fires
# ===========================================================================

class TestExpiredPolicyNoClaims:

    def test_no_claims_for_expired_worker(
        self, client, test_db, test_engine, make_worker, make_policy, make_trigger
    ):
        """
        After a policy is expired, firing a trigger in that worker's city
        should NOT create any claims for the worker.
        """
        w = make_worker(city="Bangalore", is_admin=True)
        policy = make_policy(worker=w)
        # Expire the policy
        policy.end_date = datetime.utcnow() - timedelta(days=1)
        policy.status = PolicyStatus.EXPIRED
        test_db.commit()

        # Patch SessionLocal so _auto_generate_claims uses the test DB
        bound_factory = sessionmaker(
            autocommit=False, autoflush=False, bind=test_engine
        )

        with patch("database.SessionLocal", bound_factory):
            token = worker_token(w.id)
            resp = client.post(
                "/triggers/simulate",
                json={
                    "trigger_type": "Heavy Rainfall",
                    "city": "Bangalore",
                    "measured_value": 80.0,
                },
                headers={"Authorization": f"Bearer {token}"},
            )
            assert resp.status_code in (200, 201)

        # No claims should be created for the expired-policy worker
        claims = test_db.query(Claim).filter(Claim.worker_id == w.id).all()
        assert claims == []


# ===========================================================================
# 5. Renew on expired policy returns error (no active policy)
# ===========================================================================

class TestRenewExpiredPolicyFails:

    def test_renew_returns_404(self, client, test_db, make_worker, make_policy):
        """
        POST /policies/renew should return 404 when the worker's only
        policy has expired (no active policy to renew).
        """
        w = make_worker()
        policy = make_policy(worker=w)
        policy.end_date = datetime.utcnow() - timedelta(days=1)
        policy.status = PolicyStatus.EXPIRED
        test_db.commit()

        token = worker_token(w.id)
        resp = client.post("/policies/renew", headers={"Authorization": f"Bearer {token}"})
        assert resp.status_code == 404
        assert "No active policy" in resp.json()["detail"]


# ===========================================================================
# 6. Cancel on expired policy returns error
# ===========================================================================

class TestCancelExpiredPolicyFails:

    def test_cancel_returns_400(self, client, test_db, make_worker, make_policy):
        """
        DELETE /policies/{id} on an expired policy should return 400
        because the policy is not active.
        """
        w = make_worker()
        policy = make_policy(worker=w)
        policy.end_date = datetime.utcnow() - timedelta(days=1)
        policy.status = PolicyStatus.EXPIRED
        test_db.commit()

        token = worker_token(w.id)
        resp = client.delete(
            f"/policies/{policy.id}",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert resp.status_code == 400
        assert "not active" in resp.json()["detail"]


# ===========================================================================
# 7. Multiple stale policies all expire in single expire_stale_policies() call
# ===========================================================================

class TestBulkExpiry:

    def test_multiple_stale_all_expire(self, test_db, make_worker, make_policy):
        """
        expire_stale_policies() should transition ALL past-due ACTIVE
        policies to EXPIRED in one call — not just the first one found.
        """
        workers_and_policies = []
        for i in range(5):
            w = make_worker(full_name=f"Stale Worker {i}")
            p = make_policy(worker=w)
            p.end_date = datetime.utcnow() - timedelta(days=i + 1)
            workers_and_policies.append((w, p))
        test_db.commit()

        expire_stale_policies(test_db)

        for _w, p in workers_and_policies:
            test_db.refresh(p)
            assert p.status == PolicyStatus.EXPIRED, (
                f"Policy {p.policy_number} should be EXPIRED but is {p.status}"
            )
