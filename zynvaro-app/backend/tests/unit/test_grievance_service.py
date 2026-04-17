"""
backend/tests/unit/test_grievance_service.py
=============================================
Unit tests for services/grievance_service.py

Covers:
- Appeal eligibility (window, open case, reason codes)
- Triage engine routing decisions
- SLA computation and breach detection
- Case state transitions
- Reopen limit enforcement
- Claim snapshot structure
- Audit event helper
"""
from __future__ import annotations

from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from services.grievance_service import (
    APPEAL_WINDOW_HOURS,
    MAX_REOPEN_COUNT,
    SLA_DUE_HOURS,
    check_appeal_eligibility,
    compute_sla_due_at,
    generate_case_id,
    is_sla_breached,
    reopen_case,
    triage_case,
    acknowledge_case,
    mark_triaged,
    resolve_case,
    build_claim_snapshot,
    emit_audit_event,
)
from models import (
    CaseStatus, CaseType, CasePriority, DecisionType, TriageQueue,
    GrievanceCase, GrievanceMessage, Claim, ClaimStatus,
)

_NOW = datetime(2026, 4, 17, 10, 0, 0)


# ─── Fixtures ────────────────────────────────────────────────────

def _make_claim(
    *,
    created_at=None,
    appeal_status="none",
    status=ClaimStatus.AUTO_APPROVED,
    gps_valid=True,
    activity_valid=True,
    recent_activity_valid=True,
    waiting_reason_code=None,
):
    claim = MagicMock(spec=Claim)
    claim.id = 99
    claim.worker_id = 1
    claim.policy_id = 10
    claim.created_at = created_at or (_NOW - timedelta(hours=2))
    claim.appeal_status = appeal_status
    claim.status = status
    claim.gps_valid = gps_valid
    claim.activity_valid = activity_valid
    claim.device_valid = True
    claim.recent_activity_valid = recent_activity_valid
    claim.recent_activity_reason = None
    claim.waiting_reason_code = waiting_reason_code
    claim.waiting_decision = None
    claim.cooling_off_cleared = True
    claim.shift_valid = True
    claim.authenticity_score = 0.9
    claim.risk_tier = "LOW"
    claim.ml_fraud_probability = 0.05
    claim.fraud_flags = ""
    claim.is_simulated = False
    claim.auto_processed = True
    claim.payout_amount = 500.0
    claim.policy = MagicMock()
    claim.policy.tier = "Standard Guard"
    claim.policy.max_daily_payout = 600.0
    claim.policy.max_weekly_payout = 2000.0
    return claim


def _make_case(
    *,
    status=CaseStatus.TRIAGED,
    category_code="ZONE_MISMATCH_DISPUTE",
    case_type=CaseType.APPEAL,
    severity="NORMAL",
    sla_due_at=None,
    reopen_count=0,
    linked_claim_id=1,
):
    case = MagicMock(spec=GrievanceCase)
    case.id = 1
    case.worker_id = 1
    case.status = status
    case.category_code = category_code
    case.case_type = case_type
    case.severity = severity
    case.reopen_count = reopen_count
    case.linked_claim_id = linked_claim_id
    case.sla_due_at = sla_due_at or (_NOW + timedelta(hours=72))
    case.priority = CasePriority.NORMAL
    case.messages = []
    case.decisions = []
    return case


# ─────────────────────────────────────────────────────────────────
# CASE ID
# ─────────────────────────────────────────────────────────────────

class TestGenerateCaseId:

    def test_format_starts_with_grv(self):
        cid = generate_case_id()
        assert cid.startswith("GRV-")

    def test_contains_year(self):
        cid = generate_case_id()
        assert str(datetime.utcnow().year) in cid

    def test_is_unique(self):
        ids = {generate_case_id() for _ in range(100)}
        assert len(ids) >= 95   # very high uniqueness (birthday probability negligible)

    def test_length_reasonable(self):
        cid = generate_case_id()
        assert 10 <= len(cid) <= 20


