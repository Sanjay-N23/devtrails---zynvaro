from fastapi import APIRouter, Depends, HTTPException, BackgroundTasks
from sqlalchemy import or_, func
from sqlalchemy.orm import Session
from pydantic import BaseModel
from typing import List, Optional, Dict
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
    is_simulated: bool = False
    severity: str
    description: Optional[str]
    detected_at: datetime
    expires_at: Optional[datetime]

    class Config:
        from_attributes = True

class SimulateRequest(BaseModel):
    trigger_type: str
    city: str
    bypass_gate: bool = False

class LiveCheckResponse(BaseModel):
    city: str
    platform: str
    checked_at: str
    triggers_fired: int
    events: list
    source_status: Dict[str, str]
    monitoring_note: str

class SimulateResponse(BaseModel):
    message: str
    trigger_event_id: int
    measured_value: float
    unit: str
    description: str
    is_simulated: bool = True
    current_reading: Optional[Dict] = None
    requester_eligible: bool = True
    requester_effective_city: Optional[str] = None
    requester_location_source: Optional[str] = None
    requester_eligibility_reason: Optional[str] = None


def _build_live_source_status() -> Dict[str, str]:
    from services.trigger_engine import OPENWEATHER_API_KEY, WAQI_API_TOKEN

    return {
        "weather": (
            "OpenWeatherMap configured"
            if OPENWEATHER_API_KEY
            else "Mock fallback (OPENWEATHER_API_KEY missing in backend/.env)"
        ),
        "aqi": (
            "WAQI configured"
            if WAQI_API_TOKEN
            else "Mock fallback (WAQI_API_TOKEN missing in backend/.env)"
        ),
        "platform": "Live HTTP reachability probe",
        "civil": "Live GDELT news scan",
    }


