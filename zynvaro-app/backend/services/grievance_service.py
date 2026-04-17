"""
services/grievance_service.py
==============================
Pure business logic for the Grievance & Appeals system (Phase 1 MVP).

No HTTP dependency — called from routers. Fully unit-testable in isolation.
"""
from __future__ import annotations

import json
import random
import string
from datetime import datetime, timedelta
from typing import Optional

from models import (
    Claim, GrievanceCase, GrievanceMessage, GrievanceAuditEvent,
    CaseStatus, CaseType, CasePriority, TriageQueue, DecisionType,
    APPEAL_REASON_CODES, GRIEVANCE_REASON_CODES,
    ClaimStatus,
)

# ─── Constants ────────────────────────────────────────────────────
APPEAL_WINDOW_HOURS     = 48      # from claim created_at
GRIEVANCE_WINDOW_DAYS   = 90      # post-policy period
MAX_REOPEN_COUNT        = 2       # further reopens need supervisor
SLA_DUE_HOURS           = 72      # ordinary resolution SLA


# ─────────────────────────────────────────────────────────────────
# CASE ID GENERATOR
# ─────────────────────────────────────────────────────────────────

def generate_case_id() -> str:
    """Return a human-readable case ID like GRV-2026-AB12CD."""
    year = datetime.utcnow().year
    suffix = "".join(random.choices(string.ascii_uppercase + string.digits, k=6))
    return f"GRV-{year}-{suffix}"


# ─────────────────────────────────────────────────────────────────
# APPEAL ELIGIBILITY CHECK
# ─────────────────────────────────────────────────────────────────

def check_appeal_eligibility(
    claim: Claim,
    *,
    existing_open_case_id: Optional[int] = None,
    now: Optional[datetime] = None,
) -> dict:
    """
    Return {eligible, reason_code, reason, window_expires_at, category_options}.

    Rules (spec §2):
    - claim must not already have an open appeal
    - 48h window from claim.created_at
    - non-appealable statuses are blocked
    """
    now = now or datetime.utcnow()
    window_expires_at = claim.created_at + timedelta(hours=APPEAL_WINDOW_HOURS)

    # Already has an open grievance case
    if existing_open_case_id:
        return {
            "eligible": False,
            "reason_code": "CASE_ALREADY_OPEN",
            "reason": "An appeal or grievance for this claim is already open.",
            "window_expires_at": window_expires_at,
            "existing_case_id": existing_open_case_id,
            "category_options": [],
        }

    # Non-appealable statuses
    if claim.status in (ClaimStatus.PENDING_REVIEW,):
        # Pending review → can appeal on MANUAL_REVIEW_DELAY grounds
        pass  # allowed

    # 48h window check
    if now > window_expires_at:
        return {
            "eligible": False,
            "reason_code": "CASE_WINDOW_EXPIRED",
            "reason": f"The 48-hour appeal window expired at {window_expires_at.strftime('%d %b %Y %H:%M UTC')}.",
            "window_expires_at": window_expires_at,
            "existing_case_id": None,
            "category_options": [],
        }

    # Build pre-filled category options based on claim deny reason
    category_options = _infer_category_options(claim)

    return {
        "eligible": True,
        "reason_code": "ELIGIBLE",
        "reason": "This claim is within the 48-hour appeal window.",
        "window_expires_at": window_expires_at,
        "existing_case_id": None,
        "category_options": category_options,
    }


def _infer_category_options(claim: Claim) -> list:
    """
    Pre-select relevant appeal reason codes based on claim denial signals.
    Returns ordered list of {code, label} dicts.
    """
    options = []

    # Waiting period was the block
    wdc = getattr(claim, "waiting_reason_code", None)
    if wdc and "WAITING" in wdc:
        options.append({
            "code": "WAITING_PERIOD_DISPUTE",
            "label": APPEAL_REASON_CODES["WAITING_PERIOD_DISPUTE"],
        })

    # Activity gate failed
    if not getattr(claim, "recent_activity_valid", True):
        options.append({
            "code": "RECENT_ACTIVITY_DISPUTE",
            "label": APPEAL_REASON_CODES["RECENT_ACTIVITY_DISPUTE"],
        })

    # GPS / zone invalid
    if not getattr(claim, "gps_valid", True):
        options.append({
            "code": "ZONE_MISMATCH_DISPUTE",
            "label": APPEAL_REASON_CODES["ZONE_MISMATCH_DISPUTE"],
        })

    # Payout failed
    if claim.status in ("payout_failed", ClaimStatus.MANUAL_REVIEW):
        options.append({
            "code": "PAYOUT_FAILED_AFTER_APPROVAL",
            "label": APPEAL_REASON_CODES["PAYOUT_FAILED_AFTER_APPROVAL"],
        })

    # Fallback — show all
    if not options:
        options = [
            {"code": k, "label": v}
            for k, v in APPEAL_REASON_CODES.items()
        ]

    return options


