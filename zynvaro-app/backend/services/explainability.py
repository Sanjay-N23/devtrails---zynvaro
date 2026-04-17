from __future__ import annotations

from datetime import datetime, timedelta
from math import isnan
from typing import Any, Optional

from pydantic import BaseModel

from models import Claim, ClaimStatus, Policy, PolicyStatus, TriggerEvent


CLAIM_TIME_SNAPSHOT_VERSION = "v1"
THRESHOLD_EPSILON = 1e-6


class ExplainabilityPayload(BaseModel):
    status_label: str
    trigger_type: Optional[str] = None
    source_label: str
    source_type: str
    source_state: str
    measured_value: Optional[float] = None
    measured_unit: str = "-"
    threshold_value: Optional[float] = None
    threshold_unit: str = "-"
    threshold_result: str
    zone_match_status: str
    shift_overlap_status: str
    recent_activity_status: str
    event_window_start: Optional[datetime] = None
    event_window_end: Optional[datetime] = None
    processed_at: Optional[datetime] = None
    payout_formula_text: str
    payout_amount: float
    payout_cap_applied: bool
    confidence_score: Optional[float] = None
    confidence_band: str
    payment_status: str
    payment_ref: Optional[str] = None
    appeal_allowed: bool
    appeal_deadline: Optional[datetime] = None
    reason_code: str
    reason_text: str
    claim_time_snapshot_version: str = CLAIM_TIME_SNAPSHOT_VERSION


def _safe_float(value: Any) -> Optional[float]:
    if value is None or value == "":
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if isnan(parsed):
        return None
    return parsed


def _confidence_band(score: Optional[float]) -> str:
    parsed = _safe_float(score)
    if parsed is None or parsed < 0 or parsed > 100:
        return "unknown"
    if parsed >= 85:
        return "high"
    if parsed >= 65:
        return "medium"
    if parsed >= 40:
        return "low"
    return "weak"


def _resolve_confidence_score(
    claim: Claim,
    trigger_event: Optional[TriggerEvent],
    source_ctx: Optional[dict],
) -> Optional[float]:
    if source_ctx and "confidence_score" in source_ctx:
        return _safe_float(source_ctx.get("confidence_score"))

    claim_conf = _safe_float(getattr(claim, "trigger_confidence_score", None))
    trigger_conf = _safe_float(getattr(trigger_event, "confidence_score", None)) if trigger_event else None
    has_explicit_evidence = bool(
        getattr(trigger_event, "source_log", None)
        or (source_ctx or {}).get("source_label")
    )

    if claim_conf is not None:
        if trigger_conf is not None and claim_conf == 100.0 and trigger_conf != 100.0:
            return trigger_conf
        if claim_conf == 100.0 and trigger_conf in {None, 100.0} and not has_explicit_evidence:
            return None
        return claim_conf

    if trigger_conf == 100.0 and not has_explicit_evidence:
        return None
    return trigger_conf


def _source_label(trigger_event: Optional[TriggerEvent], source_ctx: Optional[dict]) -> str:
    explicit = (source_ctx or {}).get("source_label")
    if explicit:
        return str(explicit)
    labels = [
        getattr(trigger_event, "source_primary", None),
        getattr(trigger_event, "source_secondary", None),
    ]
    joined = " + ".join([label for label in labels if label])
    return joined or "Source pending"


def _source_type(trigger_event: Optional[TriggerEvent], source_ctx: Optional[dict], confidence: Optional[float]) -> str:
    explicit = (source_ctx or {}).get("source_type")
    if explicit in {"official", "fallback", "provisional"}:
        return explicit

    source_blob = " ".join(
        str(part).lower()
        for part in [
            getattr(trigger_event, "source_primary", None),
            getattr(trigger_event, "source_secondary", None),
            getattr(trigger_event, "source_log", None),
            (source_ctx or {}).get("source_label"),
        ]
        if part
    )
    if any(token in source_blob for token in ("mock", "fallback", "simulation", "simulator", "override", "unavailable")):
        return "fallback"
    if trigger_event and getattr(trigger_event, "is_validated", None) is False:
        return "provisional"
    if _confidence_band(confidence) in {"low", "weak"}:
        return "provisional"
    return "official"


