"""
routers/admin_cases.py
=======================
Admin-only Case Management API (Phase 1 MVP).  Requires is_admin=True.

Endpoints
---------
GET  /admin/cases              — list all cases with filters
GET  /admin/cases/{id}         — full detail + claim snapshot
POST /admin/cases/{id}/triage  — set team / priority
POST /admin/cases/{id}/request-info  — send WAITING_FOR_WORKER message
POST /admin/cases/{id}/resolve       — uphold / reverse / partial (mandatory note)
POST /admin/cases/{id}/escalate      — → WAITING_FOR_INSURER
POST /admin/cases/{id}/reopen        — reopen closed case
POST /admin/cases/{id}/override-claim — update claim status
POST /admin/cases/{id}/retry-payout   — re-trigger payout on linked claim
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from database import get_db
from models import (
    Claim, ClaimSnapshot, GrievanceCase, GrievanceMessage, GrievanceDecision,
    CaseStatus, CaseType, CasePriority, DecisionType, TriageQueue,
    ClaimStatus, Worker,
)
from routers.auth import get_current_worker
from services.grievance_service import (
    mark_triaged, resolve_case, reopen_case, is_sla_breached,
    emit_audit_event,
)

router = APIRouter(prefix="/admin/cases", tags=["Admin — Cases"])


# ─── Admin guard ─────────────────────────────────────────────────

def get_admin(current_worker: Worker = Depends(get_current_worker)) -> Worker:
    if not getattr(current_worker, "is_admin", False):
        raise HTTPException(status_code=403, detail="Admin access required.")
    return current_worker


# ─── Schemas ─────────────────────────────────────────────────────

class AdminTriageRequest(BaseModel):
    assigned_team: str          # TriageQueue.*
    priority: Optional[str] = None
    internal_note: Optional[str] = None


class AdminRequestInfoRequest(BaseModel):
    body_text: str

    @field_validator("body_text")
    @classmethod
    def _min(cls, v):
        if len(v.strip()) < 10:
            raise ValueError("body_text must be at least 10 characters.")
        return v.strip()


class AdminResolveRequest(BaseModel):
    decision_type: str          # DecisionType.*
    decision_reason_code: str
    worker_visible_text: str
    internal_note: str          # mandatory minimum 20 chars
    payout_retry_required: bool = False
    claim_override_action: Optional[str] = None   # APPROVE / REJECT / NONE

    @field_validator("internal_note")
    @classmethod
    def _note_length(cls, v):
        if len(v.strip()) < 20:
            raise ValueError("internal_note must be at least 20 characters.")
        return v.strip()

    @field_validator("decision_type")
    @classmethod
    def _valid_type(cls, v):
        valid = {DecisionType.UPHOLD, DecisionType.REVERSE, DecisionType.PARTIAL,
                 DecisionType.NON_APPEALABLE_CLOSED, DecisionType.REQUEST_INFO}
        if v not in valid:
            raise ValueError(f"Unknown decision_type: {v!r}")
        return v


class AdminEscalateRequest(BaseModel):
    reason: str
    internal_note: str


class AdminReopenRequest(BaseModel):
    reason: str


class AdminOverrideClaimRequest(BaseModel):
    claim_override_action: str   # APPROVE / REJECT
    internal_note: str

    @field_validator("claim_override_action")
    @classmethod
    def _valid(cls, v):
        if v not in ("APPROVE", "REJECT"):
            raise ValueError("claim_override_action must be APPROVE or REJECT")
        return v


class AdminCaseSummary(BaseModel):
    id: int
    public_case_id: str
    case_type: str
    category_code: str
    status: str
    priority: str
    assigned_team: Optional[str]
    worker_id: int
    linked_claim_id: Optional[int]
    sla_due_at: Optional[datetime]
    sla_breached: bool
    reopen_count: int
    created_at: datetime
    resolved_at: Optional[datetime]

    class Config:
        from_attributes = True


class AdminCaseDetail(AdminCaseSummary):
    subcategory_code: Optional[str]
    worker_summary_text: Optional[str]
    internal_summary_text: Optional[str]
    latest_reason_code: Optional[str]
    closed_at: Optional[datetime]
    # Enriched at runtime
    claim_snapshot: Optional[dict] = None
    messages: list = []
    decisions: list = []


# ─── Helpers ─────────────────────────────────────────────────────

def _get_case(case_id: int, db: Session) -> GrievanceCase:
    case = db.query(GrievanceCase).filter(GrievanceCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
    return case


def _load_snapshot(case: GrievanceCase, db: Session) -> Optional[dict]:
    if not case.linked_claim_id:
        return None
    snap = db.query(ClaimSnapshot).filter(
        ClaimSnapshot.claim_id == case.linked_claim_id
    ).first()
    if not snap:
        return None
    return {
        "decision": _safe_json(snap.decision_snapshot_json),
        "source": _safe_json(snap.source_snapshot_json),
        "eligibility": _safe_json(snap.eligibility_snapshot_json),
        "payout_formula": _safe_json(snap.payout_formula_snapshot_json),
        "created_at": snap.created_at.isoformat() if snap.created_at else None,
    }


def _safe_json(text: Optional[str]) -> Optional[dict]:
    if not text:
        return None
    try:
        return json.loads(text)
    except Exception:
        return {"raw": text}


def _build_detail(case: GrievanceCase, db: Session) -> AdminCaseDetail:
    msgs = [
        {
            "id": m.id, "sender_type": m.sender_type,
            "body_text": m.body_text, "visible_to_worker": m.visible_to_worker,
            "created_at": m.created_at.isoformat(),
        }
        for m in (case.messages or [])
    ]
    decisions = [
        {
            "decision_type": d.decision_type,
            "decision_reason_code": d.decision_reason_code,
            "worker_visible_text": d.worker_visible_text,
            "internal_note": d.internal_note,
            "decided_by": d.decided_by,
            "payout_retry_required": d.payout_retry_required,
            "decision_time": d.decision_time.isoformat(),
        }
        for d in (case.decisions or [])
    ]
    return AdminCaseDetail(
        id=case.id,
        public_case_id=case.public_case_id,
        case_type=case.case_type,
        category_code=case.category_code,
        subcategory_code=case.subcategory_code,
        status=case.status,
        priority=case.priority,
        assigned_team=case.assigned_team,
        worker_id=case.worker_id,
        linked_claim_id=case.linked_claim_id,
        linked_policy_id=getattr(case, "linked_policy_id", None),
        sla_due_at=case.sla_due_at,
        sla_breached=is_sla_breached(case),
        reopen_count=case.reopen_count,
        created_at=case.created_at,
        resolved_at=case.resolved_at,
        closed_at=case.closed_at,
        worker_summary_text=case.worker_summary_text,
        internal_summary_text=case.internal_summary_text,
        latest_reason_code=case.latest_reason_code,
        claim_snapshot=_load_snapshot(case, db),
        messages=msgs,
        decisions=decisions,
    )


# ─── Endpoints ───────────────────────────────────────────────────

@router.get("/", response_model=List[AdminCaseSummary])
def list_all_cases(
    status: Optional[str] = Query(None),
    case_type: Optional[str] = Query(None),
    team: Optional[str] = Query(None),
    limit: int = Query(50, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
    admin: Worker = Depends(get_admin),
):
    """List all cases with optional filters."""
    q = db.query(GrievanceCase)
    if status:
        q = q.filter(GrievanceCase.status == status)
    if case_type:
        q = q.filter(GrievanceCase.case_type == case_type)
    if team:
        q = q.filter(GrievanceCase.assigned_team == team)
    cases = q.order_by(GrievanceCase.created_at.desc()).offset(offset).limit(limit).all()
    return [
        AdminCaseSummary(
            id=c.id, public_case_id=c.public_case_id,
            case_type=c.case_type, category_code=c.category_code,
            status=c.status, priority=c.priority,
            assigned_team=c.assigned_team, worker_id=c.worker_id,
            linked_claim_id=c.linked_claim_id, sla_due_at=c.sla_due_at,
            sla_breached=is_sla_breached(c), reopen_count=c.reopen_count,
            created_at=c.created_at, resolved_at=c.resolved_at,
        )
        for c in cases
    ]


@router.get("/{case_id}", response_model=AdminCaseDetail)
def get_case_detail(
    case_id: int,
    db: Session = Depends(get_db),
    admin: Worker = Depends(get_admin),
):
    """Full case detail with claim snapshot (admin-only fields)."""
    case = _get_case(case_id, db)
    return _build_detail(case, db)


@router.post("/{case_id}/triage", status_code=200)
def admin_triage(
    case_id: int,
    req: AdminTriageRequest,
    db: Session = Depends(get_db),
    admin: Worker = Depends(get_admin),
):
    case = _get_case(case_id, db)
    old_team = case.assigned_team
    mark_triaged(case, req.assigned_team)
    if req.priority:
        case.priority = req.priority
    if req.internal_note:
        case.internal_summary_text = req.internal_note
    emit_audit_event(
        db, case=case, event_type="TRIAGED", actor_type="ADMIN",
        actor_id=admin.id,
        old_value={"team": old_team},
        new_value={"team": req.assigned_team, "priority": case.priority},
    )
    db.commit()
    return {"detail": f"Case routed to team={req.assigned_team}."}


@router.post("/{case_id}/request-info", status_code=200)
def request_worker_info(
    case_id: int,
    req: AdminRequestInfoRequest,
    db: Session = Depends(get_db),
    admin: Worker = Depends(get_admin),
):
    case = _get_case(case_id, db)
    msg = GrievanceMessage(
        case_id=case.id,
        sender_type="SUPPORT",
        sender_id=admin.id,
        body_text=req.body_text,
        visible_to_worker=True,
    )
    db.add(msg)
    case.status = CaseStatus.WAITING_FOR_WORKER
    emit_audit_event(
        db, case=case, event_type="INFO_REQUESTED", actor_type="ADMIN",
        actor_id=admin.id, new_value={"msg": req.body_text[:100]},
    )
    db.commit()
    return {"detail": "Worker information request sent."}


@router.post("/{case_id}/resolve", status_code=200)
def admin_resolve(
    case_id: int,
    req: AdminResolveRequest,
    db: Session = Depends(get_db),
    admin: Worker = Depends(get_admin),
):
    """Resolve a case. If REVERSE + payout_retry_required, re-initiate payout."""
    case = _get_case(case_id, db)
    old_status = case.status

    # Record decision
    decision = GrievanceDecision(
        case_id=case.id,
        decision_type=req.decision_type,
        decision_reason_code=req.decision_reason_code,
        worker_visible_text=req.worker_visible_text,
        internal_note=req.internal_note,
        decided_by=admin.id,
        payout_retry_required=req.payout_retry_required,
        claim_override_action=req.claim_override_action,
    )
    db.add(decision)

    # Worker-visible resolution message
    resolution_msg = GrievanceMessage(
        case_id=case.id,
        sender_type="SUPPORT",
        sender_id=admin.id,
        body_text=req.worker_visible_text,
        visible_to_worker=True,
    )
    db.add(resolution_msg)

    # State transition
    resolve_case(case, req.decision_type)
    case.latest_reason_code = req.decision_reason_code
    case.closed_at = datetime.utcnow()

    # Claim override (REVERSE)
    if req.claim_override_action == "APPROVE" and case.linked_claim_id:
        claim = db.query(Claim).filter(Claim.id == case.linked_claim_id).first()
        if claim:
            claim.status = ClaimStatus.AUTO_APPROVED
            claim.appeal_status = "resolved_paid"
            # Trigger payout retry
            if req.payout_retry_required:
                try:
                    from services.payout_service import initiate_payout
                    worker = db.query(Worker).filter(
                        Worker.id == case.worker_id
                    ).first()
                    if worker:
                        initiate_payout(claim, worker, db)
                except Exception as e:
                    # Log but don't fail the resolution — ops can retry manually
                    resolution_msg.body_text += f"\n\n⚠ Payout retry failed: {e}"
    elif req.claim_override_action == "REJECT" and case.linked_claim_id:
        claim = db.query(Claim).filter(Claim.id == case.linked_claim_id).first()
        if claim:
            claim.appeal_status = "resolved_denied"

    emit_audit_event(
        db, case=case, event_type="RESOLVED", actor_type="ADMIN",
        actor_id=admin.id,
        old_value={"status": old_status},
        new_value={
            "status": case.status,
            "decision_type": req.decision_type,
            "reason_code": req.decision_reason_code,
            "payout_retry": req.payout_retry_required,
        },
    )
    db.commit()
    return {
        "detail": f"Case resolved: {req.decision_type}.",
        "public_case_id": case.public_case_id,
        "status": case.status,
    }


@router.post("/{case_id}/escalate", status_code=200)
def admin_escalate(
    case_id: int,
    req: AdminEscalateRequest,
    db: Session = Depends(get_db),
    admin: Worker = Depends(get_admin),
):
    case = _get_case(case_id, db)
    case.status = CaseStatus.WAITING_FOR_INSURER
    case.priority = CasePriority.HIGH
    msg = GrievanceMessage(
        case_id=case.id, sender_type="OPS", sender_id=admin.id,
        body_text=(
            "Your case has been escalated for formal review. "
            "You will be notified when a decision is reached."
        ),
        visible_to_worker=True,
    )
    db.add(msg)
    emit_audit_event(
        db, case=case, event_type="ESCALATED_TO_INSURER",
        actor_type="ADMIN", actor_id=admin.id,
        new_value={"reason": req.reason},
    )
    db.commit()
    return {"detail": "Case escalated to insurer queue."}


@router.post("/{case_id}/reopen", status_code=200)
def admin_reopen(
    case_id: int,
    req: AdminReopenRequest,
    db: Session = Depends(get_db),
    admin: Worker = Depends(get_admin),
):
    case = _get_case(case_id, db)
    result = reopen_case(case)
    if not result["allowed"]:
        raise HTTPException(status_code=422, detail=result["reason"])
    msg = GrievanceMessage(
        case_id=case.id, sender_type="SUPPORT", sender_id=admin.id,
        body_text=f"Your case has been reopened. Reason: {req.reason}",
        visible_to_worker=True,
    )
    db.add(msg)
    emit_audit_event(
        db, case=case, event_type="REOPENED", actor_type="ADMIN",
        actor_id=admin.id, new_value={"reopen_count": case.reopen_count, "reason": req.reason},
    )
    db.commit()
    return {"detail": result["reason"]}


@router.post("/{case_id}/retry-payout", status_code=200)
def admin_retry_payout(
    case_id: int,
    db: Session = Depends(get_db),
    admin: Worker = Depends(get_admin),
):
    """Manually trigger payout retry on the linked claim."""
    case = _get_case(case_id, db)
    if not case.linked_claim_id:
        raise HTTPException(status_code=422, detail="No linked claim on this case.")
    claim = db.query(Claim).filter(Claim.id == case.linked_claim_id).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Linked claim not found.")
    worker = db.query(Worker).filter(Worker.id == case.worker_id).first()
    if not worker:
        raise HTTPException(status_code=404, detail="Worker not found.")
    try:
        from services.payout_service import initiate_payout
        initiate_payout(claim, worker, db)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    emit_audit_event(
        db, case=case, event_type="PAYOUT_RETRIED", actor_type="ADMIN",
        actor_id=admin.id, new_value={"claim_id": claim.id},
    )
    db.commit()
    return {"detail": "Payout retry initiated."}