# ─────────────────────────────────────────────────────────────────
# TRIAGE ENGINE  (spec §8)
# ─────────────────────────────────────────────────────────────────

def triage_case(
    case: GrievanceCase,
    *,
    claim: Optional[Claim] = None,
    now: Optional[datetime] = None,
) -> str:
    """
    Return assigned_team (TriageQueue.*).

    Priority order:
    1. Window expired                    → AUTO
    2. Payout failed after approval      → OPS
    3. Manual review SLA breached        → OPS
    4. Source/zone/activity/waiting dispute → CLAIM_REVIEW
    5. Insurer-grade / formal escalation → INSURER
    6. Anything else                     → OPS (safe default)
    """
    now = now or datetime.utcnow()
    code = case.category_code

    # SLA breached → escalate priority automatically
    if case.sla_due_at and now > case.sla_due_at:
        case.priority = CasePriority.URGENT

    # Expired window — can be auto-resolved
    if code == "CASE_WINDOW_EXPIRED":
        return TriageQueue.AUTO

    # High reopen / severity → insurer escalation (checked first to override others)
    if case.severity in ("CRITICAL", "HIGH") or case.reopen_count >= MAX_REOPEN_COUNT:
        return TriageQueue.INSURER

    # Payment operations
    if code in ("PAYOUT_FAILED_AFTER_APPROVAL", "PREMIUM_DEBIT_ISSUE", "RENEWAL_ISSUE"):
        return TriageQueue.OPS

    # Manual review delay — ops matter
    if code == "MANUAL_REVIEW_DELAY":
        return TriageQueue.OPS

    # Claim review disputes (signal-level)
    claim_disputes = {
        "SOURCE_VALUE_DISPUTE", "SOURCE_TIME_WINDOW_DISPUTE",
        "ZONE_MISMATCH_DISPUTE", "SHIFT_OVERLAP_DISPUTE",
        "RECENT_ACTIVITY_DISPUTE", "WAITING_PERIOD_DISPUTE",
        "DUPLICATE_CLAIM_DISPUTE", "WRONG_TRIGGER_CLASSIFICATION",
    }
    if code in claim_disputes:
        return TriageQueue.CLAIM_REVIEW

    return TriageQueue.OPS  # safe default


# ─────────────────────────────────────────────────────────────────
# SLA HELPERS
# ─────────────────────────────────────────────────────────────────

def compute_sla_due_at(created_at: datetime) -> datetime:
    return created_at + timedelta(hours=SLA_DUE_HOURS)


def is_sla_breached(case: GrievanceCase, now: Optional[datetime] = None) -> bool:
    now = now or datetime.utcnow()
    if not case.sla_due_at:
        return False
    still_open = case.status not in (
        CaseStatus.RESOLVED_UPHELD, CaseStatus.RESOLVED_REVERSED,
        CaseStatus.RESOLVED_PARTIAL, CaseStatus.CLOSED, CaseStatus.CLOSED_EXPIRED,
    )
    return still_open and now > case.sla_due_at


# ─────────────────────────────────────────────────────────────────
# CASE STATE TRANSITIONS (enforced here, not in the router)
# ─────────────────────────────────────────────────────────────────

def acknowledge_case(case: GrievanceCase) -> None:
    now = datetime.utcnow()
    case.status = CaseStatus.ACKNOWLEDGED
    case.acknowledged_at = now


def mark_triaged(case: GrievanceCase, team: str) -> None:
    case.status = CaseStatus.TRIAGED
    case.assigned_team = team
    case.triaged_at = datetime.utcnow()


def resolve_case(case: GrievanceCase, decision_type: str) -> None:
    now = datetime.utcnow()
    status_map = {
        DecisionType.UPHOLD:  CaseStatus.RESOLVED_UPHELD,
        DecisionType.REVERSE: CaseStatus.RESOLVED_REVERSED,
        DecisionType.PARTIAL: CaseStatus.RESOLVED_PARTIAL,
        DecisionType.NON_APPEALABLE_CLOSED: CaseStatus.CLOSED_EXPIRED,
    }
    case.status = status_map.get(decision_type, CaseStatus.RESOLVED_UPHELD)
    case.resolved_at = now


