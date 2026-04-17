from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from models import ClaimStatus, PayoutTransactionStatus, TriggerType
from tests.conftest import worker_token


EXPLAINABILITY_KEYS = {
    "status_label",
    "trigger_type",
    "source_label",
    "source_type",
    "source_state",
    "measured_value",
    "measured_unit",
    "threshold_value",
    "threshold_unit",
    "threshold_result",
    "zone_match_status",
    "shift_overlap_status",
    "recent_activity_status",
    "event_window_start",
    "event_window_end",
    "processed_at",
    "payout_formula_text",
    "payout_amount",
    "payout_cap_applied",
    "confidence_score",
    "confidence_band",
    "payment_status",
    "payment_ref",
    "appeal_allowed",
    "appeal_deadline",
    "reason_code",
    "reason_text",
    "claim_time_snapshot_version",
}


def test_get_claim_explainability_requires_auth(client, make_worker, make_policy, make_trigger, make_claim):
    worker = make_worker()
    policy = make_policy(worker=worker)
    trigger = make_trigger()
    claim = make_claim(worker=worker, policy=policy, trigger=trigger)

    resp = client.get(f"/claims/{claim.id}/explainability")

    assert resp.status_code == 401


def test_get_claim_explainability_returns_expected_contract_for_paid_claim(
    authed_client, make_policy, make_trigger, make_claim, make_payout_txn
):
    worker = authed_client.worker
    policy = make_policy(worker=worker, tier="Pro Shield")
    trigger = make_trigger(
        trigger_type=TriggerType.HEAVY_RAINFALL,
        city=worker.city,
        measured_value=78.0,
        threshold_value=64.5,
        source_primary="IMD district weather feed",
        source_secondary="OpenWeatherMap continuity feed",
        confidence_score=82.0,
    )
    claim = make_claim(
        worker=worker,
        policy=policy,
        trigger=trigger,
        status=ClaimStatus.PAID,
        payout_amount=420.0,
        trigger_confidence_score=82.0,
        payment_ref="RZP-pay_explain_0001",
    )
    make_payout_txn(
        claim=claim,
        status=PayoutTransactionStatus.SETTLED,
        razorpay_payment_id="pay_explain_0001",
        upi_ref="UTREXPLAIN1234",
    )

    resp = authed_client.get(f"/claims/{claim.id}/explainability")

    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == EXPLAINABILITY_KEYS
    assert body["status_label"] == "Paid"
    assert body["trigger_type"] == TriggerType.HEAVY_RAINFALL
    assert body["threshold_result"] == "met"
    assert body["payment_status"] == "paid"
    assert body["confidence_score"] == pytest.approx(82.0)
    assert body["confidence_band"] == "medium"
    assert body["payment_ref"].startswith("UTRE")
    assert body["payment_ref"].endswith("1234")
    assert body["reason_code"] == "payout_approved"


def test_get_claim_explainability_worker_cannot_fetch_another_workers_claim(
    client, test_db, make_worker, make_policy, make_trigger, make_claim
):
    owner = make_worker(phone="9111111111", email="owner@zynvaro.test")
    other = make_worker(phone="9222222222", email="viewer@zynvaro.test")
    policy = make_policy(worker=owner)
    trigger = make_trigger(city=owner.city)
    claim = make_claim(worker=owner, policy=policy, trigger=trigger)

    headers = {"Authorization": f"Bearer {worker_token(other.id)}"}
    resp = client.get(f"/claims/{claim.id}/explainability", headers=headers)

    assert resp.status_code == 404


def test_get_claim_explainability_serializes_nulls_safely(
    authed_client, test_db, make_policy, make_trigger, make_claim
):
    worker = authed_client.worker
    policy = make_policy(worker=worker)
    trigger = make_trigger(source_secondary=None, source_primary="Source pending")
    claim = make_claim(
        worker=worker,
        policy=policy,
        trigger=trigger,
        status=ClaimStatus.PENDING_REVIEW,
        payment_ref=None,
        paid_at=None,
        trigger_confidence_score=None,
        recent_activity_valid=True,
        recent_activity_reason=None,
        recent_activity_at=None,
        recent_activity_age_hours=None,
    )
    trigger.confidence_score = None
    test_db.commit()

    resp = authed_client.get(f"/claims/{claim.id}/explainability")

    assert resp.status_code == 200
    body = resp.json()
    assert body["payment_ref"] is None
    assert body["threshold_value"] == pytest.approx(64.5)
    assert body["confidence_score"] is None
    assert body["confidence_band"] == "unknown"
    assert body["recent_activity_status"] == "unknown"


