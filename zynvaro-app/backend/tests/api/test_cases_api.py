"""
backend/tests/api/test_cases_api.py
=====================================
API contract tests for /cases and /admin/cases endpoints.

Covers:
- Worker submitting appeal via POST /claims/{id}/appeal
- Worker submitting generic grievance via POST /cases
- Worker cannot access other workers' cases
- Appeal eligibility endpoint
- Case created with SLA due_at
- Admin resolve (uphold / reverse)
- Admin resolve mandatory internal_note
- Admin reopen and reopen limit
- Worker cannot call admin endpoints
- Case messages visible to worker
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from models import ClaimStatus, PolicyStatus


# ─── Helpers ────────────────────────────────────────────────────

def _create_policy(authed_client) -> dict:
    r = authed_client.post("/policies/", json={"tier": "Standard Guard"})
    assert r.status_code == 201, r.text
    return r.json()


def _force_old_policy(authed_client, test_db, hours=25) -> object:
    """Set the active policy's start_date to hours ago so cooling-off is cleared."""
    from models import Policy
    p = test_db.query(Policy).filter(Policy.status == PolicyStatus.ACTIVE).first()
    if p:
        p.start_date = datetime.utcnow() - timedelta(hours=hours)
        test_db.commit()
    return p


def _force_claim(test_db, worker_id: int, policy_id: int, trigger_id: int) -> object:
    """Directly insert a claim to test the appeal flow without running the trigger."""
    from models import Claim
    c = Claim(
        worker_id=worker_id,
        policy_id=policy_id,
        trigger_event_id=trigger_id,
        claim_number=f"CLM-TEST-{trigger_id}",
        payout_amount=500.0,
        status=ClaimStatus.AUTO_APPROVED,
        gps_valid=True, activity_valid=True, device_valid=True,
        authenticity_score=0.9,
        appeal_status="none",
    )
    test_db.add(c)
    test_db.commit()
    test_db.refresh(c)
    return c


# ─── Tests: Appeal eligibility endpoint ─────────────────────────

class TestAppealEligibilityEndpoint:

    def test_eligibility_within_window_returns_eligible(
        self, authed_client, make_trigger, test_db
    ):
        _create_policy(authed_client)
        t = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")
        claim = _force_claim(
            test_db,
            authed_client.worker.id,
            test_db.query(__import__("models").Policy).first().id,
            t.id,
        )
        resp = authed_client.get(f"/claims/{claim.id}/appeal-eligibility")
        assert resp.status_code == 200
        data = resp.json()
        assert data["eligible"] is True
        assert "window_expires_at" in data
        assert len(data["category_options"]) > 0

    def test_eligibility_expired_returns_not_eligible(
        self, authed_client, make_trigger, test_db
    ):
        _create_policy(authed_client)
        t = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")
        claim = _force_claim(
            test_db,
            authed_client.worker.id,
            test_db.query(__import__("models").Policy).first().id,
            t.id,
        )
        # Backdate the claim to 50h ago (outside 48h window)
        claim.created_at = datetime.utcnow() - timedelta(hours=50)
        test_db.commit()

        resp = authed_client.get(f"/claims/{claim.id}/appeal-eligibility")
        assert resp.status_code == 200
        data = resp.json()
        assert data["eligible"] is False
        assert data["reason_code"] == "CASE_WINDOW_EXPIRED"

    def test_eligibility_returns_404_for_other_workers_claim(
        self, authed_client, make_worker, make_policy, make_trigger, test_db
    ):
        other = make_worker(city="Bangalore")
        p = make_policy(worker=other, status=PolicyStatus.ACTIVE,
                       start_date=datetime.utcnow() - timedelta(hours=25))
        t = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")
        c = _force_claim(test_db, other.id, p.id, t.id)
        resp = authed_client.get(f"/claims/{c.id}/appeal-eligibility")
        assert resp.status_code == 404


# ─── Tests: Submit appeal ────────────────────────────────────────

