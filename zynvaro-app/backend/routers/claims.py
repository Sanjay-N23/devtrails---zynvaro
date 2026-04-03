from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime

from database import get_db
from models import Claim, TriggerEvent, Policy, Worker, ClaimStatus, PolicyStatus
from routers.auth import get_current_worker

router = APIRouter(prefix="/claims", tags=["Claims Management"])


# ─── Admin dependency ──────────────────────────────────────────
def get_current_admin(worker: Worker = Depends(get_current_worker)):
    """Require the authenticated worker to have admin privileges."""
    if not worker.is_admin:
        raise HTTPException(status_code=403, detail="Admin access required")
    return worker


# ─── Schemas ────────────────────────────────────────────────────
class ClaimResponse(BaseModel):
    id: int
    claim_number: str
    status: str
    payout_amount: float
    authenticity_score: float
    gps_valid: bool
    activity_valid: bool
    device_valid: bool
    cross_source_valid: bool
    fraud_flags: Optional[str]
    auto_processed: bool
    paid_at: Optional[datetime]
    payment_ref: Optional[str]
    created_at: datetime

    # Nested trigger info
    trigger_type: Optional[str] = None
    trigger_city: Optional[str] = None
    trigger_measured_value: Optional[float] = None
    trigger_unit: Optional[str] = None
    trigger_description: Optional[str] = None

    # Nested policy info
    policy_tier: Optional[str] = None

    class Config:
        from_attributes = True

class ClaimStats(BaseModel):
    total_claims: int
    auto_approved: int
    pending_review: int
    manual_review: int
    paid: int
    rejected: int
    total_payout_inr: float
    avg_authenticity_score: float


# ─── Helpers ────────────────────────────────────────────────────
def enrich_claim(claim: Claim) -> ClaimResponse:
    """Add trigger + policy info to claim response."""
    trigger = claim.trigger_event
    policy = claim.policy
    return ClaimResponse(
        id=claim.id,
        claim_number=claim.claim_number,
        status=claim.status,
        payout_amount=claim.payout_amount,
        authenticity_score=claim.authenticity_score,
        gps_valid=claim.gps_valid,
        activity_valid=claim.activity_valid,
        device_valid=claim.device_valid,
        cross_source_valid=claim.cross_source_valid,
        fraud_flags=claim.fraud_flags,
        auto_processed=claim.auto_processed,
        paid_at=claim.paid_at,
        payment_ref=claim.payment_ref,
        created_at=claim.created_at,
        trigger_type=trigger.trigger_type if trigger else None,
        trigger_city=trigger.city if trigger else None,
        trigger_measured_value=trigger.measured_value if trigger else None,
        trigger_unit=trigger.unit if trigger else None,
        trigger_description=trigger.description if trigger else None,
        policy_tier=policy.tier if policy else None,
    )


# ─── Endpoints ──────────────────────────────────────────────────
@router.get("/", response_model=List[ClaimResponse])
def list_my_claims(
    limit: int = 20,
    worker: Worker = Depends(get_current_worker),
    db: Session = Depends(get_db),
):
    """Get all claims for the logged-in worker."""
    claims = (
        db.query(Claim)
        .filter(Claim.worker_id == worker.id)
        .order_by(Claim.created_at.desc())
        .limit(limit)
        .all()
    )
    return [enrich_claim(c) for c in claims]


@router.get("/stats", response_model=ClaimStats)
def my_claim_stats(
    worker: Worker = Depends(get_current_worker),
    db: Session = Depends(get_db),
):
    """Aggregated claim statistics for the worker."""
    claims = db.query(Claim).filter(Claim.worker_id == worker.id).all()

    total_payout = sum((c.payout_amount or 0) for c in claims if c.paid_at)
    avg_score = (
        sum(c.authenticity_score for c in claims) / len(claims) if claims else 0
    )

    return ClaimStats(
        total_claims=len(claims),
        auto_approved=sum(1 for c in claims if c.status == ClaimStatus.AUTO_APPROVED),
        pending_review=sum(1 for c in claims if c.status == ClaimStatus.PENDING_REVIEW),
        manual_review=sum(1 for c in claims if c.status == ClaimStatus.MANUAL_REVIEW),
        paid=sum(1 for c in claims if c.status == ClaimStatus.PAID or c.paid_at),
        rejected=sum(1 for c in claims if c.status == ClaimStatus.REJECTED),
        total_payout_inr=round(total_payout, 2),
        avg_authenticity_score=round(avg_score, 1),
    )


@router.get("/{claim_id}", response_model=ClaimResponse)
def get_claim(
    claim_id: int,
    worker: Worker = Depends(get_current_worker),
    db: Session = Depends(get_db),
):
    """Get a specific claim detail."""
    claim = db.query(Claim).filter(Claim.id == claim_id, Claim.worker_id == worker.id).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    return enrich_claim(claim)


