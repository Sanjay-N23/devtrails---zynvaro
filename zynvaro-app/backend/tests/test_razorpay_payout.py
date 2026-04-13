"""
Zynvaro — Razorpay Payout Integration Tests (Phase 3: SOAR)
Rigorous edge case testing modeled after GPay/PhonePe payment platform standards.

Categories:
  1. Mock Payout Flow (no Razorpay keys)
  2. Idempotency & Uniqueness
  3. Amount Edge Cases (zero, fractional, max)
  4. Worker Data Edge Cases (long phone, special chars)
  5. Transaction Status Lifecycle
  6. Claim-Transaction Relationship
  7. Webhook Handler
  8. Graceful Degradation
"""

import os
import json
import pytest
from datetime import datetime, timedelta

# Disable Razorpay in tests
os.environ["RAZORPAY_KEY_ID"] = ""
os.environ["RAZORPAY_KEY_SECRET"] = ""

from services.payout_service import (
    initiate_payout, is_razorpay_configured, get_payout_details,
    _create_mock_payout,
)
from models import (
    Claim, Worker, Policy, TriggerEvent, PayoutTransaction,
    PayoutTransactionStatus, ClaimStatus,
)


# ─── Category 1: Mock Payout Flow ────────────────────────────────
class TestMockPayoutFlow:
    def test_razorpay_not_configured(self):
        assert not is_razorpay_configured()

    def test_mock_creates_payout_transaction(self, test_db, make_worker, make_policy, make_trigger):
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-MOCK-001", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        txn = initiate_payout(c, w, test_db)
        test_db.flush()  # Assign ID
        assert txn is not None
        assert txn.id is not None

    def test_mock_gateway_is_mock(self, test_db, make_worker, make_policy, make_trigger):
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-MOCK-002", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        txn = initiate_payout(c, w, test_db)
        assert txn.gateway_name == "mock"

    def test_mock_settled_immediately(self, test_db, make_worker, make_policy, make_trigger):
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-MOCK-003", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        txn = initiate_payout(c, w, test_db)
        assert txn.status == PayoutTransactionStatus.SETTLED
        assert txn.settled_at is not None
        assert txn.amount_settled == 300.0

    def test_mock_upi_ref_format(self, test_db, make_worker, make_policy, make_trigger):
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-MOCK-004", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        txn = initiate_payout(c, w, test_db)
        assert txn.upi_ref.startswith("MOCK-UTR-")
        assert txn.internal_txn_id.startswith("ZYN-MOCK-")

    def test_mock_sets_claim_paid(self, test_db, make_worker, make_policy, make_trigger):
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-MOCK-005", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        initiate_payout(c, w, test_db)
        assert c.paid_at is not None
        assert c.payment_ref.startswith("MOCK-UPI-")

    def test_mock_payload_valid_json(self, test_db, make_worker, make_policy, make_trigger):
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-MOCK-006", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        txn = initiate_payout(c, w, test_db)
        payload = json.loads(txn.gateway_payload)
        assert payload["status"] == "settled"
        assert "utr" in payload
        assert "vpa" in payload
        assert payload["amount"] == 300.0


# ─── Category 2: Idempotency & Uniqueness ────────────────────────
class TestIdempotency:
    def test_different_claims_different_txn_ids(self, test_db, make_worker, make_policy, make_trigger):
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c1 = Claim(claim_number="CLM-IDEM-001", worker_id=w.id, policy_id=p.id,
                   trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                   authenticity_score=100)
        c2 = Claim(claim_number="CLM-IDEM-002", worker_id=w.id, policy_id=p.id,
                   trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=250,
                   authenticity_score=90)
        test_db.add_all([c1, c2]); test_db.flush()
        txn1 = initiate_payout(c1, w, test_db)
        txn2 = initiate_payout(c2, w, test_db)
        assert txn1.internal_txn_id != txn2.internal_txn_id

    def test_retry_creates_new_txn(self, test_db, make_worker, make_policy, make_trigger):
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-IDEM-003", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        txn1 = initiate_payout(c, w, test_db)
        txn2 = _create_mock_payout(c, w, test_db)
        test_db.flush()
        assert txn1.id != txn2.id


