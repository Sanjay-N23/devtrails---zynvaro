"""
Zynvaro — Advanced Fraud Detection Engine (Phase 3: SOAR)
Production-ready fraud detection with 6 composable modules:
  1. GPS Zone Validator — geofence-based spoofing detection
  2. Shift-Time Validator — off-hours claim detection
  3. Historical Weather Cross-Validator — fake weather claim detection
  4. Velocity Anomaly Detector — impossible travel detection
  5. Behavioral Pattern Analyzer — frequency & repeat offender detection
  6. Cross-Claim Deduplicator — duplicate & UPI fraud detection

Each module returns: {"valid": bool, "score_impact": float, "flag": str|None, "details": dict}
Master orchestrator aggregates all modules into a single fraud assessment.
"""

import math
import hashlib
from datetime import datetime, timedelta
from typing import Optional

# ─────────────────────────────────────────────────────────────────
# CITY COORDINATE DATABASE (production-ready for Indian metros)
# lat/lng = city center, radius_km = approximate urban sprawl radius
# ─────────────────────────────────────────────────────────────────
CITY_COORDINATES = {
    "Mumbai":    {"lat": 19.0760, "lng": 72.8777, "radius_km": 35},
    "Delhi":     {"lat": 28.6139, "lng": 77.2090, "radius_km": 40},
    "Bangalore": {"lat": 12.9716, "lng": 77.5946, "radius_km": 30},
    "Hyderabad": {"lat": 17.3850, "lng": 78.4867, "radius_km": 30},
    "Chennai":   {"lat": 13.0827, "lng": 80.2707, "radius_km": 25},
    "Pune":      {"lat": 18.5204, "lng": 73.8567, "radius_km": 25},
    "Kolkata":   {"lat": 22.5726, "lng": 88.3639, "radius_km": 30},
}

RECENT_LOCATION_FRESHNESS_HOURS = 6
RECENT_ACTIVITY_WINDOW_HOURS = 48
CITY_INFERENCE_BUFFER_KM = 8

# ─────────────────────────────────────────────────────────────────
# ACTIVITY REASON CODES  (machine-readable, never change these values)
# ─────────────────────────────────────────────────────────────────
RC_STRONG_CONFIRMED         = "RECENT_ACTIVITY_CONFIRMED_STRONG"
RC_MEDIUM_CONFIRMED         = "RECENT_ACTIVITY_CONFIRMED_MEDIUM"
RC_SHIFT_CONFIRMED          = "RECENT_ACTIVITY_CONFIRMED_SHIFT_BASED"
RC_OUTAGE_CONFIRMED         = "OUTAGE_WINDOW_ACTIVITY_CONFIRMED"
RC_OUTAGE_NOT_FOUND         = "OUTAGE_WINDOW_ACTIVITY_NOT_FOUND"
RC_ACTIVITY_TOO_OLD         = "ACTIVITY_TOO_OLD"
RC_NO_ACTIVITY              = "NO_ACTIVITY_FOUND"
RC_SIGNALS_TOO_WEAK         = "SIGNALS_TOO_WEAK"
RC_DATA_INCOMPLETE          = "ACTIVITY_DATA_INCOMPLETE"
RC_LOCATION_MISMATCH        = "LOCATION_MISMATCH"
RC_DEVICE_MISMATCH          = "DEVICE_MISMATCH"

# ─────────────────────────────────────────────────────────────────
# SIGNAL TRUST LEVELS
# ─────────────────────────────────────────────────────────────────
# Strong = platform heartbeat or GPS ping from the device
# Medium = authenticated app session
# Weak   = registration seed only (no real user session)
STRONG_SOURCES = {"gps_ping"}
MEDIUM_SOURCES = {"session_ping"}
WEAK_SOURCES   = {"signup_seed", "manual_declaration", "login_only"}

# ─────────────────────────────────────────────────────────────────
# TRIGGER-SPECIFIC LOOKBACK WINDOWS  (Section 12)
# ─────────────────────────────────────────────────────────────────
ACTIVITY_WINDOW_BY_TRIGGER: dict[str, int] = {
    "Heavy Rainfall":           24,   # hours
    "Extreme Rain / Flooding":  24,
    "Severe Heatwave":          24,
    "Hazardous AQI":            24,
    "Civil Disruption":         24,   # shift-overlap check tightens this further
    "Platform Outage":           2,   # much tighter: outage stops new orders
    "default":                  48,   # fallback
}

# Review-threshold: if confidence drops below this, route to REVIEW_REQUIRED
ACTIVITY_CONFIDENCE_REVIEW_THRESHOLD = 0.55

# ─────────────────────────────────────────────────────────────────
# PINCODE → GPS MAPPING (30+ pincodes, deterministic from city center)
# Each pincode gets a unique position within its city via hash offset
# ─────────────────────────────────────────────────────────────────
PINCODE_CITY_MAP = {
    # Mumbai
    "400001": "Mumbai", "400002": "Mumbai", "400051": "Mumbai", "400053": "Mumbai",
    "400070": "Mumbai", "400072": "Mumbai", "400063": "Mumbai", "400078": "Mumbai",
    # Delhi
    "110001": "Delhi", "110002": "Delhi", "110019": "Delhi", "110025": "Delhi",
    "110085": "Delhi", "110092": "Delhi",
    # Bangalore
    "560001": "Bangalore", "560034": "Bangalore", "560047": "Bangalore",
    "560095": "Bangalore", "560100": "Bangalore", "560076": "Bangalore",
    # Hyderabad
    "500001": "Hyderabad", "500016": "Hyderabad", "500072": "Hyderabad", "500081": "Hyderabad",
    # Chennai
    "600001": "Chennai", "600020": "Chennai", "600041": "Chennai", "600119": "Chennai",
    # Pune
    "411001": "Pune", "411045": "Pune", "411014": "Pune",
    # Kolkata
    "700001": "Kolkata", "700020": "Kolkata", "700091": "Kolkata",
}