def _source_state(source_type: str, source_ctx: Optional[dict]) -> str:
    if (source_ctx or {}).get("missing"):
        return "missing"
    if (source_ctx or {}).get("archived"):
        return "archived"
    if (source_ctx or {}).get("stale"):
        return "stale"
    if (source_ctx or {}).get("disagrees"):
        return "disputed"
    if source_type == "provisional":
        return "provisional"
    if source_type == "fallback":
        return "fallback_used"
    return "confirmed"


def _threshold_result(
    measured_value: Optional[float],
    threshold_value: Optional[float],
    source_state: str,
) -> str:
    if measured_value is None or threshold_value is None:
        return "unknown"
    if measured_value + THRESHOLD_EPSILON < threshold_value:
        return "not_met"
    if source_state in {"stale", "disputed", "provisional"}:
        return "under_review"
    return "met"


def _zone_match_status(claim: Claim, eligibility_ctx: Optional[dict]) -> str:
    if eligibility_ctx and "zone_match_status" in eligibility_ctx:
        return str(eligibility_ctx["zone_match_status"])
    if eligibility_ctx and eligibility_ctx.get("zone_match") is False:
        return "mismatch"
    if claim.gps_valid is False:
        return "mismatch"
    if claim.claim_lat is None or claim.claim_lng is None:
        return "unknown"
    return "matched"


def _shift_overlap_status(claim: Claim, eligibility_ctx: Optional[dict]) -> str:
    if eligibility_ctx and "shift_overlap_status" in eligibility_ctx:
        return str(eligibility_ctx["shift_overlap_status"])
    if eligibility_ctx and eligibility_ctx.get("shift_overlap") is False:
        return "failed"
    if getattr(claim, "shift_valid", None) is False:
        return "failed"
    if getattr(claim, "shift_valid", None) is True:
        return "passed"
    return "unknown"


def _recent_activity_status(claim: Claim, eligibility_ctx: Optional[dict]) -> str:
    if eligibility_ctx and "recent_activity_status" in eligibility_ctx:
        return str(eligibility_ctx["recent_activity_status"])
    if eligibility_ctx and eligibility_ctx.get("recent_activity_passed") is False:
        return "failed"
    if getattr(claim, "recent_activity_valid", None) is False:
        return "failed"
    if getattr(claim, "recent_activity_valid", None) is True and (
        getattr(claim, "recent_activity_reason", None) or getattr(claim, "recent_activity_at", None)
    ):
        return "passed"
    return "unknown"


def _payment_status(claim: Claim, payout_txn: Any | None) -> str:
    txn_status = str(getattr(payout_txn, "status", "") or "").lower()
    if txn_status == "settled":
        return "paid"
    if txn_status in {"initiated", "pending", "retrying"}:
        return "pending"
    if txn_status == "failed":
        return "failed"
    if txn_status == "reversed":
        return "reversed"
    if claim.paid_at:
        return "paid"
    if claim.status == ClaimStatus.PAID:
        return "pending"
    if claim.status == ClaimStatus.REJECTED:
        return "not_paid"
    if claim.status == ClaimStatus.MANUAL_REVIEW:
        return "manual_review"
    if claim.status == ClaimStatus.PENDING_REVIEW:
        return "pending_review"
    return "unknown"


def _status_label(claim: Claim, payment_status: str) -> str:
    if payment_status == "paid":
        return "Paid"
    if payment_status == "pending" and claim.status == ClaimStatus.PAID:
        return "Approved, payout in progress"
    if payment_status == "failed":
        return "Approved, payout failed"
    if payment_status == "reversed":
        return "Paid, later reversed"
    if claim.status == ClaimStatus.PENDING_REVIEW:
        return "Pending review"
    if claim.status == ClaimStatus.MANUAL_REVIEW:
        return "Manual review"
    if claim.status == ClaimStatus.REJECTED:
        return "Not paid"
    return str(claim.status).replace("_", " ").title()


def _masked_payment_ref(payment_status: str, payout_txn: Any | None, claim: Claim) -> Optional[str]:
    raw_ref = (
        getattr(payout_txn, "upi_ref", None)
        or getattr(payout_txn, "razorpay_payment_id", None)
        or getattr(claim, "payment_ref", None)
    )
    if not raw_ref or payment_status not in {"paid", "reversed"}:
        return None
    text = str(raw_ref)
    if len(text) <= 8:
        return text
    return f"{text[:4]}…{text[-4:]}"


