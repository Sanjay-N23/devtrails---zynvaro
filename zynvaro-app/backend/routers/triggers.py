from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import or_, func
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional
from datetime import datetime, timedelta
import asyncio

from database import get_db
from models import TriggerEvent, Worker, Policy, Claim, PolicyStatus, ClaimStatus
from routers.auth import get_current_worker
from services.trigger_engine import (
    check_all_triggers, simulate_trigger, compute_authenticity_score, TRIGGERS
)
from ml.premium_engine import get_payout_amount

router = APIRouter(prefix="/triggers", tags=["Parametric Triggers"])


# ─── Schemas ────────────────────────────────────────────────────
class TriggerEventResponse(BaseModel):
    id: int
    trigger_type: str
    city: str
    pincode: Optional[str]
    measured_value: float
    threshold_value: float
    unit: str
    source_primary: str
    source_secondary: Optional[str]
    is_validated: bool
    severity: str
    description: Optional[str]
    detected_at: datetime
    expires_at: Optional[datetime]

    class Config:
        from_attributes = True

class SimulateRequest(BaseModel):
    trigger_type: str
    city: str

class LiveCheckResponse(BaseModel):
    city: str
    checked_at: str
    triggers_fired: int
    events: list

class SimulateResponse(BaseModel):
    message: str
    trigger_event_id: int
    measured_value: float
    unit: str
    description: str


