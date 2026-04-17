from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from models import ClaimStatus, PolicyStatus, PayoutTransactionStatus, TriggerType
from services.explainability import (
    CLAIM_TIME_SNAPSHOT_VERSION,
    build_explainability_payload,
)


def _assert_common_payload_shape(payload):
    assert payload.status_label
    assert payload.source_label
    assert payload.source_type in {"official", "fallback", "provisional"}
    assert payload.source_state in {
        "confirmed",
        "fallback_used",
        "provisional",
        "stale",
        "disputed",
        "missing",
        "archived",
    }
    assert payload.threshold_result in {"met", "not_met", "unknown", "under_review"}
    assert payload.zone_match_status in {"matched", "mismatch", "unknown"}
    assert payload.shift_overlap_status in {"passed", "failed", "unknown"}
    assert payload.recent_activity_status in {"passed", "failed", "unknown"}
    assert payload.payment_status in {
        "paid",
        "pending",
        "failed",
        "reversed",
        "not_paid",
        "manual_review",
        "pending_review",
        "unknown",
    }
    assert payload.claim_time_snapshot_version == CLAIM_TIME_SNAPSHOT_VERSION


def test_explainability_paid_claim_renders_formula_and_source(
    make_worker, make_policy, make_trigger, make_claim, make_payout_txn
):
    worker = make_worker(city="Chennai")
    policy = make_policy(worker=worker, tier="Pro Shield")
    trigger = make_trigger(
        trigger_type=TriggerType.HEAVY_RAINFALL,
        city="Chennai",
        measured_value=82.4,
        threshold_value=64.5,
        unit="mm/24hr",
        source_primary="IMD district weather feed",
        source_secondary="OpenWeatherMap continuity feed",
        confidence_score=88.0,
    )
    claim = make_claim(
        worker=worker,
        policy=policy,
        trigger=trigger,
        status=ClaimStatus.PAID,
        payout_amount=420.0,
        trigger_confidence_score=86.0,
        payment_ref="RZP-pay_paid_0001",
    )
    txn = make_payout_txn(
        claim=claim,
        status=PayoutTransactionStatus.SETTLED,
        razorpay_payment_id="pay_paid_0001",
        upi_ref="UTR1234567890",
    )

    payload = build_explainability_payload(
        claim=claim,
        policy=policy,
        trigger_event=trigger,
        payout_txn=txn,
        eligibility_ctx={
            "formula_base_amount": 600.0,
            "formula_rate": 0.7,
            "claim_time_plan_tier": "Pro Shield",
        },
    )

    _assert_common_payload_shape(payload)
    assert payload.status_label == "Paid"
    assert payload.trigger_type == TriggerType.HEAVY_RAINFALL
    assert payload.source_type == "official"
    assert payload.threshold_result == "met"
    assert payload.payment_status == "paid"
    assert payload.payment_ref.startswith("UTR1")
    assert payload.payment_ref.endswith("7890")
    assert payload.reason_code == "payout_approved"
    assert "600" in payload.payout_formula_text
    assert "70" in payload.payout_formula_text
    assert payload.confidence_score == pytest.approx(86.0)
    assert payload.confidence_band == "high"
    assert payload.appeal_allowed is False