def _pincode_hash_offset(pincode: str) -> tuple:
    """
    Generate a deterministic lat/lng offset from a pincode.
    Uses SHA-256 hash to distribute pincodes within city radius.
    Returns offset in degrees (~0.01° ≈ 1.1 km at Indian latitudes).
    """
    h = hashlib.sha256(pincode.encode()).hexdigest()
    # Use first 8 hex chars for lat, next 8 for lng
    lat_offset = (int(h[:8], 16) / 0xFFFFFFFF - 0.5) * 0.15  # ±0.075° ≈ ±8km
    lng_offset = (int(h[8:16], 16) / 0xFFFFFFFF - 0.5) * 0.15
    return lat_offset, lng_offset


def get_pincode_gps(pincode: str, city: str) -> tuple:
    """
    Get approximate GPS coordinates for a pincode within its city.
    Returns (lat, lng). Falls back to city center if pincode unknown.
    """
    city_data = CITY_COORDINATES.get(city)
    if not city_data:
        return None, None

    lat_offset, lng_offset = _pincode_hash_offset(pincode or "000000")
    return (
        round(city_data["lat"] + lat_offset, 6),
        round(city_data["lng"] + lng_offset, 6),
    )


def get_city_center(city: str) -> Optional[dict]:
    """Get city center coordinates and radius. Returns None for unknown cities."""
    return CITY_COORDINATES.get(city)


# ─────────────────────────────────────────────────────────────────
# HAVERSINE DISTANCE CALCULATOR
# ─────────────────────────────────────────────────────────────────
def haversine_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float:
    """
    Great-circle distance between two GPS points in kilometers.
    Uses the Haversine formula — accurate for distances on Earth's surface.
    """
    R = 6371.0  # Earth radius in km

    lat1_r, lat2_r = math.radians(lat1), math.radians(lat2)
    dlat = math.radians(lat2 - lat1)
    dlng = math.radians(lng2 - lng1)

    a = math.sin(dlat / 2) ** 2 + math.cos(lat1_r) * math.cos(lat2_r) * math.sin(dlng / 2) ** 2
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1 - a))

    return round(R * c, 2)


def infer_city_from_coords(lat: float, lng: float, buffer_km: float = CITY_INFERENCE_BUFFER_KM) -> Optional[dict]:
    """
    Infer the nearest supported city for a GPS point.

    Returns the closest city only when the point falls within that city's
    geofence or a small outskirts buffer. Otherwise returns None.
    """
    best_match = None

    for city, city_data in CITY_COORDINATES.items():
        distance_km = haversine_km(lat, lng, city_data["lat"], city_data["lng"])
        max_distance = city_data["radius_km"] + buffer_km
        if distance_km > max_distance:
            continue

        if best_match is None or distance_km < best_match["distance_km"]:
            best_match = {
                "city": city,
                "distance_km": distance_km,
                "radius_km": city_data["radius_km"],
                "match_type": (
                    "IN_ZONE"
                    if distance_km <= city_data["radius_km"]
                    else "BUFFER_ZONE"
                ),
            }

    return best_match


def get_worker_location_context(worker, freshness_hours: int = RECENT_LOCATION_FRESHNESS_HOURS) -> dict:
    """
    Resolve the best available location context for a worker.

    Priority:
    1. Recent device GPS sent from the frontend
    2. Registered home GPS derived from pincode
    3. Registered profile city
    """
    now = datetime.utcnow()
    last_location_at = getattr(worker, "last_location_at", None)
    last_activity_source = getattr(worker, "last_activity_source", None)
    freshness_cutoff = now - timedelta(hours=freshness_hours)
    has_device_gps = bool(
        getattr(worker, "last_known_lat", None) is not None
        and getattr(worker, "last_known_lng", None) is not None
    )
    inferred_device_city = None
    session_backfilled_device_gps = False
    if has_device_gps:
        inferred_device_city = infer_city_from_coords(worker.last_known_lat, worker.last_known_lng)
        home_lat = getattr(worker, "home_lat", None)
        home_lng = getattr(worker, "home_lng", None)
        session_backfilled_device_gps = bool(
            last_activity_source == "session_ping"
            and last_location_at is not None
            and last_location_at >= freshness_cutoff
            and inferred_device_city is not None
            and (
                home_lat is None
                or home_lng is None
                or abs(float(worker.last_known_lat) - float(home_lat)) > 1e-6
                or abs(float(worker.last_known_lng) - float(home_lng)) > 1e-6
            )
        )
    is_recent_device_gps = bool(
        has_device_gps
        and (last_activity_source in {None, "gps_ping"} or session_backfilled_device_gps)
        and last_location_at is not None
        and last_location_at >= freshness_cutoff
    )

    def _base_context(source: str, city: Optional[str], lat: Optional[float], lng: Optional[float], inferred: Optional[dict]) -> dict:
        age_minutes = None
        if last_location_at is not None:
            age_minutes = max(0, round((now - last_location_at).total_seconds() / 60))
        return {
            "effective_city": city or getattr(worker, "city", None),
            "source": source,
            "lat": lat,
            "lng": lng,
            "distance_km": inferred["distance_km"] if inferred else None,
            "match_type": inferred["match_type"] if inferred else None,
            "location_fresh": is_recent_device_gps,
            "location_age_minutes": age_minutes,
            "last_location_at": last_location_at,
            "last_activity_source": last_activity_source,
        }

    if has_device_gps:
        lat = worker.last_known_lat
        lng = worker.last_known_lng
        inferred = inferred_device_city
        if inferred:
            src = "recent_gps" if is_recent_device_gps else "stale_gps"
            return _base_context(src, inferred["city"], lat, lng, inferred)
        src = "recent_gps_unmatched" if is_recent_device_gps else "stale_gps_unmatched"
        return _base_context(src, getattr(worker, "city", None), lat, lng, None)

    home_lat = getattr(worker, "home_lat", None)
    home_lng = getattr(worker, "home_lng", None)
    if home_lat is not None and home_lng is not None:
        inferred = infer_city_from_coords(home_lat, home_lng)
        if inferred:
            return _base_context("home_gps", inferred["city"], home_lat, home_lng, inferred)
        return _base_context("home_gps_unmatched", getattr(worker, "city", None), home_lat, home_lng, None)

    return _base_context("registered_city", getattr(worker, "city", None), None, None, None)


