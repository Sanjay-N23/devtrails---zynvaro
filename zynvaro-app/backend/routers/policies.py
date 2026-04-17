from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime, timedelta
import random, string

from database import get_db
from models import Worker, Policy, PolicyStatus
from routers.auth import get_current_worker
from ml.premium_engine import calculate_premium, TIER_CONFIG
from services.cooling_off import policy_cooling_off_status, get_cooling_off_hours

router = APIRouter(prefix="/policies", tags=["Policy Management"])


# ─── Schemas ────────────────────────────────────────────────────
class PremiumQuoteRequest(BaseModel):
    tier: str
    pincode: Optional[str] = None
    city: Optional[str] = None

class PremiumQuoteResponse(BaseModel):
    tier: str
    base_premium: float
    weekly_premium: float
    max_daily_payout: float
    max_weekly_payout: float
    breakdown: dict
    explanation: list

class CreatePolicyRequest(BaseModel):
    tier: str
    upi_id: Optional[str] = None

class CreateOrderRequest(BaseModel):
    tier: str

class CreateOrderResponse(BaseModel):
    order_id: str
    amount: int        # in paise
    currency: str
    key_id: str        # Razorpay public key for frontend Checkout.js
    tier: str
    weekly_premium: float

class VerifyPaymentRequest(BaseModel):
    razorpay_payment_id: str
    razorpay_order_id: str
    razorpay_signature: str
    tier: str

class PolicyResponse(BaseModel):
    id: int
    policy_number: str
    tier: str
    status: str
    weekly_premium: float
    base_premium: float
    max_daily_payout: float
    max_weekly_payout: float
    zone_loading: float
    seasonal_loading: float
    claim_loading: float
    streak_discount: float
    start_date: datetime
    end_date: Optional[datetime]
    is_renewal: bool = False
    created_at: datetime
    # Cooling-off / waiting-period fields
    cooling_off_active: bool = False
    cooling_off_hours: int = 24
    cooling_off_eligible_at: Optional[datetime] = None
    cooling_off_remaining_hours: Optional[float] = None

    class Config:
        from_attributes = True

class AllTiersQuote(BaseModel):
    worker_city: str
    worker_pincode: str
    zone_risk_score: float
    tiers: List[PremiumQuoteResponse]


# ─── Helpers ────────────────────────────────────────────────────
def generate_policy_number() -> str:
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return f"ZYN-{suffix}"


def _add_cooling_off(policy: Policy) -> dict:
    """Compute cooling-off status and return extra fields for the response."""
    status = policy_cooling_off_status(
        policy.start_date,
        is_renewal=getattr(policy, "is_renewal", False),
    )
    return {
        "cooling_off_active": status["in_cooling_off"],
        "cooling_off_hours": status["cooling_off_hours"],
        "cooling_off_eligible_at": status["eligible_at"],
        "cooling_off_remaining_hours": status["hours_remaining"],
    }


def _policy_response(policy: Policy) -> dict:
    """Build a complete PolicyResponse dict including cooling-off metadata."""
    base = {
        c.name: getattr(policy, c.name)
        for c in policy.__table__.columns
    }
    base.update(_add_cooling_off(policy))
    return base


def expire_stale_policies(db: Session):
    """Transition ACTIVE policies past their end_date to EXPIRED."""
    stale = db.query(Policy).filter(
        Policy.status == PolicyStatus.ACTIVE,
        Policy.end_date < datetime.utcnow(),
    ).all()
    for p in stale:
        p.status = PolicyStatus.EXPIRED
    if stale:
        db.commit()
        print(f"[PolicyExpiry] Expired {len(stale)} stale policies")


# ─── Endpoints ──────────────────────────────────────────────────
@router.get("/quote/all", response_model=AllTiersQuote)
def quote_all_tiers(
    worker: Worker = Depends(get_current_worker),
    db: Session = Depends(get_db),
):
    """Get premium quotes for all 3 tiers for the logged-in worker."""
    tiers_result = []
    for tier_name in ["Basic Shield", "Standard Guard", "Pro Armor"]:
        result = calculate_premium(
            tier=tier_name,
            pincode=worker.pincode,
            city=worker.city,
            claim_history_count=worker.claim_history_count,
            disruption_streak=worker.disruption_streak,
        )
        tiers_result.append(PremiumQuoteResponse(**result))

    return AllTiersQuote(
        worker_city=worker.city,
        worker_pincode=worker.pincode,
        zone_risk_score=worker.zone_risk_score,
        tiers=tiers_result,
    )