@pytest.mark.parametrize(
    ("claim_status", "txn_status", "expected_status_label", "expected_payment_status"),
    [
        (ClaimStatus.PENDING_REVIEW, None, "Pending review", "pending_review"),
        (ClaimStatus.MANUAL_REVIEW, None, "Manual review", "manual_review"),
        (ClaimStatus.PAID, PayoutTransactionStatus.PENDING, "Approved, payout in progress", "pending"),
        (ClaimStatus.PAID, PayoutTransactionStatus.FAILED, "Approved, payout failed", "failed"),
        (ClaimStatus.PAID, PayoutTransactionStatus.REVERSED, "Paid, later reversed", "reversed"),
        (ClaimStatus.REJECTED, None, "Not paid", "not_paid"),
    ],
)
def test_explainability_status_mapping_table(
    claim_status,
    txn_status,
    expected_status_label,
    expected_payment_status,
    make_worker,
    make_policy,
    make_trigger,
    make_claim,
    make_payout_txn,
):
    worker = make_worker()
    policy = make_policy(worker=worker)
    trigger = make_trigger()
    claim = make_claim(
        worker=worker,
        policy=policy,
        trigger=trigger,
        status=claim_status,
        paid_at=None,
        payment_ref=None if txn_status is None else "RZP-rzp_ref_0001",
    )
    txn = None
    if txn_status is not None:
        txn = make_payout_txn(
            claim=claim,
            status=txn_status,
            upi_ref="UTR-STATUS-0001" if txn_status in {PayoutTransactionStatus.SETTLED, PayoutTransactionStatus.REVERSED} else None,
            failure_reason="NPCI timeout" if txn_status == PayoutTransactionStatus.FAILED else None,
        )

    payload = build_explainability_payload(claim, policy, trigger, payout_txn=txn)

    assert payload.status_label == expected_status_label
    assert payload.payment_status == expected_payment_status


@pytest.mark.parametrize(
    ("measured_value", "threshold_value", "source_ctx", "expected"),
    [
        (64.5, 64.5, {}, "met"),
        (64.499, 64.5, {}, "not_met"),
        (64.500001, 64.5, {}, "met"),
        (401.0, 400.0, {"disagrees": True, "confidence_score": 42.0}, "under_review"),
        (None, 64.5, {}, "unknown"),
    ],
)
def test_explainability_threshold_boundary_cases(
    measured_value,
    threshold_value,
    source_ctx,
    expected,
    make_worker,
    make_policy,
    make_trigger,
    make_claim,
):
    worker = make_worker()
    policy = make_policy(worker=worker)
    trigger = make_trigger()
    claim = make_claim(worker=worker, policy=policy, trigger=trigger)

    trigger.measured_value = measured_value
    trigger.threshold_value = threshold_value
    trigger.unit = "mm/24hr"
    trigger.confidence_score = source_ctx.get("confidence_score", 88.0)

    payload = build_explainability_payload(
        claim=claim,
        policy=policy,
        trigger_event=trigger,
        source_ctx=source_ctx,
    )

    assert payload.threshold_result == expected


def test_explainability_null_and_malformed_values_fallback_safely(
    make_worker, make_policy, make_trigger, make_claim
):
    worker = make_worker()
    policy = make_policy(worker=worker)
    trigger = make_trigger(source_secondary=None)
    claim = make_claim(worker=worker, policy=policy, trigger=trigger)

    trigger.measured_value = None
    trigger.threshold_value = "broken-threshold"
    trigger.unit = None
    trigger.confidence_score = None
    claim.trigger_confidence_score = None

    payload = build_explainability_payload(claim, policy, trigger)

    _assert_common_payload_shape(payload)
    assert payload.measured_value is None
    assert payload.threshold_value is None
    assert payload.measured_unit == "-"
    assert payload.threshold_unit == "-"
    assert payload.threshold_result == "unknown"
    assert payload.confidence_score is None
    assert payload.confidence_band == "unknown"


@pytest.mark.parametrize(
    ("source_primary", "source_ctx", "expected_type", "expected_state", "expected_band"),
    [
        ("IMD district weather feed", {"stale": True, "confidence_score": 88.0}, "official", "stale", "high"),
        ("Simulation override feed", {"confidence_score": 91.0}, "fallback", "fallback_used", "high"),
        ("IMD district weather feed", {"disagrees": True, "confidence_score": 42.0}, "provisional", "disputed", "low"),
    ],
)
def test_explainability_source_badges_and_confidence_bands(
    source_primary,
    source_ctx,
    expected_type,
    expected_state,
    expected_band,
    make_worker,
    make_policy,
    make_trigger,
    make_claim,
):
    worker = make_worker()
    policy = make_policy(worker=worker)
    trigger = make_trigger(source_primary=source_primary, source_secondary="OpenWeatherMap continuity")
    claim = make_claim(worker=worker, policy=policy, trigger=trigger, trigger_confidence_score=None)

    payload = build_explainability_payload(
        claim=claim,
        policy=policy,
        trigger_event=trigger,
        source_ctx=source_ctx,
    )

    assert payload.source_type == expected_type
    assert payload.source_state == expected_state
    assert payload.confidence_band == expected_band