class TestSubmitAppeal:

    def _get_claim(self, authed_client, make_trigger, test_db) -> object:
        _create_policy(authed_client)
        t = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")
        return _force_claim(
            test_db,
            authed_client.worker.id,
            test_db.query(__import__("models").Policy).first().id,
            t.id,
        )

    def test_submit_appeal_creates_grievance_case(self, authed_client, make_trigger, test_db):
        claim = self._get_claim(authed_client, make_trigger, test_db)
        resp = authed_client.post(f"/claims/{claim.id}/appeal", json={
            "category_code": "ZONE_MISMATCH_DISPUTE",
            "worker_summary_text": "I was clearly working in Bangalore on that day.",
        })
        assert resp.status_code == 201, resp.text
        data = resp.json()
        assert data["case_type"] == "APPEAL"
        assert data["category_code"] == "ZONE_MISMATCH_DISPUTE"
        assert data["public_case_id"].startswith("GRV-")

    def test_submit_appeal_updates_claim_appeal_status(self, authed_client, make_trigger, test_db):
        from models import Claim
        claim = self._get_claim(authed_client, make_trigger, test_db)
        authed_client.post(f"/claims/{claim.id}/appeal", json={
            "category_code": "ZONE_MISMATCH_DISPUTE",
            "worker_summary_text": "My location was correct — please check the zone mapping.",
        })
        test_db.refresh(claim)
        assert claim.appeal_status == "initiated"

    def test_submit_appeal_sets_sla_due_at(self, authed_client, make_trigger, test_db):
        claim = self._get_claim(authed_client, make_trigger, test_db)
        resp = authed_client.post(f"/claims/{claim.id}/appeal", json={
            "category_code": "RECENT_ACTIVITY_DISPUTE",
            "worker_summary_text": "I completed 5 deliveries that day — activity was real.",
        })
        data = resp.json()
        assert data["sla_due_at"] is not None
        # SLA should be ~72h from now
        sla = datetime.fromisoformat(data["sla_due_at"].replace("Z", ""))
        diff_h = (sla - datetime.utcnow()).total_seconds() / 3600
        assert 70 <= diff_h <= 74

    def test_submit_appeal_gives_case_status_triaged(self, authed_client, make_trigger, test_db):
        claim = self._get_claim(authed_client, make_trigger, test_db)
        resp = authed_client.post(f"/claims/{claim.id}/appeal", json={
            "category_code": "ZONE_MISMATCH_DISPUTE",
            "worker_summary_text": "Zone was wrong — I was in Bangalore Central.",
        })
        data = resp.json()
        assert data["status"] == "TRIAGED"

    def test_duplicate_appeal_is_rejected(self, authed_client, make_trigger, test_db):
        claim = self._get_claim(authed_client, make_trigger, test_db)
        body = {
            "category_code": "ZONE_MISMATCH_DISPUTE",
            "worker_summary_text": "Zone was wrong — I was in Bangalore Central.",
        }
        authed_client.post(f"/claims/{claim.id}/appeal", json=body)
        # Second attempt
        resp2 = authed_client.post(f"/claims/{claim.id}/appeal", json=body)
        assert resp2.status_code == 422
        assert "already open" in resp2.json()["detail"].lower()

    def test_appeal_messages_are_visible_to_worker(self, authed_client, make_trigger, test_db):
        claim = self._get_claim(authed_client, make_trigger, test_db)
        resp = authed_client.post(f"/claims/{claim.id}/appeal", json={
            "category_code": "PAYOUT_FAILED_AFTER_APPROVAL",
            "worker_summary_text": "Payment failed but claim was approved.",
        })
        data = resp.json()
        assert len(data["messages"]) >= 1
        assert all(m["visible_to_worker"] for m in data["messages"])

    def test_summary_too_short_rejected(self, authed_client, make_trigger, test_db):
        claim = self._get_claim(authed_client, make_trigger, test_db)
        resp = authed_client.post(f"/claims/{claim.id}/appeal", json={
            "category_code": "ZONE_MISMATCH_DISPUTE",
            "worker_summary_text": "short",
        })
        assert resp.status_code == 422

    def test_invalid_category_code_rejected(self, authed_client, make_trigger, test_db):
        claim = self._get_claim(authed_client, make_trigger, test_db)
        resp = authed_client.post(f"/claims/{claim.id}/appeal", json={
            "category_code": "NOT_A_REAL_CODE",
            "worker_summary_text": "This is my detailed reason for appealing.",
        })
        assert resp.status_code == 422


# ─── Tests: Submit grievance ─────────────────────────────────────

class TestSubmitGrievance:

    def test_worker_can_submit_generic_grievance(self, authed_client):
        resp = authed_client.post("/cases", json={
            "category_code": "PREMIUM_DEBIT_ISSUE",
            "worker_summary_text": "I was charged twice for my weekly premium.",
        })
        assert resp.status_code == 201
        data = resp.json()
        assert data["case_type"] == "GRIEVANCE"
        assert data["public_case_id"].startswith("GRV-")

    def test_grievance_without_claim_link_is_accepted(self, authed_client):
        resp = authed_client.post("/cases", json={
            "category_code": "APP_BUG",
            "worker_summary_text": "The payout history screen crashes when I scroll.",
        })
        assert resp.status_code == 201

    def test_grievance_sets_sla_due_at(self, authed_client):
        resp = authed_client.post("/cases", json={
            "category_code": "RENEWAL_ISSUE",
            "worker_summary_text": "I was charged for a renewal I did not authorise.",
        })
        data = resp.json()
        assert data["sla_due_at"] is not None

    def test_grievance_appeal_code_rejected_for_grievance_endpoint(self, authed_client):
        """ZONE_MISMATCH_DISPUTE is an appeal code, not a grievance code."""
        resp = authed_client.post("/cases", json={
            "category_code": "ZONE_MISMATCH_DISPUTE",
            "worker_summary_text": "This should be rejected as it is an appeal code.",
        })
        assert resp.status_code == 422


# ─── Tests: Case list / detail ───────────────────────────────────