@router.post("/quote", response_model=PremiumQuoteResponse)
def quote_premium(
    req: PremiumQuoteRequest,
    worker: Worker = Depends(get_current_worker),
    db: Session = Depends(get_db),
):
    """Get an AI-calculated premium quote for a specific tier."""
    pincode = req.pincode or worker.pincode
    city = req.city or worker.city
    result = calculate_premium(
        tier=req.tier,
        pincode=pincode,
        city=city,
        claim_history_count=worker.claim_history_count,
        disruption_streak=worker.disruption_streak,
    )
    return PremiumQuoteResponse(**result)


@router.post("/", response_model=PolicyResponse, status_code=201)
def create_policy(
    req: CreatePolicyRequest,
    worker: Worker = Depends(get_current_worker),
    db: Session = Depends(get_db),
):
    """Create/activate a new weekly policy for the worker."""
    # Validate tier
    if req.tier not in TIER_CONFIG:
        raise HTTPException(status_code=400, detail=f"Invalid tier. Must be one of: {', '.join(TIER_CONFIG.keys())}")

    # Cancel any existing active policy
    existing = (
        db.query(Policy)
        .filter(Policy.worker_id == worker.id, Policy.status == PolicyStatus.ACTIVE)
        .first()
    )
    if existing:
        existing.status = PolicyStatus.CANCELLED
        existing.end_date = datetime.utcnow()

    # Calculate premium
    pricing = calculate_premium(
        tier=req.tier,
        pincode=worker.pincode,
        city=worker.city,
        claim_history_count=worker.claim_history_count,
        disruption_streak=worker.disruption_streak,
    )
    breakdown = pricing["breakdown"]
    cfg = TIER_CONFIG[req.tier]

    policy = Policy(
        worker_id=worker.id,
        policy_number=generate_policy_number(),
        tier=req.tier,
        status=PolicyStatus.ACTIVE,
        weekly_premium=pricing["weekly_premium"],
        base_premium=pricing["base_premium"],
        max_daily_payout=cfg["max_daily"],
        max_weekly_payout=cfg["max_weekly"],
        zone_loading=breakdown["zone_loading_inr"],
        seasonal_loading=breakdown["seasonal_loading_inr"],
        claim_loading=breakdown["claim_loading_inr"],
        streak_discount=abs(breakdown["streak_discount_inr"]),
        start_date=datetime.utcnow(),
        end_date=datetime.utcnow() + timedelta(days=7),
        is_renewal=False,
    )
    db.add(policy)
    db.commit()
    db.refresh(policy)
    return PolicyResponse(**_policy_response(policy))


@router.get("/active", response_model=Optional[PolicyResponse])
def get_active_policy(
    worker: Worker = Depends(get_current_worker),
    db: Session = Depends(get_db),
):
    """Get the worker's current active policy with cooling-off countdown."""
    expire_stale_policies(db)
    policy = (
        db.query(Policy)
        .filter(Policy.worker_id == worker.id, Policy.status == PolicyStatus.ACTIVE)
        .first()
    )
    if policy is None:
        return None
    return PolicyResponse(**_policy_response(policy))


@router.get("/", response_model=List[PolicyResponse])
def list_policies(
    worker: Worker = Depends(get_current_worker),
    db: Session = Depends(get_db),
):
    """Get all policies for the worker, each with cooling-off status."""
    policies = db.query(Policy).filter(Policy.worker_id == worker.id).order_by(Policy.created_at.desc()).all()
    return [PolicyResponse(**_policy_response(p)) for p in policies]


@router.post("/renew", response_model=PolicyResponse, status_code=201)
def renew_policy(
    worker: Worker = Depends(get_current_worker),
    db: Session = Depends(get_db),
):
    """
    Renew the worker's active policy for another 7-day cycle.
    Extends end_date by 7 days and recalculates premium at current risk factors.
    If no active policy exists, returns 404 — worker must call POST /policies/ instead.
    """
    policy = (
        db.query(Policy)
        .filter(Policy.worker_id == worker.id, Policy.status == PolicyStatus.ACTIVE)
        .first()
    )
    if not policy:
        raise HTTPException(status_code=404, detail="No active policy to renew. Use POST /policies/ to create one.")

    # Recalculate premium at current risk (zone risk may have changed)
    pricing = calculate_premium(
        tier=policy.tier,
        pincode=worker.pincode,
        city=worker.city,
        claim_history_count=worker.claim_history_count,
        disruption_streak=worker.disruption_streak,
    )
    breakdown = pricing["breakdown"]

    # Extend by 7 days from current end_date (not from now — avoids gaps)
    policy.end_date = (policy.end_date or datetime.utcnow()) + timedelta(days=7)
    policy.is_renewal = True       # Mark as renewal — 0h cooling-off
    policy.weekly_premium = pricing["weekly_premium"]
    policy.zone_loading = breakdown["zone_loading_inr"]
    policy.seasonal_loading = breakdown["seasonal_loading_inr"]
    policy.claim_loading = breakdown["claim_loading_inr"]
    policy.streak_discount = abs(breakdown["streak_discount_inr"])

    db.commit()
    db.refresh(policy)
    return PolicyResponse(**_policy_response(policy))