def get_recent_activity_snapshot(
    worker,
    as_of: Optional[datetime] = None,
    window_hours: int | None = None,
    trigger_type: str | None = None,
) -> dict:
    """
    Determine whether the worker has recent, user-originated activity.

    Returns an eligibility dict with keys:
      eligible       : bool
      eligibility_state : ELIGIBLE | INELIGIBLE | REVIEW_REQUIRED | UNKNOWN_ACTIVITY
      reason_code    : machine-readable reason code (RC_* constant)
      activity_at    : datetime | None
      activity_age_hours : float | None
      activity_source : str | None
      confidence     : float  0.0 – 1.0
      reason         : human-readable string

    `signup_seed` does not count as activity because it is derived from the
    registration pincode rather than a live user session or GPS ping.
    """
    reference_time = as_of or datetime.utcnow()

    # Choose lookback window: caller override > trigger-specific > default
    effective_window = (
        window_hours if window_hours is not None
        else ACTIVITY_WINDOW_BY_TRIGGER.get(trigger_type or "", RECENT_ACTIVITY_WINDOW_HOURS)
    )

    last_activity_at = getattr(worker, "last_location_at", None)
    activity_source = getattr(worker, "last_activity_source", None)

    # ── No timestamp at all ──────────────────────────────────────────
    if last_activity_at is None:
        return {
            "eligible": False,
            "eligibility_state": "UNKNOWN_ACTIVITY",
            "reason_code": RC_NO_ACTIVITY,
            "activity_at": None,
            "activity_age_hours": None,
            "activity_source": activity_source,
            "confidence": 0.0,
            "reason": f"No recent app activity was captured in the last {effective_window} hours.",
        }

    age_hours = round(max(0.0, (reference_time - last_activity_at).total_seconds() / 3600), 2)

    # ── Activity window exceeded ─────────────────────────────────────
    if last_activity_at < reference_time - timedelta(hours=effective_window):
        reason_code = RC_OUTAGE_NOT_FOUND if trigger_type == "Platform Outage" else RC_ACTIVITY_TOO_OLD
        return {
            "eligible": False,
            "eligibility_state": "INELIGIBLE",
            "reason_code": reason_code,
            "activity_at": last_activity_at,
            "activity_age_hours": age_hours,
            "activity_source": activity_source,
            "confidence": 0.0,
            "reason": f"Last user activity is {age_hours} hours old, beyond the {effective_window}-hour payout eligibility window.",
        }

    # ── Determine signal tier and confidence ────────────────────────
    if activity_source in STRONG_SOURCES:
        base_confidence = 1.0
        reason_code = RC_OUTAGE_CONFIRMED if trigger_type == "Platform Outage" else RC_STRONG_CONFIRMED
        state = "ELIGIBLE"
        eligible = True
        tier_label = "gps ping"
    elif activity_source in MEDIUM_SOURCES:
        base_confidence = 0.7
        reason_code = RC_MEDIUM_CONFIRMED
        state = "ELIGIBLE"
        eligible = True
        tier_label = "app session"
    elif activity_source in WEAK_SOURCES or activity_source is None:
        # Weak — route to REVIEW, do not auto-approve
        base_confidence = 0.4
        reason_code = RC_SIGNALS_TOO_WEAK
        state = "REVIEW_REQUIRED"
        eligible = False
        tier_label = "signup/manual signal"
    else:
        # Unknown source — treat as incomplete
        base_confidence = 0.3
        reason_code = RC_DATA_INCOMPLETE
        state = "REVIEW_REQUIRED"
        eligible = False
        tier_label = "unknown signal"

    # Staleness penalty
    staleness_ratio = age_hours / max(effective_window, 1)
    staleness_penalty = round(staleness_ratio * 0.3, 2)   # up to -0.3 as activity ages
    confidence = max(0.0, round(base_confidence - staleness_penalty, 3))

    # Down-grade to REVIEW_REQUIRED if confidence slips below threshold
    if eligible and confidence < ACTIVITY_CONFIDENCE_REVIEW_THRESHOLD:
        state = "REVIEW_REQUIRED"
        eligible = False
        reason_code = RC_SIGNALS_TOO_WEAK

    human_text = {
        "ELIGIBLE":         f"Recent {tier_label} recorded {age_hours}h before payout review.",
        "REVIEW_REQUIRED":  f"Activity signal is present but confidence ({confidence:.2f}) is below threshold — payout requires review.",
        "INELIGIBLE":       f"Last user activity is {age_hours}h old, beyond the {effective_window}-h window.",
        "UNKNOWN_ACTIVITY": f"No recent app activity found in the last {effective_window}h.",
    }

    return {
        "eligible": eligible,
        "eligibility_state": state,
        "reason_code": reason_code,
        "activity_at": last_activity_at,
        "activity_age_hours": age_hours,
        "activity_source": activity_source,
        "confidence": confidence,
        "reason": human_text[state],
    }