# ─── Endpoints ──────────────────────────────────────────────────
@router.get("/", response_model=List[TriggerEventResponse])
def list_trigger_events(
    city: Optional[str] = None,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """List recent trigger events (admin / public feed)."""
    q = db.query(TriggerEvent).order_by(TriggerEvent.detected_at.desc())
    if city:
        q = q.filter(TriggerEvent.city == city)
    return q.limit(limit).all()


@router.get("/live", response_model=LiveCheckResponse)
async def live_check(
    city: str = "Bangalore",
    platform: str = "Blinkit",
    background_tasks: BackgroundTasks = BackgroundTasks(),
    db: Session = Depends(get_db),
    current_worker: Worker = Depends(get_current_worker),
):
    """
    Run a live trigger check for a city.
    Calls real OpenWeatherMap API + mock APIs.
    Saves any fired triggers to DB and auto-generates claims (zero-touch).
    Requires auth — this endpoint has side effects (claim generation).
    """
    try:
        fired = await check_all_triggers(city, platform)
    except Exception as e:
        print(f"[LiveCheck] check_all_triggers failed for {city}: {e}")
        fired = []  # Graceful degradation — show empty rather than crash

    saved_events = []
    for t in fired:
        # Deduplication: skip if same trigger type + city fired within 3 hours or hasn't expired
        recent = (
            db.query(TriggerEvent)
            .filter(
                TriggerEvent.trigger_type == t["trigger_type"],
                TriggerEvent.city == city,
                or_(
                    TriggerEvent.detected_at >= (datetime.utcnow() - timedelta(hours=3)),
                    TriggerEvent.expires_at >= datetime.utcnow(),
                ),
            )
            .first()
        )
        if recent:
            continue

        event = TriggerEvent(
            trigger_type=t["trigger_type"],
            city=t["city"],
            measured_value=t["measured_value"],
            threshold_value=t["threshold_value"],
            unit=t["unit"],
            source_primary=t["source_primary"],
            source_secondary=t["source_secondary"],
            is_validated=t["is_validated"],
            severity=t["severity"],
            description=t["description"],
            detected_at=datetime.fromisoformat(t["detected_at"]),
            expires_at=datetime.fromisoformat(t["expires_at"]),
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        saved_events.append({
            "id": event.id,
            "trigger_type": event.trigger_type,
            "measured_value": event.measured_value,
            "threshold_value": event.threshold_value,
            "unit": event.unit,
            "severity": event.severity,
            "description": event.description,
        })
        # Zero-touch: auto-generate claims for all active policyholders in this city
        if background_tasks:
            background_tasks.add_task(
                _auto_generate_claims, event.id, city, t["trigger_type"], db
            )

    return LiveCheckResponse(
        city=city,
        checked_at=datetime.utcnow().isoformat(),
        triggers_fired=len(fired),
        events=saved_events,
    )


@router.post("/simulate", status_code=201, response_model=SimulateResponse)
async def simulate_trigger_event(
    req: SimulateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    current_worker: Worker = Depends(get_current_worker),
):
    """
    [DEMO] Force-fire a specific trigger in a city.
    Saves the event and auto-creates claims for all active policyholders in that city.
    """
    t = simulate_trigger(req.trigger_type, req.city)

    event = TriggerEvent(
        trigger_type=t["trigger_type"],
        city=t["city"],
        measured_value=t["measured_value"],
        threshold_value=t["threshold_value"],
        unit=t["unit"],
        source_primary=t["source_primary"],
        source_secondary=t["source_secondary"],
        is_validated=True,
        severity=t["severity"],
        description=t["description"],
        detected_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(hours=6),
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    # Auto-generate claims for eligible workers in this city
    background_tasks.add_task(_auto_generate_claims, event.id, req.city, req.trigger_type, db)

    return {
        "message": f"Trigger '{req.trigger_type}' simulated in {req.city}",
        "trigger_event_id": event.id,
        "measured_value": t["measured_value"],
        "unit": t["unit"],
        "description": t["description"],
    }


@router.get("/types")
def list_trigger_types():
    """List all supported trigger types with thresholds."""
    return [
        {
            "trigger_type": k,
            "threshold": v["threshold"],
            "unit": v["unit"],
            "source_primary": v["source_primary"],
        }
        for k, v in TRIGGERS.items()
    ]


# ─── Background: Auto-generate claims after trigger fires ───────
def _auto_generate_claims(event_id: int, city: str, trigger_type: str, db: Session):
    """
    Find all active workers + policies in the triggered city.
    Create claims automatically (zero-touch).
    """
    from models import Claim
    import random, string

    # Reload DB session in background task
    from database import SessionLocal
    db = SessionLocal()

    try:
        event = db.query(TriggerEvent).filter(TriggerEvent.id == event_id).first()
        if not event:
            return

        # GAP 2 FIX: Don't process claims for expired triggers
        if event.expires_at and event.expires_at < datetime.utcnow():
            return

        # Find active policies in this city
        active_policies = (
            db.query(Policy)
            .join(Worker)
            .filter(Worker.city == city, Policy.status == PolicyStatus.ACTIVE)
            .all()
        )

        claims_created = 0
        for policy in active_policies:
            worker = policy.worker
            payout = get_payout_amount(trigger_type, policy.tier, worker.city)
            if payout <= 0:
                continue  # Tier doesn't cover this trigger

            # GAP 3 FIX: Skip if worker already has a claim for same trigger type in last 24h
            existing = (
                db.query(Claim).join(TriggerEvent)
                .filter(
                    Claim.worker_id == worker.id,
                    TriggerEvent.trigger_type == trigger_type,
                    TriggerEvent.city == city,
                    Claim.created_at >= (datetime.utcnow() - timedelta(hours=24))
                ).first()
            )
            if existing:
                continue  # Prevent same-event duplicate claim

            # H7 FIX: Enforce weekly aggregate payout cap from the policy
            week_ago = datetime.utcnow() - timedelta(days=7)
            weekly_paid = db.query(
                func.coalesce(func.sum(Claim.payout_amount), 0)
            ).filter(
                Claim.worker_id == worker.id,
                Claim.paid_at.isnot(None),
                Claim.created_at >= week_ago,
            ).scalar() or 0

            if weekly_paid + payout > policy.max_weekly_payout:
                payout = max(0, policy.max_weekly_payout - weekly_paid)
                if payout <= 0:
                    continue  # Weekly cap reached, skip this claim

            # GAP 1 FIX: Count actual same-week claims for accurate fraud scoring
            same_week_count = db.query(Claim).filter(
                Claim.worker_id == worker.id,
                Claim.created_at >= (datetime.utcnow() - timedelta(days=7))
            ).count()

            # Fraud scoring
            fraud = compute_authenticity_score(
                worker_city=worker.city,
                trigger_city=city,
                claim_history=worker.claim_history_count,
                same_week_claims=same_week_count,
                device_attested=True,
            )

            # AUTO_APPROVED claims are paid immediately (payment ref + timestamp set in
            # the same DB transaction) → status goes straight to PAID.
            # PENDING / MANUAL sit in escrow awaiting human review.
            status_map = {
                "AUTO_APPROVED": ClaimStatus.PAID,          # paid instantly
                "PENDING_REVIEW": ClaimStatus.PENDING_REVIEW,
                "MANUAL_REVIEW": ClaimStatus.MANUAL_REVIEW,
            }

            claim_num = "CLM-" + ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            is_auto_approved = fraud["decision"] == "AUTO_APPROVED"
            paid_at = datetime.utcnow() if is_auto_approved else None

            claim = Claim(
                claim_number=claim_num,
                worker_id=worker.id,
                policy_id=policy.id,
                trigger_event_id=event.id,
                status=status_map.get(fraud["decision"], ClaimStatus.PENDING_REVIEW),
                payout_amount=payout,
                authenticity_score=fraud["score"],
                gps_valid=fraud["gps_valid"],
                activity_valid=fraud["activity_valid"],
                device_valid=fraud["device_valid"],
                cross_source_valid=fraud["cross_source_valid"],
                fraud_flags="; ".join(fraud["flags"]) if fraud["flags"] else None,
                auto_processed=True,
                paid_at=paid_at,
                payment_ref=f"MOCK-UPI-{claim_num}" if paid_at else None,
            )
            db.add(claim)

            # Update worker risk profile
            worker.claim_history_count = Worker.claim_history_count + 1
            worker.disruption_streak = 0  # reset streak — a claim event breaks the clean run
            claims_created += 1

        db.commit()
    finally:
        db.close()