@pytest.mark.parametrize(
    ("eligibility_ctx", "txn_status", "expected_reason"),
    [
        (
            {"policy_active": False, "waiting_period_active": True, "zone_match_status": "mismatch"},
            None,
            "policy_inactive",
        ),
        (
            {"policy_active": True, "waiting_period_active": True, "zone_match_status": "mismatch"},
            None,
            "waiting_period_active",
        ),
        (
            {"policy_active": True, "duplicate_covered": True, "zone_match_status": "mismatch"},
            None,
            "duplicate_covered",
        ),
        (
            {"policy_active": True, "zone_match_status": "mismatch", "recent_activity_status": "failed"},
            None,
            "zone_mismatch",
        ),
        (
            {"policy_active": True, "shift_overlap_status": "failed", "recent_activity_status": "failed"},
            None,
            "no_shift_overlap",
        ),
        (
            {"policy_active": True, "recent_activity_status": "failed"},
            None,
            "recent_activity_not_met",
        ),
        (
            {"policy_active": True},
            PayoutTransactionStatus.FAILED,
            "payment_operational_issue",
        ),
    ],
)
def test_explainability_reason_precedence_highest_priority_only(
    eligibility_ctx,
    txn_status,
    expected_reason,
    make_worker,
    make_policy,
    make_trigger,
    make_claim,
    make_payout_txn,
):
    worker = make_worker()
    policy = make_policy(worker=worker)
    trigger = make_trigger()
    claim = make_claim(
        worker=worker,
        policy=policy,
        trigger=trigger,
        status=ClaimStatus.REJECTED if txn_status is None else ClaimStatus.PAID,
        paid_at=None,
        payment_ref=None,
    )
    txn = None
    if txn_status is not None:
        txn = make_payout_txn(
            claim=claim,
            status=txn_status,
            failure_reason="Bank timeout",
            upi_ref=None,
        )

    payload = build_explainability_payload(
        claim=claim,
        policy=policy,
        trigger_event=trigger,
        payout_txn=txn,
        eligibility_ctx=eligibility_ctx,
    )

    assert payload.reason_code == expected_reason


def test_explainability_hides_payment_ref_when_not_settled(
    make_worker, make_policy, make_trigger, make_claim, make_payout_txn
):
    worker = make_worker()
    policy = make_policy(worker=worker)
    trigger = make_trigger()
    claim = make_claim(
        worker=worker,
        policy=policy,
        trigger=trigger,
        status=ClaimStatus.PAID,
        payment_ref="RZP-pay_pending_0001",
        paid_at=None,
    )
    txn = make_payout_txn(
        claim=claim,
        status=PayoutTransactionStatus.PENDING,
        razorpay_payment_id="pay_pending_0001",
        upi_ref="UTR-SHOULD-STAY-HIDDEN",
    )

    payload = build_explainability_payload(claim, policy, trigger, payout_txn=txn)

    assert payload.payment_status == "pending"
    assert payload.payment_ref is None


def test_explainability_recent_activity_defaults_to_unknown_for_legacy_claim(
    make_worker, make_policy, make_trigger, make_claim
):
    worker = make_worker()
    policy = make_policy(worker=worker)
    trigger = make_trigger()
    claim = make_claim(worker=worker, policy=policy, trigger=trigger)
    claim.recent_activity_valid = True
    claim.recent_activity_reason = None
    claim.recent_activity_at = None

    payload = build_explainability_payload(claim, policy, trigger)

    assert payload.recent_activity_status == "unknown"