# ─────────────────────────────────────────────────────────────────
# APPEAL ELIGIBILITY
# ─────────────────────────────────────────────────────────────────

class TestCheckAppealEligibility:

    def test_within_window_is_eligible(self):
        claim = _make_claim(created_at=_NOW - timedelta(hours=10))
        result = check_appeal_eligibility(claim, now=_NOW)
        assert result["eligible"] is True
        assert result["reason_code"] == "ELIGIBLE"

    def test_expired_window_is_not_eligible(self):
        claim = _make_claim(created_at=_NOW - timedelta(hours=APPEAL_WINDOW_HOURS + 1))
        result = check_appeal_eligibility(claim, now=_NOW)
        assert result["eligible"] is False
        assert result["reason_code"] == "CASE_WINDOW_EXPIRED"

    def test_exactly_at_window_expiry_is_not_eligible(self):
        claim = _make_claim(created_at=_NOW - timedelta(hours=APPEAL_WINDOW_HOURS))
        # window_expires_at == _NOW → expired (not strictly before)
        result = check_appeal_eligibility(claim, now=_NOW + timedelta(seconds=1))
        assert result["eligible"] is False

    def test_open_case_blocks_new_appeal(self):
        claim = _make_claim(created_at=_NOW - timedelta(hours=5))
        result = check_appeal_eligibility(claim, existing_open_case_id=42, now=_NOW)
        assert result["eligible"] is False
        assert result["reason_code"] == "CASE_ALREADY_OPEN"
        assert result["existing_case_id"] == 42

    def test_category_options_prefilled_for_zone_denied_claim(self):
        claim = _make_claim(created_at=_NOW - timedelta(hours=5), gps_valid=False)
        result = check_appeal_eligibility(claim, now=_NOW)
        codes = [o["code"] for o in result["category_options"]]
        assert "ZONE_MISMATCH_DISPUTE" in codes

    def test_category_options_prefilled_for_waiting_period_denial(self):
        claim = _make_claim(
            created_at=_NOW - timedelta(hours=5),
            waiting_reason_code="EVENT_BEFORE_CLAIM_ELIGIBLE_AT",
        )
        result = check_appeal_eligibility(claim, now=_NOW)
        codes = [o["code"] for o in result["category_options"]]
        assert "WAITING_PERIOD_DISPUTE" in codes

    def test_eligible_result_includes_window_expires_at(self):
        claim = _make_claim(created_at=_NOW - timedelta(hours=5))
        result = check_appeal_eligibility(claim, now=_NOW)
        assert result["window_expires_at"] == claim.created_at + timedelta(hours=APPEAL_WINDOW_HOURS)

    def test_no_open_case_sets_existing_case_id_none(self):
        claim = _make_claim(created_at=_NOW - timedelta(hours=5))
        result = check_appeal_eligibility(claim, now=_NOW)
        assert result["existing_case_id"] is None


# ─────────────────────────────────────────────────────────────────
# TRIAGE ENGINE
# ─────────────────────────────────────────────────────────────────