def _worker_trigger_eligibility(worker: Worker, trigger_city: str, trigger_type: str, platform: Optional[str] = None) -> dict:
    from services.fraud_engine import get_worker_location_context, validate_gps_zone

    location = get_worker_location_context(worker)
    effective_city = location.get("effective_city") or worker.city
    source = location.get("source") or "registered_city"
    lat = location.get("lat")
    lng = location.get("lng")

    if trigger_type == "Platform Outage" and platform:
        if (worker.platform or "").lower() != platform.lower():
            return {
                "eligible": False,
                "effective_city": effective_city,
                "location_source": source,
                "claim_lat": lat,
                "claim_lng": lng,
                "reason": f"Worker platform '{worker.platform}' does not match monitored platform '{platform}'.",
            }

    if source == "recent_gps_unmatched":
        return {
            "eligible": False,
            "effective_city": effective_city,
            "location_source": source,
            "claim_lat": lat,
            "claim_lng": lng,
            "reason": "Recent device GPS does not map to a supported trigger city, so the event cannot auto-generate a claim.",
        }

    if lat is not None and lng is not None:
        zone = validate_gps_zone(lat, lng, trigger_city)
        if not zone["valid"]:
            return {
                "eligible": False,
                "effective_city": effective_city,
                "location_source": source,
                "claim_lat": lat,
                "claim_lng": lng,
                "reason": (
                    f"Latest worker location is {zone['distance_km']}km away from the {trigger_city} trigger zone "
                    f"(max {zone['max_radius_km']}km)."
                ),
            }

    if not effective_city or effective_city.lower() != trigger_city.lower():
        return {
            "eligible": False,
            "effective_city": effective_city,
            "location_source": source,
            "claim_lat": lat,
            "claim_lng": lng,
            "reason": f"Worker is resolved to {effective_city or 'an unknown city'}, not {trigger_city}.",
        }

    return {
        "eligible": True,
        "effective_city": effective_city,
        "location_source": source,
        "claim_lat": lat,
        "claim_lng": lng,
        "reason": f"Worker location matches {trigger_city} via {source}.",
    }


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

        # Get trigger zone GPS coordinates (Phase 3)
        from services.fraud_engine import get_city_center
        _city_geo = get_city_center(city)
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
            trigger_lat=_city_geo["lat"] if _city_geo else None,
            trigger_lng=_city_geo["lng"] if _city_geo else None,
        )
        db.add(event)
        db.commit()
        db.refresh(event)
        saved_events.append({
            "id": event.id,
            "trigger_type": event.trigger_type,
            "city": event.city,
            "measured_value": event.measured_value,
            "threshold_value": event.threshold_value,
            "unit": event.unit,
            "source_primary": event.source_primary,
            "source_secondary": event.source_secondary,
            "is_validated": event.is_validated,
            "severity": event.severity,
            "description": event.description,
            "detected_at": event.detected_at.isoformat(),
            "expires_at": event.expires_at.isoformat() if event.expires_at else None,
        })
        # Zero-touch: auto-generate claims for all active policyholders in this city
        if background_tasks:
            background_tasks.add_task(
                _auto_generate_claims, event.id, city, t["trigger_type"], db, is_simulated=False, platform=platform
            )

    return LiveCheckResponse(
        city=city,
        platform=platform,
        checked_at=datetime.utcnow().isoformat(),
        triggers_fired=len(fired),
        events=saved_events,
        source_status=_build_live_source_status(),
        monitoring_note=(
            "Live check runs for your selected city and platform. "
            "Recent Events shows saved trigger history, not raw weather readings."
        ),
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
    valid_types = list(TRIGGERS.keys())
    if req.trigger_type not in valid_types:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid trigger type '{req.trigger_type}'. Must be one of: {', '.join(valid_types)}",
        )
    valid_cities = ["Mumbai", "Delhi", "Bangalore", "Hyderabad", "Chennai", "Pune", "Kolkata"]
    if req.city not in valid_cities:
        raise HTTPException(
            status_code=400,
            detail=f"Invalid city '{req.city}'. Must be one of: {', '.join(valid_cities)}",
        )

    requester_gate = _worker_trigger_eligibility(
        current_worker,
        req.city,
        req.trigger_type,
        platform=current_worker.platform,
    )
    
    if not requester_gate["eligible"] and not req.bypass_gate:
        raise HTTPException(
            status_code=403,
            detail=f"{requester_gate['reason']}|bypass_required"
        )
        
    t = simulate_trigger(req.trigger_type, req.city)

    # Fetch current real reading for comparison (what-if framing)
    from services.trigger_engine import (
        fetch_real_weather, fetch_real_aqi, fetch_real_platform_status,
        fetch_civil_disruption_live, mock_weather, mock_aqi,
        mock_platform_status, mock_civil_disruption,
        OPENWEATHER_API_KEY, WAQI_API_TOKEN,
    )
    current_reading = None
    try:
        if req.trigger_type in ["Heavy Rainfall", "Extreme Rain / Flooding", "Severe Heatwave"]:
            w = await fetch_real_weather(req.city) if OPENWEATHER_API_KEY else None
            if w is None:
                w = mock_weather(req.city)
            if req.trigger_type in ["Heavy Rainfall", "Extreme Rain / Flooding"]:
                current_reading = {"value": round(w.get("rain_24h_mm", 0), 1), "source": "OpenWeatherMap" if OPENWEATHER_API_KEY else "Mock"}
            else:
                current_reading = {"value": round(w.get("temp", 0), 1), "source": "OpenWeatherMap" if OPENWEATHER_API_KEY else "Mock"}
        elif req.trigger_type == "Hazardous AQI":
            aqi = await fetch_real_aqi(req.city) if WAQI_API_TOKEN else None
            current_reading = {"value": round(aqi, 0) if aqi else round(mock_aqi(req.city), 0), "source": "WAQI" if aqi else "Mock"}
        elif req.trigger_type == "Platform Outage":
            ps = await fetch_real_platform_status("Blinkit")
            if ps:
                current_reading = {"value": round(ps.get("latency_ms", 0) / 1000, 1), "source": "HTTP probe", "status": ps.get("status")}
            else:
                current_reading = {"value": 0, "source": "Mock"}
        elif req.trigger_type == "Civil Disruption":
            cd = await fetch_civil_disruption_live(req.city)
            if cd:
                current_reading = {"value": cd.get("duration_hours", 0), "source": "GDELT", "active": cd.get("active_restrictions", False), "articles": cd.get("article_count", 0)}
            else:
                current_reading = {"value": 0, "source": "Mock"}
    except Exception:
        pass  # Non-critical — comparison is optional

    # Get trigger zone GPS coordinates (Phase 3)
    from services.fraud_engine import get_city_center
    _sim_geo = get_city_center(req.city)
    event = TriggerEvent(
        trigger_type=t["trigger_type"],
        city=t["city"],
        measured_value=t["measured_value"],
        threshold_value=t["threshold_value"],
        unit=t["unit"],
        source_primary=t["source_primary"],
        source_secondary=t["source_secondary"],
        is_validated=True,
        is_simulated=True,
        severity=t["severity"],
        description=t["description"],
        detected_at=datetime.utcnow(),
        expires_at=datetime.utcnow() + timedelta(hours=6),
        trigger_lat=_sim_geo["lat"] if _sim_geo else None,
        trigger_lng=_sim_geo["lng"] if _sim_geo else None,
    )
    db.add(event)
    db.commit()
    db.refresh(event)

    # Auto-generate claims for eligible workers in this city
    background_tasks.add_task(
        _auto_generate_claims,
        event.id,
        req.city,
        req.trigger_type,
        db,
        is_simulated=True,
        platform=current_worker.platform,
    )

    return {
        "message": f"Trigger '{req.trigger_type}' simulated in {req.city}",
        "trigger_event_id": event.id,
        "measured_value": t["measured_value"],
        "unit": t["unit"],
        "description": t["description"],
        "is_simulated": True,
        "current_reading": current_reading,
        "requester_eligible": requester_gate["eligible"],
        "requester_effective_city": requester_gate["effective_city"],
        "requester_location_source": requester_gate["location_source"],
        "requester_eligibility_reason": requester_gate["reason"],
    }


