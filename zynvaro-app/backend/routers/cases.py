"""
routers/cases.py
=================
Worker-facing Case & Appeals API (Phase 1 MVP).

Endpoints
---------
GET  /claims/{claim_id}/appeal-eligibility  — pre-check before showing Appeal button
POST /claims/{claim_id}/appeal              — submit structured appeal (replaces old confirm+prompt)
POST /cases                                 — submit generic grievance
GET  /cases                                 — list worker's own cases
GET  /cases/{id}                            — full case detail + messages
POST /cases/{id}/messages                   — worker reply to a WAITING_FOR_WORKER request
"""
from __future__ import annotations

from datetime import datetime
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, field_validator
from sqlalchemy.orm import Session

from database import get_db
from models import (
    Claim, GrievanceCase, GrievanceMessage, GrievanceDecision,
    CaseStatus, CaseType, DecisionType,
    APPEAL_REASON_CODES, GRIEVANCE_REASON_CODES,
)
from routers.auth import get_current_worker
from models import Worker
from services.grievance_service import (
    generate_case_id,
    check_appeal_eligibility,
    acknowledge_case,
    mark_triaged,
    triage_case,
    compute_sla_due_at,
    is_sla_breached,
    emit_audit_event,
)

router = APIRouter(tags=["Cases & Appeals"])


# ─── Pydantic schemas ────────────────────────────────────────────

class AppealSubmitRequest(BaseModel):
    category_code: str
    worker_summary_text: str

    @field_validator("worker_summary_text")
    @classmethod
    def _min_length(cls, v):
        if len(v.strip()) < 10:
            raise ValueError("Summary must be at least 10 characters.")
        return v.strip()

    @field_validator("category_code")
    @classmethod
    def _valid_code(cls, v):
        all_codes = set(APPEAL_REASON_CODES) | set(GRIEVANCE_REASON_CODES)
        if v not in all_codes:
            raise ValueError(f"Unknown category_code: {v!r}")
        return v


class GrievanceCaseCreate(BaseModel):
    category_code: str
    subcategory_code: Optional[str] = None
    worker_summary_text: str
    linked_claim_id: Optional[int] = None
    linked_policy_id: Optional[int] = None
    channel_origin: str = "APP"

    @field_validator("worker_summary_text")
    @classmethod
    def _min_length(cls, v):
        if len(v.strip()) < 10:
            raise ValueError("Summary must be at least 10 characters.")
        return v.strip()

    @field_validator("category_code")
    @classmethod
    def _valid_code(cls, v):
        if v not in GRIEVANCE_REASON_CODES:
            raise ValueError(f"Unknown grievance category_code: {v!r}")
        return v


class WorkerMessageRequest(BaseModel):
    body_text: str

    @field_validator("body_text")
    @classmethod
    def _min_length(cls, v):
        if len(v.strip()) < 5:
            raise ValueError("Message must be at least 5 characters.")
        return v.strip()


class GrievanceMessageOut(BaseModel):
    id: int
    sender_type: str
    body_text: str
    created_at: datetime
    visible_to_worker: bool

    class Config:
        from_attributes = True


class GrievanceDecisionOut(BaseModel):
    decision_type: str
    decision_reason_code: str
    worker_visible_text: str
    decision_time: datetime

    class Config:
        from_attributes = True


class GrievanceCaseOut(BaseModel):
    id: int
    public_case_id: str
    case_type: str
    category_code: str
    subcategory_code: Optional[str]
    status: str
    priority: str
    sla_due_at: Optional[datetime]
    sla_breached: bool
    worker_summary_text: Optional[str]
    linked_claim_id: Optional[int]
    linked_policy_id: Optional[int]
    created_at: datetime
    resolved_at: Optional[datetime]
    latest_reason_code: Optional[str]
    messages: List[GrievanceMessageOut] = []
    decision: Optional[GrievanceDecisionOut] = None

    class Config:
        from_attributes = True


# ─── Helpers ─────────────────────────────────────────────────────

def _get_case_or_404(case_id: int, worker_id: int, db: Session) -> GrievanceCase:
    case = db.query(GrievanceCase).filter(GrievanceCase.id == case_id).first()
    if not case:
        raise HTTPException(status_code=404, detail="Case not found.")
    if case.worker_id != worker_id:
        raise HTTPException(status_code=403, detail="Not your case.")
    return case