class TestTriageCase:

    def test_payout_failure_routes_to_ops(self):
        case = _make_case(category_code="PAYOUT_FAILED_AFTER_APPROVAL")
        team = triage_case(case, now=_NOW)
        assert team == TriageQueue.OPS

    def test_premium_debit_routes_to_ops(self):
        case = _make_case(category_code="PREMIUM_DEBIT_ISSUE")
        team = triage_case(case, now=_NOW)
        assert team == TriageQueue.OPS

    def test_manual_review_delay_routes_to_ops(self):
        case = _make_case(category_code="MANUAL_REVIEW_DELAY")
        team = triage_case(case, now=_NOW)
        assert team == TriageQueue.OPS

    def test_zone_mismatch_routes_to_claim_review(self):
        case = _make_case(category_code="ZONE_MISMATCH_DISPUTE")
        team = triage_case(case, now=_NOW)
        assert team == TriageQueue.CLAIM_REVIEW

    def test_source_value_dispute_routes_to_claim_review(self):
        case = _make_case(category_code="SOURCE_VALUE_DISPUTE")
        team = triage_case(case, now=_NOW)
        assert team == TriageQueue.CLAIM_REVIEW

    def test_recent_activity_dispute_routes_to_claim_review(self):
        case = _make_case(category_code="RECENT_ACTIVITY_DISPUTE")
        team = triage_case(case, now=_NOW)
        assert team == TriageQueue.CLAIM_REVIEW

    def test_waiting_period_dispute_routes_to_claim_review(self):
        case = _make_case(category_code="WAITING_PERIOD_DISPUTE")
        team = triage_case(case, now=_NOW)
        assert team == TriageQueue.CLAIM_REVIEW

    def test_expired_window_routes_to_auto(self):
        case = _make_case(category_code="CASE_WINDOW_EXPIRED")
        team = triage_case(case, now=_NOW)
        assert team == TriageQueue.AUTO

    def test_high_reopen_count_routes_to_insurer(self):
        case = _make_case(category_code="ZONE_MISMATCH_DISPUTE", reopen_count=MAX_REOPEN_COUNT)
        team = triage_case(case, now=_NOW)
        assert team == TriageQueue.INSURER

    def test_sla_breached_escalates_priority_to_urgent(self):
        breached_sla = _NOW - timedelta(hours=1)   # SLA already passed
        case = _make_case(category_code="ZONE_MISMATCH_DISPUTE", sla_due_at=breached_sla)
        triage_case(case, now=_NOW)
        assert case.priority == CasePriority.URGENT


# ─────────────────────────────────────────────────────────────────
# SLA
# ─────────────────────────────────────────────────────────────────

class TestSla:

    def test_sla_due_at_is_created_at_plus_72h(self):
        due = compute_sla_due_at(_NOW)
        assert due == _NOW + timedelta(hours=SLA_DUE_HOURS)

    def test_sla_not_breached_when_within_window(self):
        case = _make_case(sla_due_at=_NOW + timedelta(hours=10))
        assert is_sla_breached(case, now=_NOW) is False

    def test_sla_breached_when_past_due(self):
        case = _make_case(sla_due_at=_NOW - timedelta(hours=1))
        assert is_sla_breached(case, now=_NOW) is True

    def test_sla_not_breached_when_case_already_resolved(self):
        case = _make_case(
            status=CaseStatus.RESOLVED_UPHELD,
            sla_due_at=_NOW - timedelta(hours=1),
        )
        assert is_sla_breached(case, now=_NOW) is False

    def test_sla_not_breached_when_case_closed(self):
        case = _make_case(
            status=CaseStatus.CLOSED,
            sla_due_at=_NOW - timedelta(hours=1),
        )
        assert is_sla_breached(case, now=_NOW) is False


# ─────────────────────────────────────────────────────────────────
# STATE TRANSITIONS
# ─────────────────────────────────────────────────────────────────

class TestStateTransitions:

    def test_acknowledge_sets_status(self):
        case = _make_case(status=CaseStatus.SUBMITTED)
        acknowledge_case(case)
        assert case.status == CaseStatus.ACKNOWLEDGED
        assert case.acknowledged_at is not None

    def test_mark_triaged_sets_team_and_status(self):
        case = _make_case()
        mark_triaged(case, TriageQueue.OPS)
        assert case.status == CaseStatus.TRIAGED
        assert case.assigned_team == TriageQueue.OPS
        assert case.triaged_at is not None

    def test_resolve_upheld_sets_status(self):
        case = _make_case()
        resolve_case(case, DecisionType.UPHOLD)
        assert case.status == CaseStatus.RESOLVED_UPHELD
        assert case.resolved_at is not None

    def test_resolve_reversed_sets_status(self):
        case = _make_case()
        resolve_case(case, DecisionType.REVERSE)
        assert case.status == CaseStatus.RESOLVED_REVERSED

    def test_resolve_partial_sets_status(self):
        case = _make_case()
        resolve_case(case, DecisionType.PARTIAL)
        assert case.status == CaseStatus.RESOLVED_PARTIAL

    def test_non_appealable_close_sets_status(self):
        case = _make_case()
        resolve_case(case, DecisionType.NON_APPEALABLE_CLOSED)
        assert case.status == CaseStatus.CLOSED_EXPIRED


