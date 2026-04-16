from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta

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
    is_simulated: bool = False
    paid_at: Optional[datetime]
    payment_ref: Optional[str]
    created_at: datetime

    # Advanced fraud metadata (Phase 3)
    claim_lat: Optional[float] = None
    claim_lng: Optional[float] = None
    gps_distance_km: Optional[float] = None
    ml_fraud_probability: Optional[float] = None
    risk_tier: Optional[str] = None
    shift_valid: Optional[bool] = True
    weather_cross_valid: Optional[bool] = True
    velocity_valid: Optional[bool] = True

    # Payout gateway info (Phase 3: Razorpay)
    payout_gateway: Optional[str] = None       # "razorpay" or "mock"
    payout_utr: Optional[str] = None           # Real UTR from Razorpay
    payout_status: Optional[str] = None        # "initiated" / "pending" / "settled" / "failed"

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

class WeeklySummary(BaseModel):
    """Worker dashboard widget — earnings protected + active weekly coverage."""
    earnings_protected_total: float
    earnings_protected_this_week: float
    coverage_remaining_this_week: float
    max_weekly_payout: float
    active_coverage: bool
    tier_name: Optional[str] = None
    days_remaining: int = 0
    weekly_premium: float = 0.0
    claims_this_week: int = 0
    disruptions_this_week: int = 0
    total_premiums_paid: float = 0.0


# ─── Helpers ────────────────────────────────────────────────────
def _get_payout_gateway(claim: Claim) -> Optional[str]:
    """Determine payout gateway from payment_ref prefix."""
    ref = claim.payment_ref or ""
    if ref.startswith("RZP-"):
        return "razorpay"
    elif ref.startswith("MOCK-") or ref.startswith("MANUAL-"):
        return "mock"
    return None

def _get_payout_utr(claim: Claim) -> Optional[str]:
    """Extract UTR from payment reference."""
    ref = claim.payment_ref or ""
    if ref.startswith("RZP-"):
        return ref[4:]  # Strip "RZP-" prefix
    return None

def _get_payout_status(claim: Claim) -> Optional[str]:
    """Get payout status. Check PayoutTransaction if available, else infer from claim."""
    if not claim.paid_at and not claim.payment_ref:
        return None
    # Try to get from PayoutTransaction
    txns = claim.transactions
    if txns:
        latest = sorted(txns, key=lambda t: t.initiated_at or datetime.min, reverse=True)[0]
        return latest.status
    # Infer from claim
    if claim.paid_at:
        return "settled"
    return "pending"

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
        is_simulated=getattr(claim, 'is_simulated', False),
        paid_at=claim.paid_at,
        payment_ref=claim.payment_ref,
        created_at=claim.created_at,
        # Phase 3 advanced fraud metadata
        claim_lat=claim.claim_lat,
        claim_lng=claim.claim_lng,
        gps_distance_km=claim.gps_distance_km,
        ml_fraud_probability=claim.ml_fraud_probability,
        risk_tier=claim.risk_tier,
        shift_valid=claim.shift_valid,
        weather_cross_valid=claim.weather_cross_valid,
        velocity_valid=claim.velocity_valid,
        # Payout gateway info (Phase 3: Razorpay)
        payout_gateway=_get_payout_gateway(claim),
        payout_utr=_get_payout_utr(claim),
        payout_status=_get_payout_status(claim),
        # Nested trigger/policy info
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


@router.get("/my-weekly-summary", response_model=WeeklySummary)
def my_weekly_summary(
    worker: Worker = Depends(get_current_worker),
    db: Session = Depends(get_db),
):
    """Worker dashboard: earnings protected + active weekly coverage metrics."""
    from sqlalchemy import func
    from math import ceil

    # Active policy
    policy = (
        db.query(Policy)
        .filter(Policy.worker_id == worker.id, Policy.status == PolicyStatus.ACTIVE)
        .first()
    )

    # This ISO week's Monday at 00:00
    now = datetime.utcnow()
    weekday = now.weekday()  # 0=Monday
    week_start = datetime(now.year, now.month, now.day) - timedelta(days=weekday)

    # This week's claims
    week_claims = (
        db.query(Claim)
        .filter(Claim.worker_id == worker.id, Claim.created_at >= week_start)
        .all()
    )
    earned_this_week = round(sum((c.payout_amount or 0) for c in week_claims if c.paid_at), 2)

    max_weekly = policy.max_weekly_payout if policy else 0
    coverage_remaining = round(max(0, max_weekly - earned_this_week), 2)

    # Lifetime total payouts
    lifetime_total = db.query(
        func.coalesce(func.sum(Claim.payout_amount), 0)
    ).filter(Claim.worker_id == worker.id, Claim.paid_at.isnot(None)).scalar() or 0

    # Disruptions this week in worker's city
    from models import TriggerEvent
    disruptions = db.query(TriggerEvent).filter(
        TriggerEvent.city == worker.city,
        TriggerEvent.detected_at >= week_start,
    ).count()

    # Estimated total premiums paid
    if policy and policy.start_date:
        weeks_active = max(1, ceil((now - policy.start_date).total_seconds() / (7 * 86400)))
        total_premiums = round(policy.weekly_premium * weeks_active, 2)
    else:
        weeks_active = 0
        total_premiums = 0.0

    # Days remaining on policy
    days_remaining = 0
    if policy and policy.end_date:
        delta = (policy.end_date - now).total_seconds()
        days_remaining = max(0, ceil(delta / 86400))

    return WeeklySummary(
        earnings_protected_total=round(float(lifetime_total), 2),
        earnings_protected_this_week=earned_this_week,
        coverage_remaining_this_week=coverage_remaining,
        max_weekly_payout=max_weekly,
        active_coverage=policy is not None,
        tier_name=policy.tier if policy else None,
        days_remaining=days_remaining,
        weekly_premium=policy.weekly_premium if policy else 0,
        claims_this_week=len(week_claims),
        disruptions_this_week=disruptions,
        total_premiums_paid=total_premiums,
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


@router.get("/admin/transactions")
def admin_transactions(
    limit: int = 50,
    worker: Worker = Depends(get_current_admin),
    db: Session = Depends(get_db),
):
    """[Admin] Complete transaction log (premium payments + claim payouts)."""
    from models import PayoutTransaction
    txns = (
        db.query(PayoutTransaction)
        .order_by(PayoutTransaction.initiated_at.desc())
        .limit(limit)
        .all()
    )
    return [{
        "id": t.id,
        "transaction_type": t.transaction_type,
        "worker_id": t.worker_id,
        "amount": t.amount_requested,
        "amount_settled": t.amount_settled,
        "status": t.status,
        "gateway": t.gateway_name,
        "razorpay_payment_id": t.razorpay_payment_id,
        "razorpay_order_id": t.razorpay_order_id,
        "internal_txn_id": t.internal_txn_id,
        "upi_ref": t.upi_ref,
        "initiated_at": t.initiated_at.isoformat() if t.initiated_at else None,
        "settled_at": t.settled_at.isoformat() if t.settled_at else None,
    } for t in txns]


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
    # Phase 3: Razorpay payout (or mock fallback)
    try:
        from services.payout_service import initiate_payout
        claim_worker = db.query(Worker).filter(Worker.id == claim.worker_id).first()
        initiate_payout(claim, claim_worker, db)
    except Exception as e:
        claim.paid_at = datetime.utcnow()
        claim.payment_ref = f"MANUAL-UPI-{claim.claim_number}"
        print(f"[Payout] Admin approve payout error, using mock: {e}")
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