def test_explainability_location_unavailable_stays_unknown_not_matched(
    make_worker, make_policy, make_trigger, make_claim
):
    worker = make_worker()
    policy = make_policy(worker=worker)
    trigger = make_trigger()
    claim = make_claim(worker=worker, policy=policy, trigger=trigger, gps_valid=True)
    claim.claim_lat = None
    claim.claim_lng = None

    payload = build_explainability_payload(claim, policy, trigger)

    assert payload.zone_match_status == "unknown"


def test_explainability_formula_mentions_cap_when_applied(
    make_worker, make_policy, make_trigger, make_claim
):
    worker = make_worker()
    policy = make_policy(worker=worker, tier="Basic Shield")
    trigger = make_trigger()
    claim = make_claim(worker=worker, policy=policy, trigger=trigger, payout_amount=320.0)

    payload = build_explainability_payload(
        claim=claim,
        policy=policy,
        trigger_event=trigger,
        eligibility_ctx={
            "formula_base_amount": 600.0,
            "formula_rate": 0.7,
            "payout_cap_applied": True,
        },
    )

    assert payload.payout_cap_applied is True
    assert "capped" in payload.payout_formula_text.lower()


def test_explainability_zero_payout_uses_clear_formula_text(
    make_worker, make_policy, make_trigger, make_claim
):
    worker = make_worker()
    policy = make_policy(worker=worker)
    trigger = make_trigger()
    claim = make_claim(worker=worker, policy=policy, trigger=trigger, payout_amount=0.0, paid_at=None, payment_ref=None)

    payload = build_explainability_payload(claim, policy, trigger)

    assert payload.payout_amount == 0.0
    assert payload.payout_formula_text == "No payout approved for this claim."


def test_explainability_appeal_disabled_after_deadline(
    make_worker, make_policy, make_trigger, make_claim
):
    worker = make_worker()
    policy = make_policy(worker=worker)
    trigger = make_trigger()
    claim = make_claim(
        worker=worker,
        policy=policy,
        trigger=trigger,
        status=ClaimStatus.REJECTED,
        paid_at=None,
        payment_ref=None,
    )
    claim.created_at = datetime.utcnow() - timedelta(hours=72)

    payload = build_explainability_payload(
        claim,
        policy,
        trigger,
        now=datetime.utcnow(),
    )

    assert payload.appeal_deadline is not None
    assert payload.appeal_allowed is False


def test_explainability_uses_claim_time_formula_context_over_current_policy_tier(
    make_worker, make_policy, make_trigger, make_claim
):
    worker = make_worker()
    policy = make_policy(worker=worker, tier="Elite Shield")
    trigger = make_trigger()
    claim = make_claim(worker=worker, policy=policy, trigger=trigger, payout_amount=350.0)

    payload = build_explainability_payload(
        claim=claim,
        policy=policy,
        trigger_event=trigger,
        eligibility_ctx={"claim_time_plan_tier": "Basic Shield"},
    )

    assert "Basic Shield" in payload.payout_formula_text
    assert "Elite Shield" not in payload.payout_formula_text


def test_explainability_policy_inactive_reason_beats_live_trigger_validity(
    make_worker, make_policy, make_trigger, make_claim
):
    worker = make_worker()
    policy = make_policy(worker=worker, status=PolicyStatus.EXPIRED)
    trigger = make_trigger(measured_value=91.0, threshold_value=64.5)
    claim = make_claim(
        worker=worker,
        policy=policy,
        trigger=trigger,
        status=ClaimStatus.REJECTED,
        paid_at=None,
        payment_ref=None,
    )

    payload = build_explainability_payload(claim, policy, trigger)

    assert payload.threshold_result == "met"
    assert payload.reason_code == "policy_inactive"