def _appeal_deadline(claim: Claim) -> Optional[datetime]:
    if not claim.created_at:
        return None
    return claim.created_at + timedelta(hours=48)


def _appeal_allowed(claim: Claim, payment_status: str, now: Optional[datetime] = None) -> bool:
    reference_time = now or datetime.utcnow()
    deadline = _appeal_deadline(claim)
    if deadline and reference_time > deadline:
        return False
    if (claim.appeal_status or "none") not in {"none", "resolved_denied", "resolved_paid"}:
        return False
    if payment_status in {"paid"}:
        return False
    return claim.status in {
        ClaimStatus.PENDING_REVIEW,
        ClaimStatus.MANUAL_REVIEW,
        ClaimStatus.REJECTED,
        ClaimStatus.PAID,
    } and payment_status in {"failed", "pending", "pending_review", "manual_review", "not_paid"}


def _formula_text(
    claim: Claim,
    policy: Optional[Policy],
    eligibility_ctx: Optional[dict],
) -> tuple[str, bool]:
    payout_amount = round(float(getattr(claim, "payout_amount", 0.0) or 0.0), 2)
    cap_applied = bool((eligibility_ctx or {}).get("payout_cap_applied"))
    tier_label = (eligibility_ctx or {}).get("claim_time_plan_tier") or getattr(policy, "tier", None)
    base_amount = _safe_float((eligibility_ctx or {}).get("formula_base_amount"))
    replacement_rate = _safe_float((eligibility_ctx or {}).get("formula_rate"))

    if payout_amount <= 0:
        return ("No payout approved for this claim.", cap_applied)

    if base_amount is not None and replacement_rate is not None:
        rate_text = f"{round(replacement_rate * 100, 1):g}%"
        if cap_applied:
            return (
                f"Base payout ₹{base_amount:,.0f} at {rate_text} replacement was capped to ₹{payout_amount:,.0f}.",
                cap_applied,
            )
        return (
            f"Base payout ₹{base_amount:,.0f} at {rate_text} replacement resulted in ₹{payout_amount:,.0f}.",
            cap_applied,
        )

    if cap_applied:
        return (
            f"Approved payout was reduced to ₹{payout_amount:,.0f} after applying the remaining weekly cap.",
            cap_applied,
        )

    if tier_label:
        return (f"Final approved payout recorded at ₹{payout_amount:,.0f} under the {tier_label} claim snapshot.", False)
    return (f"Final approved payout recorded at ₹{payout_amount:,.0f}.", False)


def _reason_from_precedence(
    claim: Claim,
    policy: Optional[Policy],
    payment_status: str,
    source_state: str,
    zone_status: str,
    shift_status: str,
    recent_activity_status: str,
    threshold_result: str,
    eligibility_ctx: Optional[dict],
) -> tuple[str, str]:
    ctx = eligibility_ctx or {}
    reason_text_map = {
        "policy_inactive": "The policy was inactive or expired when this event occurred.",
        "waiting_period_active": "The trigger occurred during the waiting period, so payout was not released.",
        "duplicate_covered": "This event was already covered by a prior claim or duplicate-suppression rule.",
        "zone_mismatch": "The worker location did not match the impacted payout zone.",
        "no_shift_overlap": "The disruption window did not overlap the worker's declared shift.",
        "recent_activity_not_met": "Recent rider activity was not sufficient to release payout.",
        "source_under_review": "The trigger crossed threshold but source confidence is still under review.",
        "payment_operational_issue": "The claim was approved but the payment transfer hit an operational issue.",
        "payout_approved": "The claim passed eligibility checks and payout was approved.",
        "pending_review": "The claim is awaiting automated review before payout can be released.",
        "manual_review": "The claim requires manual review before any payout can be released.",
        "not_paid": "The claim is not paid yet.",
    }

    if ctx.get("reason_code") and ctx.get("reason_text"):
        explicit_code = str(ctx["reason_code"])
        explicit_text = str(ctx["reason_text"])
    else:
        explicit_code = ""
        explicit_text = ""

    checks = [
        ("policy_inactive", bool(ctx.get("policy_active") is False or (policy and policy.status != PolicyStatus.ACTIVE))),
        ("waiting_period_active", bool(ctx.get("waiting_period_active"))),
        ("duplicate_covered", bool(ctx.get("duplicate_covered"))),
        ("zone_mismatch", zone_status == "mismatch"),
        ("no_shift_overlap", shift_status == "failed"),
        ("recent_activity_not_met", recent_activity_status == "failed"),
        ("source_under_review", source_state in {"stale", "disputed", "provisional"} or threshold_result == "under_review"),
        ("payment_operational_issue", payment_status in {"failed", "reversed"}),
        ("payout_approved", payment_status == "paid"),
    ]

    for code, active in checks:
        if active:
            if explicit_code == code and explicit_text:
                return explicit_code, explicit_text
            return code, reason_text_map[code]

    if explicit_code and explicit_text:
        return explicit_code, explicit_text
    if claim.status == ClaimStatus.PENDING_REVIEW:
        return "pending_review", reason_text_map["pending_review"]
    if claim.status == ClaimStatus.MANUAL_REVIEW:
        return "manual_review", reason_text_map["manual_review"]
    return "not_paid", reason_text_map["not_paid"]


