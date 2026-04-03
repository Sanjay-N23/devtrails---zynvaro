"""
Zynvaro Backend — Admin RBAC Tests
====================================
Verifies that admin-only endpoints reject non-admin workers with 403,
and that admin state-transition rules (approve/reject) are enforced.
"""

import pytest
from tests.conftest import worker_token
from models import ClaimStatus


# ─────────────────────────────────────────────────────────────────────
# Helper: build auth headers for a non-admin worker
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture()
def non_admin_headers(make_worker):
    """Return Authorization headers for a non-admin worker."""
    worker = make_worker(is_admin=False)
    token = worker_token(worker.id)
    return {"Authorization": f"Bearer {token}"}


# ─────────────────────────────────────────────────────────────────────
# 1-2. Non-admin access to admin claim listing endpoints → 403
# ─────────────────────────────────────────────────────────────────────

def test_non_admin_get_all_claims_403(client, non_admin_headers):
    resp = client.get("/claims/admin/all", headers=non_admin_headers)
    assert resp.status_code == 403


def test_non_admin_get_workers_403(client, non_admin_headers):
    resp = client.get("/claims/admin/workers", headers=non_admin_headers)
    assert resp.status_code == 403


# ─────────────────────────────────────────────────────────────────────
# 3-4. Non-admin approve / reject → 403
# ─────────────────────────────────────────────────────────────────────

def test_non_admin_approve_claim_403(client, non_admin_headers):
    resp = client.patch("/claims/999/approve", headers=non_admin_headers)
    assert resp.status_code == 403


def test_non_admin_reject_claim_403(client, non_admin_headers):
    resp = client.patch("/claims/999/reject", headers=non_admin_headers)
    assert resp.status_code == 403


# ─────────────────────────────────────────────────────────────────────
# 5-7. Non-admin access to analytics endpoints → 403
# ─────────────────────────────────────────────────────────────────────

def test_non_admin_analytics_weekly_403(client, non_admin_headers):
    resp = client.get("/analytics/weekly", headers=non_admin_headers)
    assert resp.status_code == 403


def test_non_admin_analytics_cities_403(client, non_admin_headers):
    resp = client.get("/analytics/cities", headers=non_admin_headers)
    assert resp.status_code == 403


def test_non_admin_analytics_time_series_403(client, non_admin_headers):
    resp = client.get("/analytics/time-series", headers=non_admin_headers)
    assert resp.status_code == 403


# ─────────────────────────────────────────────────────────────────────
# 8-9. Admin approve / reject on already-PAID claim → 400
# ─────────────────────────────────────────────────────────────────────

def test_admin_approve_paid_claim_400(
    authed_client, make_worker, make_policy, make_trigger, make_claim
):
    w = make_worker()
    p = make_policy(worker=w)
    t = make_trigger(city=w.city)
    c = make_claim(worker=w, policy=p, trigger=t, status=ClaimStatus.PAID)
    resp = authed_client.patch(f"/claims/{c.id}/approve")
    assert resp.status_code == 400


def test_admin_reject_paid_claim_400(
    authed_client, make_worker, make_policy, make_trigger, make_claim
):
    w = make_worker()
    p = make_policy(worker=w)
    t = make_trigger(city=w.city)
    c = make_claim(worker=w, policy=p, trigger=t, status=ClaimStatus.PAID)
    resp = authed_client.patch(f"/claims/{c.id}/reject")
    assert resp.status_code == 400


# ─────────────────────────────────────────────────────────────────────
# 10. Admin approve MANUAL_REVIEW claim → 200, status becomes paid
# ─────────────────────────────────────────────────────────────────────

def test_admin_approve_manual_review_claim_200(
    authed_client, make_worker, make_policy, make_trigger, make_claim
):
    w = make_worker()
    p = make_policy(worker=w)
    t = make_trigger(city=w.city)
    c = make_claim(worker=w, policy=p, trigger=t, status=ClaimStatus.MANUAL_REVIEW)
    resp = authed_client.patch(f"/claims/{c.id}/approve")
    assert resp.status_code == 200
    data = resp.json()
    assert data["status"] == ClaimStatus.PAID.value
