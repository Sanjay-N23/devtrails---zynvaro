"""
Tests for admin claim approve / reject endpoints.

Covers:
  1. Approve PENDING_REVIEW  -> paid, paid_at set
  2. Approve MANUAL_REVIEW   -> paid
  3. Reject  PENDING_REVIEW  -> rejected
  4. Reject  MANUAL_REVIEW   -> rejected
  5. Approve already PAID    -> 400
  6. Reject already REJECTED -> 400
  7. Approve non-existent ID -> 404
  8. Approved claim has payment_ref starting with "MANUAL-UPI-"
"""

from models import ClaimStatus


# ── 1. Approve PENDING_REVIEW ──────────────────────────────────────

def test_approve_pending_review(authed_client, make_worker, make_policy, make_trigger, make_claim):
    w = make_worker()
    p = make_policy(worker=w)
    t = make_trigger()
    c = make_claim(worker=w, policy=p, trigger=t, status=ClaimStatus.PENDING_REVIEW, paid_at=None)

    resp = authed_client.patch(f"/claims/{c.id}/approve")
    assert resp.status_code == 200

    body = resp.json()
    assert body["status"] == "paid"
    assert body["paid_at"] is not None


# ── 2. Approve MANUAL_REVIEW ──────────────────────────────────────

def test_approve_manual_review(authed_client, make_worker, make_policy, make_trigger, make_claim):
    w = make_worker()
    p = make_policy(worker=w)
    t = make_trigger()
    c = make_claim(worker=w, policy=p, trigger=t, status=ClaimStatus.MANUAL_REVIEW, paid_at=None)

    resp = authed_client.patch(f"/claims/{c.id}/approve")
    assert resp.status_code == 200
    assert resp.json()["status"] == "paid"


# ── 3. Reject PENDING_REVIEW ──────────────────────────────────────

def test_reject_pending_review(authed_client, make_worker, make_policy, make_trigger, make_claim):
    w = make_worker()
    p = make_policy(worker=w)
    t = make_trigger()
    c = make_claim(worker=w, policy=p, trigger=t, status=ClaimStatus.PENDING_REVIEW, paid_at=None)

    resp = authed_client.patch(f"/claims/{c.id}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


# ── 4. Reject MANUAL_REVIEW ──────────────────────────────────────

def test_reject_manual_review(authed_client, make_worker, make_policy, make_trigger, make_claim):
    w = make_worker()
    p = make_policy(worker=w)
    t = make_trigger()
    c = make_claim(worker=w, policy=p, trigger=t, status=ClaimStatus.MANUAL_REVIEW, paid_at=None)

    resp = authed_client.patch(f"/claims/{c.id}/reject")
    assert resp.status_code == 200
    assert resp.json()["status"] == "rejected"


# ── 5. Approve already PAID → 400 ─────────────────────────────────

def test_approve_already_paid_returns_400(authed_client, make_worker, make_policy, make_trigger, make_claim):
    w = make_worker()
    p = make_policy(worker=w)
    t = make_trigger()
    c = make_claim(worker=w, policy=p, trigger=t, status=ClaimStatus.PAID)

    resp = authed_client.patch(f"/claims/{c.id}/approve")
    assert resp.status_code == 400


# ── 6. Reject already REJECTED → 400 ──────────────────────────────

def test_reject_already_rejected_returns_400(authed_client, make_worker, make_policy, make_trigger, make_claim):
    w = make_worker()
    p = make_policy(worker=w)
    t = make_trigger()
    c = make_claim(worker=w, policy=p, trigger=t, status=ClaimStatus.REJECTED)

    resp = authed_client.patch(f"/claims/{c.id}/reject")
    assert resp.status_code == 400


# ── 7. Approve non-existent claim → 404 ───────────────────────────

def test_approve_nonexistent_claim_returns_404(authed_client):
    resp = authed_client.patch("/claims/99999/approve")
    assert resp.status_code == 404


# ── 8. Approved claim payment_ref starts with "MANUAL-UPI-" ───────

def test_approved_claim_has_manual_upi_payment_ref(authed_client, make_worker, make_policy, make_trigger, make_claim):
    w = make_worker()
    p = make_policy(worker=w)
    t = make_trigger()
    c = make_claim(worker=w, policy=p, trigger=t, status=ClaimStatus.PENDING_REVIEW, paid_at=None)

    resp = authed_client.patch(f"/claims/{c.id}/approve")
    assert resp.status_code == 200

    body = resp.json()
    assert body["payment_ref"].startswith("MANUAL-UPI-")