# ─── Razorpay Checkout Flow (Phase 3: Premium Payment Gateway) ────

@router.post("/create-order", response_model=CreateOrderResponse)
def create_order(
    req: CreateOrderRequest,
    worker: Worker = Depends(get_current_worker),
    db: Session = Depends(get_db),
):
    """Create a Razorpay Order for premium payment via Checkout popup."""
    import os, uuid
    from services.payout_service import is_razorpay_configured, create_razorpay_order

    if req.tier not in TIER_CONFIG:
        raise HTTPException(400, f"Invalid tier: {req.tier}")

    pricing = calculate_premium(
        req.tier, worker.pincode, worker.city,
        worker.claim_history_count, worker.disruption_streak,
    )
    premium = pricing["weekly_premium"]
    amount_paise = int(premium * 100)

    if is_razorpay_configured():
        order = create_razorpay_order(
            amount_inr=premium,
            receipt=f"ZYN-PREM-{uuid.uuid4().hex[:8].upper()}",
            notes={"worker_id": str(worker.id), "tier": req.tier, "type": "premium_payment"},
        )
        return CreateOrderResponse(
            order_id=order["id"],
            amount=order["amount"],
            currency=order.get("currency", "INR"),
            key_id=os.getenv("RAZORPAY_KEY_ID", ""),
            tier=req.tier,
            weekly_premium=premium,
        )
    else:
        return CreateOrderResponse(
            order_id="MOCK_ORDER",
            amount=amount_paise,
            currency="INR",
            key_id="",
            tier=req.tier,
            weekly_premium=premium,
        )


@router.post("/verify-payment", response_model=PolicyResponse, status_code=201)
def verify_payment(
    req: VerifyPaymentRequest,
    worker: Worker = Depends(get_current_worker),
    db: Session = Depends(get_db),
):
    """Verify Razorpay payment signature, then activate policy + log transaction."""
    import json
    from services.payout_service import verify_razorpay_signature, create_premium_transaction

    # Verify signature
    try:
        verify_razorpay_signature(req.razorpay_payment_id, req.razorpay_order_id, req.razorpay_signature)
    except Exception as e:
        raise HTTPException(400, f"Payment verification failed: {e}")

    if req.tier not in TIER_CONFIG:
        raise HTTPException(400, f"Invalid tier: {req.tier}")

    # Cancel existing active policy
    existing = db.query(Policy).filter(
        Policy.worker_id == worker.id, Policy.status == PolicyStatus.ACTIVE
    ).first()
    if existing:
        existing.status = PolicyStatus.CANCELLED
        existing.end_date = datetime.utcnow()

    # Calculate premium and create policy
    pricing = calculate_premium(
        req.tier, worker.pincode, worker.city,
        worker.claim_history_count, worker.disruption_streak,
    )
    cfg = TIER_CONFIG[req.tier]
    bkd = pricing["breakdown"]
    import random, string
    pol_num = "ZYN-" + "".join(random.choices(string.ascii_uppercase + string.digits, k=8))

    policy = Policy(
        worker_id=worker.id,
        policy_number=pol_num,
        tier=req.tier,
        status=PolicyStatus.ACTIVE,
        weekly_premium=pricing["weekly_premium"],
        base_premium=pricing["base_premium"],
        max_daily_payout=cfg["max_daily"],
        max_weekly_payout=cfg["max_weekly"],
        zone_loading=bkd["zone_loading_inr"],
        seasonal_loading=bkd["seasonal_loading_inr"],
        claim_loading=bkd["claim_loading_inr"],
        streak_discount=abs(bkd["streak_discount_inr"]),
        start_date=datetime.utcnow(),
        end_date=datetime.utcnow() + timedelta(days=7),
    )
    db.add(policy)
    db.flush()

    # Log transaction
    create_premium_transaction(
        worker_id=worker.id,
        policy_id=policy.id,
        amount=pricing["weekly_premium"],
        razorpay_order_id=req.razorpay_order_id,
        razorpay_payment_id=req.razorpay_payment_id,
        db=db,
    )

    db.commit()
    db.refresh(policy)
    return policy