# ─── Admin endpoints ────────────────────────────────────────────
@router.get("/admin/workers")
def admin_all_workers(
    worker: Worker = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """[Admin] Get all workers with their policy and claim summary."""
    from models import Policy
    workers = db.query(Worker).order_by(Worker.id).all()
    result = []
    for w in workers:
        active_policy = db.query(Policy).filter(
            Policy.worker_id == w.id, Policy.status == PolicyStatus.ACTIVE
        ).first()
        result.append({
            "id": w.id,
            "full_name": w.full_name,
            "phone": w.phone,
            "city": w.city,
            "platform": w.platform,
            "active_tier": active_policy.tier if active_policy else None,
            "weekly_premium": active_policy.weekly_premium if active_policy else 0,
            "claim_history_count": w.claim_history_count,
            "zone_risk_score": w.zone_risk_score,
            "registered_at": w.created_at.isoformat(),
        })
    return result


@router.get("/admin/all", response_model=List[ClaimResponse])
def admin_all_claims(
    limit: int = 50,
    worker: Worker = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """[Admin] Get all claims across all workers."""
    claims = db.query(Claim).order_by(Claim.created_at.desc()).limit(limit).all()
    return [enrich_claim(c) for c in claims]


@router.get("/admin/stats")
def admin_stats(
    worker: Worker = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """[Admin] Platform-wide analytics."""
    from sqlalchemy import func
    from models import Policy

    total_workers = db.query(Worker).count()
    active_policies = db.query(Policy).filter(Policy.status == PolicyStatus.ACTIVE).count()
    claims = db.query(Claim).all()
    total_premium = db.query(func.sum(Policy.weekly_premium)).filter(Policy.status == PolicyStatus.ACTIVE).scalar() or 0

    total_payout = sum((c.payout_amount or 0) for c in claims if c.paid_at)
    loss_ratio = (total_payout / (total_premium * 4)) * 100 if total_premium > 0 else 0

    # Claims by trigger type
    trigger_breakdown = {}
    for c in claims:
        if c.trigger_event:
            t = c.trigger_event.trigger_type
            trigger_breakdown[t] = trigger_breakdown.get(t, 0) + 1

    return {
        "total_workers": total_workers,
        "active_policies": active_policies,
        "weekly_premium_collection_inr": round(float(total_premium), 2),
        "total_claims": len(claims),
        # Count both PAID (auto-approved + payment confirmed) and AUTO_APPROVED
        # (auto-approved, payment pending) — both were passed by the ML fraud scorer.
        "auto_approved_claims": sum(1 for c in claims if c.status in (ClaimStatus.AUTO_APPROVED, ClaimStatus.PAID)),
        "total_payout_inr": round(total_payout, 2),
        "loss_ratio_pct": round(loss_ratio, 1),
        "claims_by_trigger": trigger_breakdown,
        "avg_authenticity_score": round(
            sum(c.authenticity_score for c in claims) / len(claims) if claims else 0, 1
        ),
    }


@router.patch("/{claim_id}/approve", response_model=ClaimResponse)
def admin_approve_claim(
    claim_id: int,
    worker: Worker = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """[Admin] Manually approve a PENDING_REVIEW or MANUAL_REVIEW claim → PAID."""
    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim.status not in (ClaimStatus.PENDING_REVIEW, ClaimStatus.MANUAL_REVIEW):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot approve claim with status '{claim.status}'. Only PENDING_REVIEW or MANUAL_REVIEW claims can be approved.",
        )
    claim.status = ClaimStatus.PAID
    claim.paid_at = datetime.utcnow()
    claim.payment_ref = f"MANUAL-UPI-{claim.claim_number}"
    db.commit()
    db.refresh(claim)
    return enrich_claim(claim)


@router.patch("/{claim_id}/reject", response_model=ClaimResponse)
def admin_reject_claim(
    claim_id: int,
    worker: Worker = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """[Admin] Manually reject a PENDING_REVIEW or MANUAL_REVIEW claim → REJECTED."""
    claim = db.query(Claim).filter(Claim.id == claim_id).first()
    if not claim:
        raise HTTPException(status_code=404, detail="Claim not found")
    if claim.status not in (ClaimStatus.PENDING_REVIEW, ClaimStatus.MANUAL_REVIEW):
        raise HTTPException(
            status_code=400,
            detail=f"Cannot reject claim with status '{claim.status}'. Only PENDING_REVIEW or MANUAL_REVIEW claims can be rejected.",
        )
    claim.status = ClaimStatus.REJECTED
    db.commit()
    db.refresh(claim)
    return enrich_claim(claim)