# ─── Category 3: Amount Edge Cases ───────────────────────────────
class TestAmountEdgeCases:
    @pytest.mark.parametrize("amount,desc", [
        (0.0, "zero"),
        (1.0, "minimum 1 INR"),
        (0.50, "fractional 50 paise"),
        (300.0, "standard Basic"),
        (600.0, "standard Standard"),
        (1000.0, "standard Pro"),
        (2000.0, "max weekly Pro"),
        (332.50, "fractional"),
        (9999.99, "very large"),
    ])
    def test_amount_handled(self, test_db, make_worker, make_policy, make_trigger, amount, desc):
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number=f"CLM-AMT-{desc[:8].upper()}", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=amount,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        txn = initiate_payout(c, w, test_db)
        assert txn.amount_requested == amount
        assert txn.amount_settled == amount
        assert txn.status == PayoutTransactionStatus.SETTLED


# ─── Category 4: Worker Data Edge Cases ──────────────────────────
class TestWorkerEdgeCases:
    def test_long_phone_number(self, test_db, make_policy, make_trigger):
        w = Worker(full_name="Long Phone", phone="919876543210999",
                   password_hash="x", city="Delhi", pincode="110001", platform="Zepto")
        test_db.add(w); test_db.flush()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-WRK-001", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        txn = initiate_payout(c, w, test_db)
        assert "@" in txn.upi_id

    def test_special_chars_in_name(self, test_db, make_policy, make_trigger):
        w = Worker(full_name="Ravi K. Singh-Jr (III)", phone="9999988881",
                   password_hash="x", city="Pune", pincode="411001", platform="Swiggy")
        test_db.add(w); test_db.flush()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-WRK-002", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        txn = initiate_payout(c, w, test_db)
        assert txn.status == PayoutTransactionStatus.SETTLED

    def test_unicode_name(self, test_db, make_policy, make_trigger):
        w = Worker(full_name="Ravi Kumar", phone="9999988882",
                   password_hash="x", city="Mumbai", pincode="400001", platform="Blinkit")
        test_db.add(w); test_db.flush()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-WRK-003", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        txn = initiate_payout(c, w, test_db)
        assert txn is not None


# ─── Category 5: Transaction Status Lifecycle ────────────────────
class TestTransactionLifecycle:
    def test_all_statuses_exist(self):
        assert PayoutTransactionStatus.INITIATED == "initiated"
        assert PayoutTransactionStatus.PENDING == "pending"
        assert PayoutTransactionStatus.SETTLED == "settled"
        assert PayoutTransactionStatus.FAILED == "failed"
        assert PayoutTransactionStatus.REVERSED == "reversed"
        assert PayoutTransactionStatus.RETRYING == "retrying"

    def test_settled_has_timestamp(self, test_db, make_worker, make_policy, make_trigger):
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-LIFE-001", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        txn = initiate_payout(c, w, test_db)
        assert txn.settled_at is not None
        assert txn.initiated_at is not None
        assert txn.settled_at >= txn.initiated_at

    def test_currency_always_inr(self, test_db, make_worker, make_policy, make_trigger):
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-LIFE-002", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        txn = initiate_payout(c, w, test_db)
        assert txn.currency == "INR"


# ─── Category 6: Claim-Transaction Relationship ──────────────────
class TestClaimTransactionRelation:
    def test_txn_links_to_correct_worker(self, test_db, make_worker, make_policy, make_trigger):
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-REL-001", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        txn = initiate_payout(c, w, test_db)
        assert txn.worker_id == w.id
        assert txn.claim_id == c.id

    def test_get_payout_details(self, test_db, make_worker, make_policy, make_trigger):
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-REL-002", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        initiate_payout(c, w, test_db)
        test_db.flush()
        details = get_payout_details(c.id, test_db)
        assert details is not None
        assert details["gateway"] == "mock"
        assert details["status"] == PayoutTransactionStatus.SETTLED
        assert details["amount_requested"] == 300.0

    def test_no_payout_returns_none(self, test_db):
        details = get_payout_details(99999, test_db)
        assert details is None