def build_explainability_payload(
    claim: Claim,
    policy: Optional[Policy],
    trigger_event: Optional[TriggerEvent],
    payout_txn: Any | None = None,
    eligibility_ctx: Optional[dict] = None,
    source_ctx: Optional[dict] = None,
    now: Optional[datetime] = None,
) -> ExplainabilityPayload:
    measured_value = _safe_float(getattr(trigger_event, "measured_value", None))
    threshold_value = _safe_float(getattr(trigger_event, "threshold_value", None))
    confidence_score = _resolve_confidence_score(claim, trigger_event, source_ctx)
    source_label = _source_label(trigger_event, source_ctx)
    source_type = _source_type(trigger_event, source_ctx, confidence_score)
    source_state = _source_state(source_type, source_ctx)
    threshold_result = _threshold_result(measured_value, threshold_value, source_state)
    zone_status = _zone_match_status(claim, eligibility_ctx)
    shift_status = _shift_overlap_status(claim, eligibility_ctx)
    recent_activity_status = _recent_activity_status(claim, eligibility_ctx)
    payment_status = _payment_status(claim, payout_txn)
    status_label = _status_label(claim, payment_status)
    payout_formula_text, payout_cap_applied = _formula_text(claim, policy, eligibility_ctx)
    reason_code, reason_text = _reason_from_precedence(
        claim=claim,
        policy=policy,
        payment_status=payment_status,
        source_state=source_state,
        zone_status=zone_status,
        shift_status=shift_status,
        recent_activity_status=recent_activity_status,
        threshold_result=threshold_result,
        eligibility_ctx=eligibility_ctx,
    )

    processed_at = (
        getattr(payout_txn, "settled_at", None)
        or getattr(payout_txn, "initiated_at", None)
        or getattr(claim, "paid_at", None)
        or getattr(claim, "created_at", None)
    )

    return ExplainabilityPayload(
        status_label=status_label,
        trigger_type=getattr(trigger_event, "trigger_type", None),
        source_label=source_label,
        source_type=source_type,
        source_state=source_state,
        measured_value=measured_value,
        measured_unit=getattr(trigger_event, "unit", None) or "-",
        threshold_value=threshold_value,
        threshold_unit=getattr(trigger_event, "unit", None) or "-",
        threshold_result=threshold_result,
        zone_match_status=zone_status,
        shift_overlap_status=shift_status,
        recent_activity_status=recent_activity_status,
        event_window_start=getattr(trigger_event, "detected_at", None),
        event_window_end=getattr(trigger_event, "expires_at", None),
        processed_at=processed_at,
        payout_formula_text=payout_formula_text,
        payout_amount=round(float(getattr(claim, "payout_amount", 0.0) or 0.0), 2),
        payout_cap_applied=payout_cap_applied,
        confidence_score=confidence_score,
        confidence_band=_confidence_band(confidence_score),
        payment_status=payment_status,
        payment_ref=_masked_payment_ref(payment_status, payout_txn, claim),
        appeal_allowed=_appeal_allowed(claim, payment_status, now=now),
        appeal_deadline=_appeal_deadline(claim),
        reason_code=reason_code,
        reason_text=reason_text,
    )
