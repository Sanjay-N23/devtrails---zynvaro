"""
services/demo_payment_service.py
================================
Implements the Demo Payment Bypass endpoint logic for hackathon judges.
As mandated by the Antigravity Spec, this allows skipping payment gateways 
(like Razorpay) if they fail in sandbox mode, while robustly tracking 
the bypass event as DEMO_SETTLED instead of mingling it with real 
production settlement flows.
"""
from datetime import datetime, timedelta
import os
import random
import string

from sqlalchemy.orm import Session
from fastapi import HTTPException

from models import (
    Worker, Policy, PolicyStatus, PayoutTransaction, 
    PayoutTransactionStatus, TransactionType
)
from ml.premium_engine import calculate_premium, TIER_CONFIG

def generate_policy_number() -> str:
    suffix = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
    return f"ZYN-{suffix}"

def is_demo_mode_active() -> bool:
    """Check if the environment allows payment bypass."""
    # Strict fallback ensuring it never permits bypass in formal "production"
    env = os.getenv("ENVIRONMENT", "development").lower()
    if env == "production" and os.getenv("MOCK_PAYMENTS") != "1":
        return False
    # Normal heuristic: either demo env or explicit mock override
    if env == "demo" or os.getenv("MOCK_PAYMENTS") == "1":
        return True
    return False

def complete_demo_payment_bypass(
    db: Session,
    worker: Worker,
    tier: str,
    order_id: str,
    source_screen: str,
    original_provider_error: str,
    is_renewal: bool = False
) -> dict:
    """
    Simulates a successful payment checkout, creates/renews the relevant policy,
    and logs the transaction safely under DEMO_SETTLED status.
    """
    if not is_demo_mode_active():
        raise HTTPException(
            status_code=403, 
            detail="Demo payment bypass disabled. Environment strictly requires real payment."
        )

    if tier not in TIER_CONFIG:
        raise HTTPException(status_code=400, detail=f"Invalid tier constraint: {tier}")

    # Determine amount & pricing
    pricing = calculate_premium(
        tier=tier,
        pincode=worker.pincode,
        city=worker.city,
        claim_history_count=worker.claim_history_count,
        disruption_streak=worker.disruption_streak,
    )
    premium = pricing["weekly_premium"]
    bkd = pricing["breakdown"]
    cfg = TIER_CONFIG[tier]

    # Resolve idempotency / Duplicate check 
    # To satisfy idempotent retry matrix, check if this specific order was already demo-settled.
    if order_id and order_id != 'MOCK_ORDER':
        existing_txn = db.query(PayoutTransaction).filter(
            PayoutTransaction.razorpay_order_id == order_id,
            PayoutTransaction.status == PayoutTransactionStatus.DEMO_SETTLED
        ).first()
        
        if existing_txn:
            # Safely resolve previously established policy link
            existing_policy = db.query(Policy).filter(Policy.id == existing_txn.policy_id).first()
            if not existing_policy:
                raise HTTPException(status_code=422, detail="Transaction previously settled structurally missing matching Policy.")
            return {
                "transaction_id": existing_txn.id,
                "payment_state": existing_txn.status,
                "is_demo_bypass": True,
                "policy_id": existing_policy.id,
                "activated_at": existing_policy.start_date,
                "plan_id": existing_policy.tier,
                "amount": premium,
                "original_provider_state": "FAILED",
                "original_provider_error": original_provider_error,
            }

    # Atomically cancel existing active policies safely before issuance if NOT renewing.
    # If renewing, verify we have an active policy to extend.
    if is_renewal:
        policy = db.query(Policy).filter(
            Policy.worker_id == worker.id, 
            Policy.status == PolicyStatus.ACTIVE
        ).first()
        if not policy:
            raise HTTPException(status_code=404, detail="No active policy found to renew via Demo Bypass.")
        
        policy.end_date = (policy.end_date or datetime.utcnow()) + timedelta(days=7)
        policy.weekly_premium = premium
        policy.zone_loading = bkd["zone_loading_inr"]
        policy.seasonal_loading = bkd["seasonal_loading_inr"]
        policy.claim_loading = bkd["claim_loading_inr"]
        policy.streak_discount = abs(bkd["streak_discount_inr"])
        db.flush()
    else:
        # Prevent duplicate cross-activation -> Clean up active first
        prior_active = db.query(Policy).filter(
            Policy.worker_id == worker.id, 
            Policy.status == PolicyStatus.ACTIVE
        ).first()
        if prior_active:
            prior_active.status = PolicyStatus.CANCELLED
            prior_active.end_date = datetime.utcnow()
            db.flush()

        # Create new 7-Day sequence
        policy = Policy(
            worker_id=worker.id,
            policy_number=generate_policy_number(),
            tier=tier,
            status=PolicyStatus.ACTIVE,
            weekly_premium=premium,
            base_premium=pricing["base_premium"],
            max_daily_payout=cfg["max_daily"],
            max_weekly_payout=cfg["max_weekly"],
            zone_loading=bkd["zone_loading_inr"],
            seasonal_loading=bkd["seasonal_loading_inr"],
            claim_loading=bkd["claim_loading_inr"],
            streak_discount=abs(bkd["streak_discount_inr"]),
            start_date=datetime.utcnow(),
            end_date=datetime.utcnow() + timedelta(days=7),
            is_renewal=False,
        )
        db.add(policy)
        db.flush()

    # Generate internal ID referencing exact point of failure 
    import uuid
    sys_txn_id = f"DEMO-{uuid.uuid4().hex[:8].upper()}"
    
    # Audit trail -> record transaction securely
    txn = PayoutTransaction(
        transaction_type=TransactionType.PREMIUM_PAYMENT,
        policy_id=policy.id,
        worker_id=worker.id,
        internal_txn_id=sys_txn_id,
        razorpay_order_id=order_id if order_id != 'MOCK_ORDER' else None,
        razorpay_payment_id=f"pay_DEMO_{uuid.uuid4().hex[:8]}",
        amount_requested=premium,
        amount_settled=premium,
        currency="INR",
        status=PayoutTransactionStatus.DEMO_SETTLED, # Strict Bypass Labeling
        failure_reason=None, # Overall process is 'settled', though gateway failed originally
        gateway_name="razorpay", # Keeps context of which gateway fell through
        is_demo_bypass=True,
        bypass_source_screen=source_screen,
        original_provider_error=original_provider_error,
        environment_at_bypass=os.getenv("ENVIRONMENT", "development"),
        settled_at=datetime.utcnow(),
    )
    db.add(txn)
    db.commit()
    db.refresh(policy)
    db.refresh(txn)

    return {
        "transaction_id": txn.id,
        "payment_state": txn.status,
        "is_demo_bypass": True,
        "policy_id": policy.id,
        "activated_at": policy.start_date,
        "plan_id": policy.tier,
        "amount": premium,
        "original_provider_state": "FAILED",
        "original_provider_error": original_provider_error,
        # Returning policy number directly to UI confirmation triggers smoothly
        "policy_number": policy.policy_number,
    }