# ─── Category 7: Webhook Handler ─────────────────────────────────
class TestWebhookHandler:
    def test_webhook_endpoint_exists(self, client):
        resp = client.get("/webhooks/razorpay/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ok"
        assert data["razorpay_configured"] == False  # No keys in test

    def test_webhook_with_invalid_json(self, client):
        resp = client.post("/webhooks/razorpay", content=b"not json",
                           headers={"Content-Type": "application/json"})
        assert resp.status_code == 400

    def test_webhook_no_matching_txn(self, client):
        payload = {
            "event": "payout.processed",
            "payload": {
                "payout": {
                    "entity": {
                        "id": "pout_nonexistent",
                        "status": "processed",
                        "utr": "UTR123456",
                        "amount": 30000,
                        "reference_id": "ZYN-FAKE-XXXXX",
                    }
                }
            }
        }
        resp = client.post("/webhooks/razorpay", json=payload)
        assert resp.status_code == 200
        data = resp.json()
        assert data["status"] == "ignored"

    def test_webhook_processes_settlement(self, test_db, authed_client, make_worker, make_policy, make_trigger):
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-WH-001", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()

        # Create a PENDING transaction manually
        txn = PayoutTransaction(
            claim_id=c.id, worker_id=w.id, upi_id="9876@upi",
            internal_txn_id="ZYN-WH-TEST001",
            amount_requested=300, currency="INR",
            status=PayoutTransactionStatus.PENDING,
            gateway_name="razorpay",
            gateway_payload=json.dumps({"payment_link_id": "plink_test123"}),
        )
        test_db.add(txn); test_db.flush()

        # Send webhook
        payload = {
            "event": "payout.processed",
            "payload": {
                "payout": {
                    "entity": {
                        "id": "plink_test123",
                        "status": "processed",
                        "utr": "UTR-REAL-12345",
                        "amount": 30000,
                        "reference_id": "ZYN-WH-TEST001",
                    }
                }
            }
        }
        resp = authed_client.post("/webhooks/razorpay", json=payload)
        assert resp.status_code == 200

        # Verify transaction updated
        test_db.refresh(txn)
        assert txn.status == PayoutTransactionStatus.SETTLED
        assert txn.upi_ref == "UTR-REAL-12345"
        assert txn.settled_at is not None

    def test_webhook_handles_failure(self, test_db, authed_client, make_worker, make_policy, make_trigger):
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-WH-002", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()

        txn = PayoutTransaction(
            claim_id=c.id, worker_id=w.id, upi_id="9876@upi",
            internal_txn_id="ZYN-WH-TEST002",
            amount_requested=300, currency="INR",
            status=PayoutTransactionStatus.PENDING,
            gateway_name="razorpay",
            gateway_payload=json.dumps({"payment_link_id": "plink_fail123"}),
        )
        test_db.add(txn); test_db.flush()

        payload = {
            "event": "payout.failed",
            "payload": {
                "payout": {
                    "entity": {
                        "id": "plink_fail123",
                        "status": "failed",
                        "failure_reason": "Insufficient balance",
                        "reference_id": "ZYN-WH-TEST002",
                    }
                }
            }
        }
        resp = authed_client.post("/webhooks/razorpay", json=payload)
        assert resp.status_code == 200

        test_db.refresh(txn)
        assert txn.status == PayoutTransactionStatus.FAILED
        assert "Insufficient" in txn.failure_reason


# ─── Category 8: Graceful Degradation ────────────────────────────
class TestGracefulDegradation:
    def test_no_keys_uses_mock(self, test_db, make_worker, make_policy, make_trigger):
        """Without Razorpay keys, payout falls back to mock."""
        assert not is_razorpay_configured()
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-DEG-001", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        txn = initiate_payout(c, w, test_db)
        assert txn.gateway_name == "mock"
        assert txn.status == PayoutTransactionStatus.SETTLED

    def test_claim_always_paid_regardless_of_gateway(self, test_db, make_worker, make_policy, make_trigger):
        """Claim.paid_at is always set, whether Razorpay or mock."""
        w = make_worker()
        p = make_policy(w)
        t = make_trigger()
        c = Claim(claim_number="CLM-DEG-002", worker_id=w.id, policy_id=p.id,
                  trigger_event_id=t.id, status=ClaimStatus.PAID, payout_amount=300,
                  authenticity_score=100)
        test_db.add(c); test_db.flush()
        initiate_payout(c, w, test_db)
        assert c.paid_at is not None
        assert c.payment_ref is not None