@router.get("/conditions")
async def get_live_conditions(
    city: str = "Bangalore",
    platform: str = "Blinkit",
    current_worker: Worker = Depends(get_current_worker),
):
    """
    Fetch raw live weather, AQI, platform, and civil disruption data for a city.
    Returns actual readings (not just fired triggers) so the frontend
    can display real-time conditions regardless of threshold exceedance.
    """
    from services.trigger_engine import (
        fetch_real_weather, fetch_real_aqi, fetch_real_platform_status,
        fetch_civil_disruption_live, mock_weather, mock_aqi,
        mock_platform_status, mock_civil_disruption,
        OPENWEATHER_API_KEY, WAQI_API_TOKEN, CITY_COORDS, TRIGGERS,
    )

    # --- Weather (OpenWeatherMap) ---
    weather_data = None
    weather_source = "mock"
    if OPENWEATHER_API_KEY:
        weather_data = await fetch_real_weather(city)
    if weather_data:
        weather_source = "OpenWeatherMap (live)"
    else:
        mock = mock_weather(city)
        weather_data = {
            "temp": mock["temp"],
            "rain_1h_mm": 0,
            "rain_3h_mm": 0,
            "rain_24h_mm": mock["rain_24h_mm"],
            "description": "mock data",
            "source": "Mock fallback",
        }

    # --- AQI (WAQI) ---
    aqi_value = None
    aqi_source = "mock"
    if WAQI_API_TOKEN:
        aqi_value = await fetch_real_aqi(city)
    if aqi_value is not None:
        aqi_source = "WAQI (live)"
    else:
        aqi_value = mock_aqi(city)
        aqi_source = "Mock fallback"

    # --- Platform status ---
    platform_data = await fetch_real_platform_status(platform)
    if platform_data:
        platform_source = platform_data.get("source", "HTTP probe (live)")
    else:
        platform_data = mock_platform_status(platform)
        platform_source = "Mock fallback"

    # --- Civil disruption (GDELT) ---
    civil_data = await fetch_civil_disruption_live(city)
    if civil_data:
        civil_source = civil_data.get("source", "GDELT (live)")
    else:
        civil_data = mock_civil_disruption(city)
        civil_source = "Mock fallback"

    # --- AQI category label ---
    def aqi_category(val):
        if val <= 50: return "Good"
        if val <= 100: return "Moderate"
        if val <= 150: return "Unhealthy (Sensitive)"
        if val <= 200: return "Unhealthy"
        if val <= 300: return "Very Unhealthy"
        return "Hazardous"

    # --- Threshold proximity ---
    rain_threshold = TRIGGERS["Heavy Rainfall"]["threshold"]
    heat_threshold = TRIGGERS["Severe Heatwave"]["threshold"]
    aqi_threshold = TRIGGERS["Hazardous AQI"]["threshold"]

    coords = CITY_COORDS.get(city, CITY_COORDS["Bangalore"])

    return {
        "city": city,
        "platform": platform,
        "checked_at": datetime.utcnow().isoformat(),
        "coordinates": {"lat": coords["lat"], "lon": coords["lon"]},
        "weather": {
            "temperature_c": round(weather_data["temp"], 1),
            "description": weather_data.get("description", ""),
            "rain_1h_mm": round(weather_data.get("rain_1h_mm", 0), 1),
            "rain_3h_mm": round(weather_data.get("rain_3h_mm", 0), 1),
            "rain_24h_est_mm": round(weather_data.get("rain_24h_mm", 0), 1),
            "rain_threshold_mm": rain_threshold,
            "heat_threshold_c": heat_threshold,
            "source": weather_source,
        },
        "aqi": {
            "value": round(aqi_value, 0),
            "category": aqi_category(aqi_value),
            "threshold": aqi_threshold,
            "source": aqi_source,
        },
        "platform_status": {
            "name": platform_data.get("platform", platform),
            "status": platform_data.get("status", "UNKNOWN"),
            "latency_ms": platform_data.get("latency_ms", 0),
            "source": platform_source,
        },
        "civil_disruption": {
            "active": civil_data.get("active_restrictions", False),
            "type": civil_data.get("type"),
            "article_count": civil_data.get("article_count", 0),
            "source": civil_source,
        },
        "sources": {
            "weather": weather_source,
            "aqi": aqi_source,
            "platform": platform_source,
            "civil": civil_source,
        },
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
def _auto_generate_claims(
    event_id: int,
    city: str,
    trigger_type: str,
    db: Session,
    is_simulated: bool = False,
    platform: Optional[str] = None,
):
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

        # Find active policies and apply location/platform eligibility in Python.
        # We cannot rely on Worker.city alone because recent device GPS may place
        # the worker in a different supported city than their original profile city.
        active_policies = (
            db.query(Policy)
            .join(Worker)
            .filter(Policy.status == PolicyStatus.ACTIVE, Worker.is_active == True)
            .all()
        )

        claims_created = 0
        for policy in active_policies:
            worker = policy.worker
            eligibility = _worker_trigger_eligibility(worker, city, trigger_type, platform=platform)
            if not eligibility["eligible"]:
                continue

            payout_city = eligibility.get("effective_city") or worker.city
            payout = get_payout_amount(trigger_type, policy.tier, payout_city)
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

            # Use the freshest resolved worker location for fraud checks and eligibility.
            claim_lat = eligibility.get("claim_lat")
            claim_lng = eligibility.get("claim_lng")

            # Advanced fraud scoring (Phase 3: 6-module engine + 14-feature ML)
            fraud = compute_authenticity_score(
                worker_city=worker.city,
                trigger_city=city,
                claim_history=worker.claim_history_count,
                same_week_claims=same_week_count,
                device_attested=True,
                trigger_type=trigger_type,
                payout_amount=payout,
                disruption_streak=worker.disruption_streak or 0,
                worker=worker,
                trigger_event=event,
                claim_lat=claim_lat,
                claim_lng=claim_lng,
                db=db,
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
                # Phase 3 advanced fraud metadata
                claim_lat=fraud.get("claim_lat"),
                claim_lng=fraud.get("claim_lng"),
                gps_distance_km=fraud.get("gps_distance_km"),
                ml_fraud_probability=fraud.get("ml_fraud_probability"),
                risk_tier=fraud.get("risk_tier"),
                shift_valid=fraud.get("shift_valid", True),
                weather_cross_valid=fraud.get("weather_cross_valid", True),
                velocity_valid=fraud.get("velocity_valid", True),
                is_simulated=is_simulated,
            )
            db.add(claim)
            db.flush()  # Get claim.id for payout transaction

            # Phase 3: Razorpay payout (or mock fallback)
            if is_auto_approved:
                try:
                    from services.payout_service import initiate_payout
                    initiate_payout(claim, worker, db)
                except Exception as e:
                    # Fallback: mark paid with mock ref if payout service fails
                    claim.paid_at = datetime.utcnow()
                    claim.payment_ref = f"MOCK-UPI-{claim_num}"
                    print(f"[Payout] Service error, using mock: {e}")

            # Update worker risk profile
            worker.claim_history_count = Worker.claim_history_count + 1
            worker.disruption_streak = 0  # reset streak — a claim event breaks the clean run

            # Update worker behavioral profile (Phase 3)
            worker.last_claim_city = city
            worker.last_claim_at = datetime.utcnow()
            if fraud["flags"]:
                worker.fraud_flag_count = (worker.fraud_flag_count or 0) + len(fraud["flags"])
            claims_created += 1

        db.commit()
    finally:
        db.close()