# ─────────────────────────────────────────────────────────────────
# GPS ZONE VALIDATION
# ─────────────────────────────────────────────────────────────────
def validate_gps_zone(worker_lat: float, worker_lng: float, trigger_city: str) -> dict:
    """
    Check if worker's GPS position is within the trigger city's geofence zone.

    Returns:
        {
            "valid": bool,
            "distance_km": float,
            "max_radius_km": float,
            "zone_status": "IN_ZONE" | "EDGE_ZONE" | "OUTSIDE_ZONE"
        }
    """
    city_data = CITY_COORDINATES.get(trigger_city)
    if not city_data:
        return {"valid": True, "distance_km": 0.0, "max_radius_km": 0.0, "zone_status": "UNKNOWN_CITY"}

    if worker_lat is None or worker_lng is None:
        return {"valid": True, "distance_km": 0.0, "max_radius_km": city_data["radius_km"], "zone_status": "NO_GPS"}

    distance = haversine_km(worker_lat, worker_lng, city_data["lat"], city_data["lng"])
    radius = city_data["radius_km"]

    if distance <= radius * 0.7:
        zone_status = "IN_ZONE"
        valid = True
    elif distance <= radius:
        zone_status = "EDGE_ZONE"
        valid = True
    else:
        zone_status = "OUTSIDE_ZONE"
        valid = False

    return {
        "valid": valid,
        "distance_km": distance,
        "max_radius_km": radius,
        "zone_status": zone_status,
    }


# ─────────────────────────────────────────────────────────────────
# SHIFT-TIME WINDOWS
# ─────────────────────────────────────────────────────────────────
SHIFT_WINDOWS = {
    "Morning Rush (6AM-2PM)":  (6, 14),
    "Afternoon (12PM-8PM)":    (12, 20),
    "Evening Peak (6PM-2AM)":  (18, 2),    # crosses midnight
    "Night Owl (10PM-6AM)":    (22, 6),    # crosses midnight
    "Full Day (8AM-8PM)":      (8, 20),
}

GRACE_HOURS = 1  # ±1 hour grace period


def _is_hour_in_shift(hour: int, shift_start: int, shift_end: int) -> bool:
    """Check if hour falls within shift window (handles midnight crossing)."""
    hour_with_grace_start = (shift_start - GRACE_HOURS) % 24
    hour_with_grace_end = (shift_end + GRACE_HOURS) % 24

    if shift_start < shift_end:
        # Normal range (e.g., 6-14)
        return hour_with_grace_start <= hour <= hour_with_grace_end
    else:
        # Crosses midnight (e.g., 18-2 → valid hours: 18-23, 0-2)
        return hour >= hour_with_grace_start or hour <= hour_with_grace_end


# ═════════════════════════════════════════════════════════════════
# MODULE 1: GPS SPOOFING DETECTOR
# ═════════════════════════════════════════════════════════════════
def check_gps_spoofing(worker_city: str, trigger_city: str,
                       claim_lat: Optional[float] = None,
                       claim_lng: Optional[float] = None,
                       worker_home_lat: Optional[float] = None,
                       worker_home_lng: Optional[float] = None) -> dict:
    """
    Detect GPS spoofing by checking worker's position against trigger zone.
    Uses actual GPS coords if available, falls back to city-name matching.
    """
    # Use claim GPS if available, otherwise use home coords
    lat = claim_lat if claim_lat is not None else worker_home_lat
    lng = claim_lng if claim_lng is not None else worker_home_lng

    if lat is not None and lng is not None:
        zone = validate_gps_zone(lat, lng, trigger_city)
        distance_km = zone["distance_km"]

        if zone["zone_status"] == "OUTSIDE_ZONE":
            return {
                "valid": False,
                "score_impact": -45,
                "flag": f"⚠️ GPS spoofing detected: {distance_km}km from {trigger_city} (max {zone['max_radius_km']}km)",
                "details": {"distance_km": distance_km, "zone_status": "OUTSIDE_ZONE", "max_radius_km": zone["max_radius_km"]},
            }
        elif zone["zone_status"] == "EDGE_ZONE":
            return {
                "valid": True,
                "score_impact": -15,
                "flag": f"⚠️ GPS near zone boundary: {distance_km}km from {trigger_city} center",
                "details": {"distance_km": distance_km, "zone_status": "EDGE_ZONE", "max_radius_km": zone["max_radius_km"]},
            }
        else:
            return {
                "valid": True,
                "score_impact": 0,
                "flag": None,
                "details": {"distance_km": distance_km, "zone_status": "IN_ZONE", "max_radius_km": zone["max_radius_km"]},
            }
    else:
        # No GPS data — fall back to city-name matching
        city_match = worker_city.lower() == trigger_city.lower()
        if city_match:
            return {
                "valid": True,
                "score_impact": 0,
                "flag": None,
                "details": {"distance_km": 0.0, "zone_status": "CITY_MATCH_ONLY", "max_radius_km": 0.0},
            }
        else:
            return {
                "valid": False,
                "score_impact": -40,
                "flag": f"⚠️ City mismatch: worker in {worker_city}, trigger in {trigger_city} (no GPS)",
                "details": {"distance_km": None, "zone_status": "CITY_MISMATCH_NO_GPS", "max_radius_km": 0.0},
            }