def _build_case_out(case: GrievanceCase) -> GrievanceCaseOut:
    msgs = [m for m in (case.messages or []) if m.visible_to_worker]
    latest_decision = case.decisions[-1] if case.decisions else None
    return GrievanceCaseOut(
        id=case.id,
        public_case_id=case.public_case_id,
        case_type=case.case_type,
        category_code=case.category_code,
        subcategory_code=case.subcategory_code,
        status=case.status,
        priority=case.priority,
        sla_due_at=case.sla_due_at,
        sla_breached=is_sla_breached(case),
        worker_summary_text=case.worker_summary_text,
        linked_claim_id=case.linked_claim_id,
        linked_policy_id=case.linked_policy_id,
        created_at=case.created_at,
        resolved_at=case.resolved_at,
        latest_reason_code=case.latest_reason_code,
        messages=[GrievanceMessageOut.model_validate(m) for m in msgs],
        decision=(
            GrievanceDecisionOut.model_validate(latest_decision)
            if latest_decision else None
        ),
    )


def _create_case_row(
    *,
    db: Session,
    worker_id: int,
    case_type: str,
    category_code: str,
    subcategory_code: Optional[str],
    worker_summary_text: str,
    linked_claim_id: Optional[int],
    linked_policy_id: Optional[int],
    channel_origin: str,
) -> GrievanceCase:
    now = datetime.utcnow()
    case = GrievanceCase(
        public_case_id=generate_case_id(),
        worker_id=worker_id,
        case_type=case_type,
        category_code=category_code,
        subcategory_code=subcategory_code,
        worker_summary_text=worker_summary_text,
        linked_claim_id=linked_claim_id,
        linked_policy_id=linked_policy_id,
        channel_origin=channel_origin,
        status=CaseStatus.SUBMITTED,
        sla_due_at=compute_sla_due_at(now),
    )
    db.add(case)
    db.flush()  # get case.id

    # Immediate acknowledgement (spec §9 — acknowledgement SLA: immediate)
    acknowledge_case(case)

    # Auto-triage
    team = triage_case(case)
    mark_triaged(case, team)

    # System message visible to worker
    triage_label = {
        "AUTO":         "Your case is being reviewed automatically.",
        "OPS":          "Your case has been routed to our Operations team.",
        "CLAIM_REVIEW": "Your case is being reviewed by our Claims team.",
        "INSURER":      "Your case has been escalated for formal review.",
    }.get(team, "Your case is under review.")

    msg = GrievanceMessage(
        case_id=case.id,
        sender_type="SYSTEM",
        body_text=(
            f"Your case {case.public_case_id} has been received and is {triage_label} "
            f"We aim to resolve it within 72 hours."
        ),
        visible_to_worker=True,
    )
    db.add(msg)

    emit_audit_event(
        db,
        case=case,
        event_type="CASE_CREATED",
        actor_type="WORKER",
        actor_id=worker_id,
        new_value={"status": case.status, "team": team, "category": category_code},
    )
    return case


# ─── Endpoints ───────────────────────────────────────────────────

@router.get("/claims/{claim_id}/appeal-eligibility")
def get_appeal_eligibility(
    claim_id: int,
    db: Session = Depends(get_db),
    current_worker: Worker = Depends(get_current_worker),
):
    """Check whether the given claim is currently appealable."""
    claim = db.query(Claim).filter(
        Claim.id == claim_id, Claim.worker_id == current_worker.id
    ).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found.")

    # Check for existing open case
    open_case = db.query(GrievanceCase).filter(
        GrievanceCase.linked_claim_id == claim_id,
        GrievanceCase.worker_id == current_worker.id,
        GrievanceCase.status.notin_([
            CaseStatus.CLOSED,
            CaseStatus.CLOSED_EXPIRED,
            CaseStatus.RESOLVED_UPHELD,
            CaseStatus.RESOLVED_REVERSED,
            CaseStatus.RESOLVED_PARTIAL,
        ]),
    ).first()

    result = check_appeal_eligibility(
        claim,
        existing_open_case_id=open_case.id if open_case else None,
    )
    return result