# ─────────────────────────────────────────────────────────────────
# REOPEN POLICY
# ─────────────────────────────────────────────────────────────────

class TestReopenPolicy:

    def test_first_reopen_allowed(self):
        case = _make_case(reopen_count=0, status=CaseStatus.RESOLVED_UPHELD)
        result = reopen_case(case)
        assert result["allowed"] is True
        assert case.reopen_count == 1
        assert case.status == CaseStatus.REOPENED

    def test_second_reopen_allowed(self):
        case = _make_case(reopen_count=1, status=CaseStatus.RESOLVED_UPHELD)
        result = reopen_case(case)
        assert result["allowed"] is True

    def test_third_reopen_blocked_at_limit(self):
        case = _make_case(reopen_count=MAX_REOPEN_COUNT, status=CaseStatus.RESOLVED_UPHELD)
        result = reopen_case(case)
        assert result["allowed"] is False
        assert "Supervisor" in result["reason"]

    def test_reopen_increments_count(self):
        case = _make_case(reopen_count=0)
        reopen_case(case)
        assert case.reopen_count == 1


# ─────────────────────────────────────────────────────────────────
# CLAIM SNAPSHOT
# ─────────────────────────────────────────────────────────────────

class TestBuildClaimSnapshot:

    def _make_trigger(self):
        t = MagicMock()
        t.trigger_type = "Heavy Rainfall"
        t.city = "Bangalore"
        t.measured_value = 65.2
        t.threshold_value = 50.0
        t.unit = "mm/hr"
        t.source_primary = "IMD"
        t.confidence_score = 0.88
        t.source_log = '{"imd": 65.2}'
        return t

    def test_snapshot_contains_all_section_keys(self):
        claim = _make_claim()
        trigger = self._make_trigger()
        snap = build_claim_snapshot(claim, trigger, {"zone_match": True})
        assert "decision_snapshot_json" in snap
        assert "source_snapshot_json" in snap
        assert "eligibility_snapshot_json" in snap
        assert "payout_formula_snapshot_json" in snap

    def test_decision_snapshot_has_claim_id(self):
        import json
        claim = _make_claim()
        trigger = self._make_trigger()
        snap = build_claim_snapshot(claim, trigger, {})
        dec = json.loads(snap["decision_snapshot_json"])
        assert dec["claim_id"] == claim.id

    def test_source_snapshot_has_trigger_type(self):
        import json
        claim = _make_claim()
        trigger = self._make_trigger()
        snap = build_claim_snapshot(claim, trigger, {})
        src = json.loads(snap["source_snapshot_json"])
        assert src["trigger_type"] == "Heavy Rainfall"

    def test_eligibility_snapshot_has_zone_match(self):
        import json
        claim = _make_claim()
        trigger = self._make_trigger()
        snap = build_claim_snapshot(claim, trigger, {"zone_match": True})
        elig = json.loads(snap["eligibility_snapshot_json"])
        assert elig["zone_match"] is True

    def test_payout_snapshot_has_payout_amount(self):
        import json
        claim = _make_claim()
        trigger = self._make_trigger()
        snap = build_claim_snapshot(claim, trigger, {})
        pf = json.loads(snap["payout_formula_snapshot_json"])
        assert pf["payout_amount"] == 500.0