# ═════════════════════════════════════════════════════════════════
# MODULE 2: SHIFT-TIME VALIDATOR
# ═════════════════════════════════════════════════════════════════
def check_shift_time(worker_shift: str, claim_hour: int) -> dict:
    """
    Validate that claim was filed during worker's declared shift hours.
    Uses ±1 hour grace period for shift boundaries.
    """
    shift_range = SHIFT_WINDOWS.get(worker_shift)
    if not shift_range:
        # Unknown shift — can't validate, no penalty
        return {"valid": True, "score_impact": 0, "flag": None,
                "details": {"shift": worker_shift, "claim_hour": claim_hour, "status": "UNKNOWN_SHIFT"}}

    shift_start, shift_end = shift_range
    in_shift = _is_hour_in_shift(claim_hour, shift_start, shift_end)

    if in_shift:
        return {"valid": True, "score_impact": 0, "flag": None,
                "details": {"shift": worker_shift, "claim_hour": claim_hour, "window": f"{shift_start}:00-{shift_end}:00", "status": "WITHIN_SHIFT"}}
    else:
        return {
            "valid": False,
            "score_impact": -20,
            "flag": f"⚠️ Off-hours claim: filed at {claim_hour}:00, shift is {worker_shift}",
            "details": {"shift": worker_shift, "claim_hour": claim_hour, "window": f"{shift_start}:00-{shift_end}:00", "status": "OUTSIDE_SHIFT"},
        }


# ═════════════════════════════════════════════════════════════════
# MODULE 3: HISTORICAL WEATHER CROSS-VALIDATOR
# ═════════════════════════════════════════════════════════════════
WEATHER_TRIGGER_TYPES = {"Heavy Rainfall", "Extreme Rain / Flooding", "Severe Heatwave", "Hazardous AQI"}