def reopen_case(case: GrievanceCase) -> dict:
    if case.reopen_count >= MAX_REOPEN_COUNT:
        return {
            "allowed": False,
            "reason": f"Reopen limit ({MAX_REOPEN_COUNT}) reached. Supervisor approval required.",
        }
    case.reopen_count += 1
    case.status = CaseStatus.REOPENED
    return {"allowed": True, "reason": f"Case reopened (attempt {case.reopen_count}/{MAX_REOPEN_COUNT})."}


# ─────────────────────────────────────────────────────────────────
# CLAIM SNAPSHOT BUILDER
# ─────────────────────────────────────────────────────────────────

def build_claim_snapshot(
    claim: Claim,
    trigger_event,
    eligibility: dict,
) -> dict:
    """
    Serialize all decision inputs to immutable JSON dict.
    Called at claim creation to populate ClaimSnapshot.
    """
    decision = {
        "claim_id": claim.id,
        "status": claim.status,
        "auto_processed": claim.auto_processed,
        "authenticity_score": claim.authenticity_score,
        "risk_tier": getattr(claim, "risk_tier", None),
        "ml_fraud_probability": getattr(claim, "ml_fraud_probability", None),
        "fraud_flags": claim.fraud_flags,
        "is_simulated": claim.is_simulated,
    }

    source = {
        "trigger_type": trigger_event.trigger_type,
        "city": trigger_event.city,
        "measured_value": trigger_event.measured_value,
        "threshold_value": trigger_event.threshold_value,
        "unit": trigger_event.unit,
        "source_primary": getattr(trigger_event, "source_primary", None),
        "confidence_score": getattr(trigger_event, "confidence_score", None),
        "source_log": getattr(trigger_event, "source_log", None),
    }

    elig = {
        "gps_valid": claim.gps_valid,
        "activity_valid": claim.activity_valid,
        "device_valid": claim.device_valid,
        "recent_activity_valid": getattr(claim, "recent_activity_valid", None),
        "recent_activity_reason": getattr(claim, "recent_activity_reason", None),
        "waiting_decision": getattr(claim, "waiting_decision", None),
        "waiting_reason_code": getattr(claim, "waiting_reason_code", None),
        "cooling_off_cleared": getattr(claim, "cooling_off_cleared", None),
        "shift_valid": getattr(claim, "shift_valid", True),
        "zone_match": eligibility.get("zone_match"),
        "effective_city": eligibility.get("effective_city"),
        "location_source": eligibility.get("location_source"),
    }

    payout = {
        "payout_amount": claim.payout_amount,
        "policy_tier": claim.policy.tier if claim.policy else None,
        "max_daily_payout": claim.policy.max_daily_payout if claim.policy else None,
        "max_weekly_payout": claim.policy.max_weekly_payout if claim.policy else None,
    }

    return {
        "decision_snapshot_json": json.dumps(decision),
        "source_snapshot_json": json.dumps(source),
        "eligibility_snapshot_json": json.dumps(elig),
        "payout_formula_snapshot_json": json.dumps(payout),
    }


def persist_claim_snapshot(claim: Claim, trigger_event, eligibility: dict, db) -> None:
    """Create and commit a ClaimSnapshot row for the given claim."""
    from models import ClaimSnapshot
    snaps = build_claim_snapshot(claim, trigger_event, eligibility)
    snap = ClaimSnapshot(
        claim_id=claim.id,
        **snaps,
    )
    db.add(snap)
    # do NOT commit here — caller controls the transaction


# ─────────────────────────────────────────────────────────────────
# AUDIT EVENT HELPER
# ─────────────────────────────────────────────────────────────────

def emit_audit_event(
    db,
    *,
    case: GrievanceCase,
    event_type: str,
    actor_type: str,
    actor_id: Optional[int] = None,
    entity_type: str = "CASE",
    entity_id: Optional[int] = None,
    old_value: Optional[dict] = None,
    new_value: Optional[dict] = None,
) -> None:
    """Create a GrievanceAuditEvent row. Caller commits."""
    evt = GrievanceAuditEvent(
        case_id=case.id,
        entity_type=entity_type,
        entity_id=entity_id or case.id,
        event_type=event_type,
        actor_type=actor_type,
        actor_id=actor_id,
        old_value_json=json.dumps(old_value) if old_value else None,
        new_value_json=json.dumps(new_value) if new_value else None,
    )
    db.add(evt)