@router.post("/claims/{claim_id}/appeal", response_model=GrievanceCaseOut, status_code=201)
def submit_appeal(
    claim_id: int,
    req: AppealSubmitRequest,
    db: Session = Depends(get_db),
    current_worker: Worker = Depends(get_current_worker),
):
    """
    Submit a structured appeal for a specific claim.
    Creates a GrievanceCase and updates claim.appeal_status.
    """
    claim = db.query(Claim).filter(
        Claim.id == claim_id, Claim.worker_id == current_worker.id
    ).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found.")

    # Check eligibility
    open_case = db.query(GrievanceCase).filter(
        GrievanceCase.linked_claim_id == claim_id,
        GrievanceCase.worker_id == current_worker.id,
        GrievanceCase.status.notin_([
            CaseStatus.CLOSED, CaseStatus.CLOSED_EXPIRED,
            CaseStatus.RESOLVED_UPHELD, CaseStatus.RESOLVED_REVERSED,
            CaseStatus.RESOLVED_PARTIAL,
        ]),
    ).first()

    elig = check_appeal_eligibility(
        claim, existing_open_case_id=open_case.id if open_case else None
    )
    if not elig["eligible"]:
        raise HTTPException(status_code=422, detail=elig["reason"])

    # Create case
    case = _create_case_row(
        db=db,
        worker_id=current_worker.id,
        case_type=CaseType.APPEAL,
        category_code=req.category_code,
        subcategory_code=None,
        worker_summary_text=req.worker_summary_text,
        linked_claim_id=claim_id,
        linked_policy_id=claim.policy_id,
        channel_origin="APP",
    )

    # Update claim.appeal_status for backward compat
    claim.appeal_status = "initiated"
    claim.appeal_reason = req.worker_summary_text
    claim.appealed_at = datetime.utcnow()

    db.commit()
    db.refresh(case)
    return _build_case_out(case)


@router.post("/cases", response_model=GrievanceCaseOut, status_code=201)
def submit_grievance(
    req: GrievanceCaseCreate,
    db: Session = Depends(get_db),
    current_worker: Worker = Depends(get_current_worker),
):
    """Submit a generic grievance not tied to a specific claim."""
    case = _create_case_row(
        db=db,
        worker_id=current_worker.id,
        case_type=CaseType.GRIEVANCE,
        category_code=req.category_code,
        subcategory_code=req.subcategory_code,
        worker_summary_text=req.worker_summary_text,
        linked_claim_id=req.linked_claim_id,
        linked_policy_id=req.linked_policy_id,
        channel_origin=req.channel_origin,
    )
    db.commit()
    db.refresh(case)
    return _build_case_out(case)


@router.get("/cases", response_model=List[GrievanceCaseOut])
def list_my_cases(
    db: Session = Depends(get_db),
    current_worker: Worker = Depends(get_current_worker),
):
    """List all cases belonging to the authenticated worker."""
    cases = (
        db.query(GrievanceCase)
        .filter(GrievanceCase.worker_id == current_worker.id)
        .order_by(GrievanceCase.created_at.desc())
        .all()
    )
    return [_build_case_out(c) for c in cases]


@router.get("/cases/{case_id}", response_model=GrievanceCaseOut)
def get_case(
    case_id: int,
    db: Session = Depends(get_db),
    current_worker: Worker = Depends(get_current_worker),
):
    """Get full case detail including worker-visible messages and latest decision."""
    case = _get_case_or_404(case_id, current_worker.id, db)
    return _build_case_out(case)


@router.post("/cases/{case_id}/messages", status_code=201)
def add_worker_message(
    case_id: int,
    req: WorkerMessageRequest,
    db: Session = Depends(get_db),
    current_worker: Worker = Depends(get_current_worker),
):
    """Worker replies to a WAITING_FOR_WORKER request."""
    case = _get_case_or_404(case_id, current_worker.id, db)
    if case.status != CaseStatus.WAITING_FOR_WORKER:
        raise HTTPException(
            status_code=422,
            detail="Messages can only be added when the case is awaiting your response.",
        )
    msg = GrievanceMessage(
        case_id=case.id,
        sender_type="WORKER",
        sender_id=current_worker.id,
        body_text=req.body_text,
        visible_to_worker=True,
    )
    db.add(msg)
    # Move case back to triage
    case.status = CaseStatus.TRIAGED

    emit_audit_event(
        db, case=case, event_type="WORKER_REPLIED",
        actor_type="WORKER", actor_id=current_worker.id,
        new_value={"message_length": len(req.body_text)},
    )
    db.commit()
    return {"detail": "Message added. Your case is back in review."}