def check_weather_history(trigger_type: str, trigger_city: str,
                          measured_value: float, trigger_description: str = None,
                          db=None) -> dict:
    """
    Cross-validate trigger's measured value against historical trigger data.
    Flags if the value is wildly inconsistent with recent history for that city+type.
    Skips validation for demo/simulated triggers.
    """
    # Skip for non-weather triggers
    if trigger_type not in WEATHER_TRIGGER_TYPES:
        return {"valid": True, "score_impact": 0, "flag": None,
                "details": {"status": "NON_WEATHER_TRIGGER"}}

    # Skip for simulated triggers (demo mode)
    if trigger_description and "Simulated" in trigger_description:
        return {"valid": True, "score_impact": 0, "flag": None,
                "details": {"status": "SIMULATED_TRIGGER_SKIPPED"}}

    if db is None:
        return {"valid": True, "score_impact": 0, "flag": None,
                "details": {"status": "NO_DB_SESSION"}}

    # Query last 5 trigger events of same type+city (excluding current)
    from models import TriggerEvent
    recent_triggers = (
        db.query(TriggerEvent)
        .filter(
            TriggerEvent.trigger_type == trigger_type,
            TriggerEvent.city == trigger_city,
            TriggerEvent.detected_at >= (datetime.utcnow() - timedelta(days=30)),
        )
        .order_by(TriggerEvent.detected_at.desc())
        .limit(6)  # Get 6 to exclude current one
        .all()
    )

    # Need at least 2 historical data points to validate
    historical_values = [t.measured_value for t in recent_triggers if t.measured_value and t.measured_value > 0]
    if len(historical_values) < 2:
        return {"valid": True, "score_impact": 0, "flag": None,
                "details": {"status": "INSUFFICIENT_HISTORY", "data_points": len(historical_values)}}

    # Calculate median of historical values
    sorted_vals = sorted(historical_values)
    median_val = sorted_vals[len(sorted_vals) // 2]

    # Flag if current value is >3x or <0.3x the historical median
    if median_val > 0:
        ratio = measured_value / median_val
        if ratio >= 3.0 or ratio <= 0.3:
            return {
                "valid": False,
                "score_impact": -35,
                "flag": f"⚠️ Weather anomaly: {measured_value} vs historical median {median_val:.1f} ({ratio:.1f}x)",
                "details": {"status": "WEATHER_ANOMALY", "measured": measured_value,
                           "median": round(median_val, 1), "ratio": round(ratio, 2),
                           "data_points": len(historical_values)},
            }

    return {"valid": True, "score_impact": 0, "flag": None,
            "details": {"status": "WEATHER_CONFIRMED", "measured": measured_value,
                       "median": round(median_val, 1) if median_val > 0 else 0,
                       "data_points": len(historical_values)}}


# ═════════════════════════════════════════════════════════════════
# MODULE 4: VELOCITY ANOMALY DETECTOR
# ═════════════════════════════════════════════════════════════════
def check_velocity_anomaly(worker_last_claim_city: Optional[str],
                           worker_last_claim_at: Optional[datetime],
                           current_claim_city: str,
                           current_claim_time: datetime) -> dict:
    """
    Detect impossible travel by checking distance/time between consecutive claims.
    If a worker claimed in Mumbai and now claims in Delhi 30 minutes later, that's
    physically impossible by road/rail.
    """
    if not worker_last_claim_city or not worker_last_claim_at:
        return {"valid": True, "score_impact": 0, "flag": None,
                "details": {"status": "NO_PRIOR_CLAIM", "velocity_kmh": 0}}

    # Same city — no velocity issue
    if worker_last_claim_city.lower() == current_claim_city.lower():
        return {"valid": True, "score_impact": 0, "flag": None,
                "details": {"status": "SAME_CITY", "velocity_kmh": 0}}

    # Calculate inter-city distance
    city1 = CITY_COORDINATES.get(worker_last_claim_city)
    city2 = CITY_COORDINATES.get(current_claim_city)
    if not city1 or not city2:
        return {"valid": True, "score_impact": 0, "flag": None,
                "details": {"status": "UNKNOWN_CITY", "velocity_kmh": 0}}

    distance_km = haversine_km(city1["lat"], city1["lng"], city2["lat"], city2["lng"])
    time_delta = (current_claim_time - worker_last_claim_at).total_seconds() / 3600.0  # hours

    if time_delta <= 0:
        time_delta = 0.01  # Prevent division by zero

    velocity_kmh = round(distance_km / time_delta, 1)

    if velocity_kmh >= 200:
        # Impossible by any road transport in India
        return {
            "valid": False,
            "score_impact": -30,
            "flag": f"⚠️ Impossible travel: {distance_km}km in {time_delta:.1f}h ({velocity_kmh}km/h)",
            "details": {"status": "IMPOSSIBLE_TRAVEL", "distance_km": distance_km,
                       "time_hours": round(time_delta, 2), "velocity_kmh": velocity_kmh,
                       "from_city": worker_last_claim_city, "to_city": current_claim_city},
        }
    elif velocity_kmh >= 80 and time_delta < 2:
        # Suspicious but not impossible (could be domestic flight)
        return {
            "valid": True,
            "score_impact": -15,
            "flag": f"⚠️ Suspicious travel: {distance_km}km in {time_delta:.1f}h ({velocity_kmh}km/h)",
            "details": {"status": "SUSPICIOUS_TRAVEL", "distance_km": distance_km,
                       "time_hours": round(time_delta, 2), "velocity_kmh": velocity_kmh,
                       "from_city": worker_last_claim_city, "to_city": current_claim_city},
        }
    else:
        return {"valid": True, "score_impact": 0, "flag": None,
                "details": {"status": "NORMAL_TRAVEL", "distance_km": distance_km,
                           "time_hours": round(time_delta, 2), "velocity_kmh": velocity_kmh}}


# ═════════════════════════════════════════════════════════════════
# MODULE 5: BEHAVIORAL PATTERN ANALYZER
# ═════════════════════════════════════════════════════════════════
def check_behavioral_pattern(worker_claim_history: int,
                             worker_fraud_flag_count: int,
                             same_week_claims: int,
                             db=None) -> dict:
    """
    Analyze worker's claim patterns for fraud indicators:
    - Frequency anomaly (claims way above platform average)
    - Repeat offender (accumulated fraud flags)
    - Escalation pattern (high lifetime + high recent claims)
    """
    total_impact = 0
    flags = []
    details = {
        "same_week_claims": same_week_claims,
        "lifetime_claims": worker_claim_history,
        "fraud_flag_count": worker_fraud_flag_count,
    }

    # Calculate platform average if DB available
    platform_avg = 0.5  # default fallback
    if db:
        from models import Claim, Worker
        four_weeks_ago = datetime.utcnow() - timedelta(days=28)
        total_recent_claims = db.query(Claim).filter(Claim.created_at >= four_weeks_ago).count()
        active_workers = db.query(Worker).filter(Worker.is_active == True).count()
        if active_workers > 0:
            platform_avg = max(0.1, total_recent_claims / (active_workers * 4))  # per week
    details["platform_avg_per_week"] = round(platform_avg, 2)

    # Frequency analysis
    if same_week_claims > 0 and platform_avg > 0:
        frequency_ratio = same_week_claims / platform_avg
        details["frequency_ratio"] = round(frequency_ratio, 1)

        if frequency_ratio >= 5:
            total_impact -= 25
            flags.append(f"⚠️ Extreme frequency: {same_week_claims} claims this week ({frequency_ratio:.1f}x platform avg)")
        elif frequency_ratio >= 3:
            total_impact -= 15
            flags.append(f"⚠️ High frequency: {same_week_claims} claims this week ({frequency_ratio:.1f}x platform avg)")
        elif same_week_claims > 0:
            total_impact -= min(20, same_week_claims * 10)
            if same_week_claims >= 2:
                flags.append(f"⚠️ {same_week_claims} other claim(s) this week")
    elif same_week_claims > 0:
        # No platform avg available, use basic penalty
        total_impact -= min(20, same_week_claims * 10)
        if same_week_claims >= 2:
            flags.append(f"⚠️ {same_week_claims} other claim(s) this week")

    # Repeat offender detection
    if worker_fraud_flag_count >= 3:
        total_impact -= 20
        flags.append(f"⚠️ Repeat offender: {worker_fraud_flag_count} prior fraud flags")
    details["repeat_offender"] = worker_fraud_flag_count >= 3

    # Escalation pattern: high lifetime + high recent
    if worker_claim_history > 5 and same_week_claims >= 3:
        total_impact -= 10
        flags.append(f"⚠️ Escalation pattern: {worker_claim_history} lifetime claims + {same_week_claims} this week")
    elif worker_claim_history > 5:
        total_impact -= 10
        flags.append(f"⚠️ High claim history ({worker_claim_history} total)")
    details["escalation_detected"] = worker_claim_history > 5 and same_week_claims >= 3

    # Combine flags into single string if multiple
    combined_flag = "; ".join(flags) if flags else None

    return {
        "valid": total_impact >= -10,  # Minor penalties are still "valid"
        "score_impact": total_impact,
        "flag": combined_flag,
        "details": details,
    }


# ═════════════════════════════════════════════════════════════════
# MODULE 6: CROSS-CLAIM DEDUPLICATOR
# ═════════════════════════════════════════════════════════════════
def check_cross_claim_dedup(worker_id: int, trigger_event_id: int,
                            worker_upi_id: Optional[str] = None,
                            db=None) -> dict:
    """
    Enhanced duplicate detection:
    - Same trigger event → same worker (already prevented upstream, this is defense-in-depth)
    - Same UPI ID across different workers (fraud ring detection)
    """
    if db is None:
        return {"valid": True, "score_impact": 0, "flag": None,
                "details": {"status": "NO_DB_SESSION"}}

    from models import Claim

    # Check same trigger event + same worker (should be caught upstream, but defense-in-depth)
    existing_for_trigger = (
        db.query(Claim)
        .filter(Claim.worker_id == worker_id, Claim.trigger_event_id == trigger_event_id)
        .first()
    )
    if existing_for_trigger:
        return {
            "valid": False,
            "score_impact": -50,
            "flag": f"⚠️ Duplicate claim: already claimed for this trigger event",
            "details": {"status": "DUPLICATE_TRIGGER_CLAIM", "existing_claim": existing_for_trigger.claim_number},
        }

    # Check UPI ID reuse across workers (fraud ring detection)
    if worker_upi_id:
        upi_reuse = (
            db.query(Claim)
            .filter(
                Claim.upi_id == worker_upi_id,
                Claim.worker_id != worker_id,
                Claim.created_at >= (datetime.utcnow() - timedelta(days=30)),
            )
            .first()
        )
        if upi_reuse:
            return {
                "valid": False,
                "score_impact": -50,
                "flag": f"⚠️ UPI fraud: same UPI ID used by different worker",
                "details": {"status": "UPI_CROSS_WORKER", "other_worker_id": upi_reuse.worker_id},
            }

    return {"valid": True, "score_impact": 0, "flag": None,
            "details": {"status": "NO_DUPLICATES"}}


# ═════════════════════════════════════════════════════════════════
# MASTER ORCHESTRATOR — Runs all 6 modules
# ═════════════════════════════════════════════════════════════════
def compute_advanced_fraud_score(
    worker,           # Worker ORM object
    trigger_event,    # TriggerEvent ORM object
    claim_lat: Optional[float] = None,
    claim_lng: Optional[float] = None,
    same_week_claims: int = 0,
    db=None,
) -> dict:
    """
    Production-ready fraud assessment running all 6 detection modules.

    Returns enriched dict compatible with existing compute_authenticity_score() output,
    plus advanced fraud metadata for Phase 3.

    Score starts at 100, each module subtracts its penalty.
    Final score clamped to [0, 100].
    """
    now = datetime.utcnow()
    claim_hour = now.hour
    score = 100.0
    all_flags = []
    module_results = {}

    # ── Module 1: GPS Spoofing ────────────────────────────────────
    is_simulated = getattr(trigger_event, 'is_simulated', False) or (trigger_event.description and "Simulated" in trigger_event.description)
    
    if is_simulated:
        # [Phase 3 Demo Override] Bypass hard GPS geofence so judges can experience the fund transfer from anywhere
        gps_result = {
            "valid": True,
            "score_impact": 0,
            "flag": None,
            "details": {"distance_km": 0.0, "zone_status": "SIMULATED_BYPASS", "max_radius_km": 0.0},
        }
    else:
        gps_result = check_gps_spoofing(
            worker_city=worker.city,
            trigger_city=trigger_event.city,
            claim_lat=claim_lat,
            claim_lng=claim_lng,
            worker_home_lat=worker.home_lat,
            worker_home_lng=worker.home_lng,
        )
        
    score += gps_result["score_impact"]
    if gps_result["flag"]:
        all_flags.append(gps_result["flag"])
    module_results["gps"] = gps_result

    # ── Module 2: Shift-Time ──────────────────────────────────────
    shift_result = check_shift_time(
        worker_shift=worker.shift or "Evening Peak (6PM-2AM)",
        claim_hour=claim_hour,
    )
    score += shift_result["score_impact"]
    if shift_result["flag"]:
        all_flags.append(shift_result["flag"])
    module_results["shift"] = shift_result

    # ── Module 3: Historical Weather ──────────────────────────────
    weather_result = check_weather_history(
        trigger_type=trigger_event.trigger_type,
        trigger_city=trigger_event.city,
        measured_value=trigger_event.measured_value,
        trigger_description=trigger_event.description,
        db=db,
    )
    score += weather_result["score_impact"]
    if weather_result["flag"]:
        all_flags.append(weather_result["flag"])
    module_results["weather"] = weather_result

    # ── Module 4: Velocity Anomaly ────────────────────────────────
    velocity_result = check_velocity_anomaly(
        worker_last_claim_city=worker.last_claim_city,
        worker_last_claim_at=worker.last_claim_at,
        current_claim_city=trigger_event.city,
        current_claim_time=now,
    )
    score += velocity_result["score_impact"]
    if velocity_result["flag"]:
        all_flags.append(velocity_result["flag"])
    module_results["velocity"] = velocity_result

    # ── Module 5: Behavioral Pattern ──────────────────────────────
    pattern_result = check_behavioral_pattern(
        worker_claim_history=worker.claim_history_count or 0,
        worker_fraud_flag_count=worker.fraud_flag_count or 0,
        same_week_claims=same_week_claims,
        db=db,
    )
    score += pattern_result["score_impact"]
    if pattern_result["flag"]:
        all_flags.append(pattern_result["flag"])
    module_results["pattern"] = pattern_result

    # ── Module 6: Cross-Claim Dedup ───────────────────────────────
    dedup_result = check_cross_claim_dedup(
        worker_id=worker.id,
        trigger_event_id=trigger_event.id,
        worker_upi_id=worker.phone,  # Use phone as UPI proxy
        db=db,
    )
    score += dedup_result["score_impact"]
    if dedup_result["flag"]:
        all_flags.append(dedup_result["flag"])
    module_results["dedup"] = dedup_result

    # ── Clamp and decide ──────────────────────────────────────────
    score = max(0.0, min(100.0, round(score, 1)))

    if score >= 75:
        decision = "AUTO_APPROVED"
        decision_label = "✅ Auto-Approved"
        risk_tier = "LOW"
    elif score >= 45:
        decision = "PENDING_REVIEW"
        decision_label = "⏳ Escrow Hold (2hr review)"
        risk_tier = "MEDIUM"
    elif score >= 20:
        decision = "MANUAL_REVIEW"
        decision_label = "🔍 Manual Review (24hr)"
        risk_tier = "HIGH"
    else:
        decision = "MANUAL_REVIEW"
        decision_label = "🚨 Critical Risk — Manual Review"
        risk_tier = "CRITICAL"

    # ── ML augmentation ───────────────────────────────────────────
    ml_available = False
    ml_score = score
    ml_fraud_probability = None
    ml_confidence = None
    ml_top_signals = []

    try:
        from ml.fraud_model import get_ml_fraud_decision
        gps_distance = gps_result["details"].get("distance_km", 0) or 0
        max_radius = gps_result["details"].get("max_radius_km", 30) or 30
        gps_dist_norm = min(3.0, gps_distance / max_radius) if max_radius > 0 else 0
        velocity_kmh = velocity_result["details"].get("velocity_kmh", 0) or 0

        ml_result = get_ml_fraud_decision(
            city_match=gps_result["valid"],
            device_attested=True,
            same_week_claims=same_week_claims,
            claim_history_count=worker.claim_history_count or 0,
            hour_of_day=claim_hour,
            trigger_type=trigger_event.trigger_type,
            payout_amount=None,  # Set by caller
            disruption_streak=worker.disruption_streak or 0,
            gps_distance_norm=gps_dist_norm,
            shift_overlap=1.0 if shift_result["valid"] else 0.0,
            claim_velocity_norm=min(1.0, velocity_kmh / 200.0),
            fraud_history_norm=min(1.0, (worker.fraud_flag_count or 0) / 10.0),
        )
        ml_score = ml_result["ml_score"]
        ml_fraud_probability = ml_result["fraud_probability"]
        ml_confidence = ml_result.get("model_confidence")
        ml_top_signals = ml_result.get("top_signals", [])
        ml_available = True
    except Exception as e:
        print(f"[FraudEngine] ML model unavailable: {e}")

    # ── Build response (compatible with existing format) ──────────
    gps_distance_km = gps_result["details"].get("distance_km")

    return {
        # Existing fields (backward compatible)
        "score": score,
        "decision": decision,
        "decision_label": decision_label,
        "flags": all_flags,
        "gps_valid": gps_result["valid"],
        "activity_valid": same_week_claims == 0,
        "device_valid": True,
        "cross_source_valid": dedup_result["valid"],

        # ML augmentation
        "ml_available": ml_available,
        "ml_score": round(ml_score, 1) if ml_available else None,
        "ml_fraud_probability": ml_fraud_probability,
        "ml_confidence": ml_confidence,
        "ml_top_signals": ml_top_signals,

        # Advanced Phase 3 fields
        "risk_tier": risk_tier,
        "gps_distance_km": gps_distance_km,
        "claim_lat": claim_lat,
        "claim_lng": claim_lng,
        "shift_valid": shift_result["valid"],
        "weather_cross_valid": weather_result["valid"],
        "velocity_valid": velocity_result["valid"],

        # Module detail (for admin panel)
        "module_results": module_results,
    }