@router.post("/renew-order", response_model=CreateOrderResponse)
def renew_order(
    worker: Worker = Depends(get_current_worker),
    db: Session = Depends(get_db),
):
    """Create a Razorpay Order for policy renewal payment."""
    import os, uuid
    from services.payout_service import is_razorpay_configured, create_razorpay_order

    policy = db.query(Policy).filter(
        Policy.worker_id == worker.id, Policy.status == PolicyStatus.ACTIVE
    ).first()
    if not policy:
        raise HTTPException(404, "No active policy to renew")

    pricing = calculate_premium(
        policy.tier, worker.pincode, worker.city,
        worker.claim_history_count, worker.disruption_streak,
    )
    premium = pricing["weekly_premium"]

    if is_razorpay_configured():
        order = create_razorpay_order(
            amount_inr=premium,
            receipt=f"ZYN-RENEW-{uuid.uuid4().hex[:8].upper()}",
            notes={"worker_id": str(worker.id), "tier": policy.tier, "type": "renewal_payment"},
        )
        return CreateOrderResponse(
            order_id=order["id"], amount=order["amount"],
            currency=order.get("currency", "INR"),
            key_id=os.getenv("RAZORPAY_KEY_ID", ""),
            tier=policy.tier, weekly_premium=premium,
        )
    else:
        return CreateOrderResponse(
            order_id="MOCK_ORDER", amount=int(premium * 100),
            currency="INR", key_id="",
            tier=policy.tier, weekly_premium=premium,
        )


@router.post("/verify-renewal", response_model=PolicyResponse, status_code=201)
def verify_renewal(
    req: VerifyPaymentRequest,
    worker: Worker = Depends(get_current_worker),
    db: Session = Depends(get_db),
):
    """Verify Razorpay renewal payment, then extend policy + log transaction."""
    from services.payout_service import verify_razorpay_signature, create_premium_transaction

    try:
        verify_razorpay_signature(req.razorpay_payment_id, req.razorpay_order_id, req.razorpay_signature)
    except Exception as e:
        raise HTTPException(400, f"Renewal verification failed: {e}")

    policy = db.query(Policy).filter(
        Policy.worker_id == worker.id, Policy.status == PolicyStatus.ACTIVE
    ).first()
    if not policy:
        raise HTTPException(404, "No active policy to renew")

    # Recalculate premium and extend
    pricing = calculate_premium(
        policy.tier, worker.pincode, worker.city,
        worker.claim_history_count, worker.disruption_streak,
    )
    policy.end_date = (policy.end_date or datetime.utcnow()) + timedelta(days=7)
    policy.weekly_premium = pricing["weekly_premium"]
    bkd = pricing["breakdown"]
    policy.zone_loading = bkd["zone_loading_inr"]
    policy.seasonal_loading = bkd["seasonal_loading_inr"]
    policy.claim_loading = bkd["claim_loading_inr"]
    policy.streak_discount = abs(bkd["streak_discount_inr"])

    # Log transaction
    create_premium_transaction(
        worker_id=worker.id, policy_id=policy.id,
        amount=pricing["weekly_premium"],
        razorpay_order_id=req.razorpay_order_id,
        razorpay_payment_id=req.razorpay_payment_id,
        db=db,
    )

    db.commit()
    db.refresh(policy)
    return policy


@router.get("/risk-profile")
def get_risk_profile(
    worker: Worker = Depends(get_current_worker),
    db: Session = Depends(get_db),
):
    """
    Generate a personalized AI risk profile for the logged-in worker.
    Uses Anthropic Claude (if ANTHROPIC_API_KEY set) or rule-based fallback.
    Returns narrative explanation, key risks, seasonal alert, and premium breakdown.
    """
    from services.risk_explainer import generate_risk_profile

    # Use active policy tier, or Basic Shield if no policy yet
    policy = (
        db.query(Policy)
        .filter(Policy.worker_id == worker.id, Policy.status == "active")
        .first()
    )
    tier = policy.tier if policy else "Basic Shield"

    return generate_risk_profile(
        worker_city=worker.city,
        worker_pincode=worker.pincode,
        worker_platform=worker.platform,
        worker_shift=worker.shift,
        tier=tier,
        claim_history=worker.claim_history_count,
        disruption_streak=worker.disruption_streak,
    )


@router.get("/ml-model-info")
def get_fraud_model_info(
    worker: Worker = Depends(get_current_worker),
):
    """
    Return metadata about the trained ML fraud detection model.
    Useful for the admin panel transparency view.
    """
    from ml.fraud_model import get_model_info
    return get_model_info()


@router.delete("/{policy_id}", status_code=204)
def cancel_policy(
    policy_id: int,
    worker: Worker = Depends(get_current_worker),
    db: Session = Depends(get_db),
):
    """Cancel an active policy."""
    policy = db.query(Policy).filter(Policy.id == policy_id, Policy.worker_id == worker.id).first()
    if not policy:
        raise HTTPException(status_code=404, detail="Policy not found")
    if policy.status != PolicyStatus.ACTIVE:
        raise HTTPException(status_code=400, detail="Policy is not active")

    policy.status = PolicyStatus.CANCELLED
    policy.end_date = datetime.utcnow()
    db.commit()