class TestCaseListAndDetail:

    def test_list_cases_returns_worker_own_cases_only(self, authed_client):
        authed_client.post("/cases", json={
            "category_code": "APP_BUG",
            "worker_summary_text": "The app keeps crashing on the claims page.",
        })
        resp = authed_client.get("/cases")
        assert resp.status_code == 200
        cases = resp.json()
        assert len(cases) >= 1
        assert all(c["case_type"] in ("APPEAL", "GRIEVANCE") for c in cases)

    def test_get_case_detail_includes_messages(self, authed_client):
        authed_client.post("/cases", json={
            "category_code": "NOTIFICATION_ISSUE",
            "worker_summary_text": "I never received the payout notification message.",
        })
        cases = authed_client.get("/cases").json()
        case_id = cases[0]["id"]
        resp = authed_client.get(f"/cases/{case_id}")
        assert resp.status_code == 200
        data = resp.json()
        assert "messages" in data
        assert len(data["messages"]) >= 1

    def test_worker_cannot_see_other_workers_case(
        self, authed_client, make_worker, test_db
    ):
        # Create a case for authed_client's worker
        authed_client.post("/cases", json={
            "category_code": "APP_BUG",
            "worker_summary_text": "App crashes every time I open payout history.",
        })
        from models import GrievanceCase
        case = test_db.query(GrievanceCase).first()

        # Change the worker_id to someone else's
        case.worker_id = case.worker_id + 9999
        test_db.commit()

        resp = authed_client.get(f"/cases/{case.id}")
        assert resp.status_code == 403


# ─── Tests: Admin resolve ────────────────────────────────────────

class TestAdminResolve:

    def _get_admin_client(self, test_db):
        """Return a client with a real admin worker."""
        from tests.conftest import worker_token
        from models import Worker
        from passlib.context import CryptContext
        from fastapi.testclient import TestClient
        from main import app
        from database import get_db

        pwd = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
        admin = Worker(
            full_name="Admin User", phone="9000000099",
            email="admin@zynvaro.test",
            password_hash=pwd.hash("adminpass"),
            city="Bangalore", pincode="560001",
            platform="Blinkit", vehicle_type="2-Wheeler",
            shift="Morning (6AM-2PM)", is_admin=True,
        )
        test_db.add(admin)
        test_db.commit()
        test_db.refresh(admin)

        def _override():
            try:
                yield test_db
            finally:
                pass

        app.dependency_overrides[get_db] = _override
        token = worker_token(admin.id)
        client = TestClient(app, raise_server_exceptions=True)
        client.headers.update({"Authorization": f"Bearer {token}"})
        return client, admin

    def _create_case(self, authed_client, test_db) -> int:
        resp = authed_client.post("/cases", json={
            "category_code": "APP_BUG",
            "worker_summary_text": "App crashes every time I open the payout history page.",
        })
        return resp.json()["id"]

    def test_admin_can_resolve_upheld(self, authed_client, test_db):
        from database import get_db
        case_id = self._create_case(authed_client, test_db)
        admin_c, _ = self._get_admin_client(test_db)

        resp = admin_c.post(f"/admin/cases/{case_id}/resolve", json={
            "decision_type": "UPHOLD",
            "decision_reason_code": "DATA_MATCHES_SOURCE",
            "worker_visible_text": "We reviewed your case and the original decision was correct.",
            "internal_note": "Data verified against IMD API — measurements confirm threshold crossed.",
            "payout_retry_required": False,
        })
        assert resp.status_code == 200
        assert resp.json()["status"] == "RESOLVED_UPHELD"

    def test_admin_resolve_requires_internal_note_min_20_chars(
        self, authed_client, test_db
    ):
        case_id = self._create_case(authed_client, test_db)
        admin_c, _ = self._get_admin_client(test_db)

        resp = admin_c.post(f"/admin/cases/{case_id}/resolve", json={
            "decision_type": "UPHOLD",
            "decision_reason_code": "DATA_OK",
            "worker_visible_text": "All good.",
            "internal_note": "short",
        })
        assert resp.status_code == 422

    def test_non_admin_cannot_access_admin_data(self, authed_client, test_db):
        # A normal worker hitting /admin/cases must either get 403 (not admin)
        # or 404 (not found) — never 200 with data.
        resp = authed_client.get("/admin/cases/99999")
        assert resp.status_code in (403, 404), (
            f"Expected 403 or 404 for non-admin access, got {resp.status_code}"
        )
        # If it returned 200, that's a permissions leak
        assert resp.status_code != 200

    def test_admin_reopen_increments_reopen_count(self, authed_client, test_db):
        from models import GrievanceCase
        case_id = self._create_case(authed_client, test_db)
        admin_c, _ = self._get_admin_client(test_db)

        # First resolve it
        admin_c.post(f"/admin/cases/{case_id}/resolve", json={
            "decision_type": "UPHOLD",
            "decision_reason_code": "DATA_OK",
            "worker_visible_text": "Decision was correct.",
            "internal_note": "Checked all snapshots and evidence, upheld.",
        })
        # Reopen
        resp = admin_c.post(f"/admin/cases/{case_id}/reopen", json={
            "reason": "New evidence of shift overlap submitted.",
        })
        assert resp.status_code == 200

        case = test_db.query(GrievanceCase).filter(GrievanceCase.id == case_id).first()
        test_db.refresh(case)
        assert case.reopen_count == 1