def test_get_claim_explainability_appeal_allowed_for_recent_denial(
    authed_client, test_db, make_policy, make_trigger, make_claim
):
    worker = authed_client.worker
    policy = make_policy(worker=worker)
    trigger = make_trigger(city=worker.city)
    claim = make_claim(
        worker=worker,
        policy=policy,
        trigger=trigger,
        status=ClaimStatus.REJECTED,
        payment_ref=None,
        paid_at=None,
    )
    claim.created_at = datetime.utcnow() - timedelta(hours=2)
    test_db.commit()

    resp = authed_client.get(f"/claims/{claim.id}/explainability")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status_label"] == "Not paid"
    assert body["appeal_allowed"] is True
    assert body["appeal_deadline"] is not None


def test_get_claim_explainability_disables_appeal_after_deadline(
    authed_client, test_db, make_policy, make_trigger, make_claim
):
    worker = authed_client.worker
    policy = make_policy(worker=worker)
    trigger = make_trigger(city=worker.city)
    claim = make_claim(
        worker=worker,
        policy=policy,
        trigger=trigger,
        status=ClaimStatus.REJECTED,
        payment_ref=None,
        paid_at=None,
    )
    claim.created_at = datetime.utcnow() - timedelta(hours=72)
    test_db.commit()

    resp = authed_client.get(f"/claims/{claim.id}/explainability")

    assert resp.status_code == 200
    assert resp.json()["appeal_allowed"] is False


def test_get_claim_explainability_reflects_failed_payout_status(
    authed_client, make_policy, make_trigger, make_claim, make_payout_txn
):
    worker = authed_client.worker
    policy = make_policy(worker=worker)
    trigger = make_trigger(city=worker.city)
    claim = make_claim(
        worker=worker,
        policy=policy,
        trigger=trigger,
        status=ClaimStatus.PAID,
        payment_ref="RZP-pay_failed_0001",
        paid_at=None,
    )
    make_payout_txn(
        claim=claim,
        status=PayoutTransactionStatus.FAILED,
        failure_reason="Bank unavailable",
        upi_ref=None,
        razorpay_payment_id="pay_failed_0001",
    )

    resp = authed_client.get(f"/claims/{claim.id}/explainability")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status_label"] == "Approved, payout failed"
    assert body["payment_status"] == "failed"
    assert body["reason_code"] == "payment_operational_issue"
    assert body["payment_ref"] is None


def test_get_claim_explainability_prefers_latest_transaction(
    authed_client, make_policy, make_trigger, make_claim, make_payout_txn
):
    worker = authed_client.worker
    policy = make_policy(worker=worker)
    trigger = make_trigger(city=worker.city)
    claim = make_claim(
        worker=worker,
        policy=policy,
        trigger=trigger,
        status=ClaimStatus.PAID,
        payment_ref="RZP-pay_latest_0002",
        paid_at=None,
    )
    make_payout_txn(
        claim=claim,
        status=PayoutTransactionStatus.PENDING,
        initiated_at=datetime.utcnow() - timedelta(minutes=10),
        upi_ref=None,
        razorpay_payment_id="pay_latest_0001",
    )
    make_payout_txn(
        claim=claim,
        status=PayoutTransactionStatus.FAILED,
        initiated_at=datetime.utcnow() - timedelta(minutes=2),
        upi_ref=None,
        razorpay_payment_id="pay_latest_0002",
        failure_reason="NPCI timeout",
    )

    resp = authed_client.get(f"/claims/{claim.id}/explainability")

    assert resp.status_code == 200
    body = resp.json()
    assert body["payment_status"] == "failed"
    assert body["status_label"] == "Approved, payout failed"


def test_get_claim_explainability_does_not_leak_internal_only_fields(
    authed_client, make_policy, make_trigger, make_claim, make_payout_txn
):
    worker = authed_client.worker
    policy = make_policy(worker=worker)
    trigger = make_trigger(city=worker.city)
    claim = make_claim(
        worker=worker,
        policy=policy,
        trigger=trigger,
        status=ClaimStatus.PAID,
        payment_ref="RZP-pay_privacy_0001",
        paid_at=None,
        fraud_flags="velocity_spike|device_mismatch",
    )
    claim.claim_lat = 12.97
    claim.claim_lng = 77.59
    claim.ml_fraud_probability = 0.81
    make_payout_txn(
        claim=claim,
        status=PayoutTransactionStatus.SETTLED,
        razorpay_payment_id="pay_privacy_0001",
        upi_ref="UTRPRIVACY1234",
        gateway_payload='{"raw":"should_not_leak"}',
    )

    resp = authed_client.get(f"/claims/{claim.id}/explainability")

    assert resp.status_code == 200
    body = resp.json()
    for field in {"fraud_flags", "claim_lat", "claim_lng", "ml_fraud_probability", "gateway_payload", "upi_id"}:
        assert field not in body
