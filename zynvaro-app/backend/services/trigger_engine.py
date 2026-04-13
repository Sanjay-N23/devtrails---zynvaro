"""
Zynvaro — Parametric Trigger Engine
Monitors 5 trigger types. Uses real OpenWeatherMap API + mocks for others.
Each trigger: detects → validates (dual-source) → fires claim batch.
"""

import os
import httpx
import asyncio
import random
from datetime import datetime, timedelta
from typing import Optional

OPENWEATHER_API_KEY = os.getenv("OPENWEATHER_API_KEY", "")
WAQI_API_TOKEN      = os.getenv("WAQI_API_TOKEN", "")

# ─────────────────────────────────────────────────────────────────
# CITY COORDINATES
# ─────────────────────────────────────────────────────────────────
CITY_COORDS = {
    "Mumbai":    {"lat": 19.0760, "lon": 72.8777},
    "Delhi":     {"lat": 28.6139, "lon": 77.2090},
    "Bangalore": {"lat": 12.9716, "lon": 77.5946},
    "Hyderabad": {"lat": 17.3850, "lon": 78.4867},
    "Chennai":   {"lat": 13.0827, "lon": 80.2707},
    "Pune":      {"lat": 18.5204, "lon": 73.8567},
    "Kolkata":   {"lat": 22.5726, "lon": 88.3639},
}

# ─────────────────────────────────────────────────────────────────
# TRIGGER THRESHOLDS
# ─────────────────────────────────────────────────────────────────
TRIGGERS = {
    "Heavy Rainfall": {
        "threshold": 64.5,
        "unit": "mm/24hr",
        "source_primary": "OpenWeatherMap",
        "source_secondary": "IMD API (mock)",
    },
    "Extreme Rain / Flooding": {
        "threshold": 204.5,
        "unit": "mm/24hr",
        "source_primary": "OpenWeatherMap",
        "source_secondary": "NDMA SACHET (mock)",
    },
    "Severe Heatwave": {
        "threshold": 45.0,
        "unit": "°C",
        "source_primary": "OpenWeatherMap",
        "source_secondary": "IMD Bulletins (mock)",
    },
    "Hazardous AQI": {
        "threshold": 400.0,
        "unit": "AQI",
        "source_primary": "WAQI API (mock)",
        "source_secondary": "CPCB Stations (mock)",
    },
    "Platform Outage": {
        "threshold": 15.0,
        "unit": "minutes down",
        "source_primary": "Synthetic Monitors",
        "source_secondary": "Downdetector (mock)",
    },
    "Civil Disruption": {
        "threshold": 4.0,
        "unit": "hours restricted",
        "source_primary": "GDELT API (mock)",
        "source_secondary": "NewsAPI (mock)",
    },
}

# ─────────────────────────────────────────────────────────────────
# REAL WEATHER FETCH (OpenWeatherMap)
# ─────────────────────────────────────────────────────────────────
async def fetch_real_weather(city: str) -> dict:
    """Fetch current weather from OpenWeatherMap."""
    coords = CITY_COORDS.get(city)
    if coords is None:
        print(f"[TriggerEngine] WARNING: City '{city}' not in CITY_COORDS, defaulting to Bangalore")
        coords = CITY_COORDS["Bangalore"]
    url = (
        f"https://api.openweathermap.org/data/2.5/weather"
        f"?lat={coords['lat']}&lon={coords['lon']}"
        f"&appid={OPENWEATHER_API_KEY}&units=metric"
    )
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                rain_1h = data.get("rain", {}).get("1h", 0)
                rain_3h = data.get("rain", {}).get("3h", 0)
                return {
                    "temp": data["main"]["temp"],
                    "rain_1h_mm": rain_1h,
                    "rain_3h_mm": rain_3h,
                    # Dampened 24h estimate: take the worse of 1h and 3h extrapolations,
                    # then apply 0.4 dampening factor to avoid false positives.
                    # Still an approximation — proper 24h accumulation requires forecast API.
                    "rain_24h_mm": max(rain_1h * 24, rain_3h * 8) * 0.4,
                    "description": data["weather"][0]["description"],
                    "source": "OpenWeatherMap (live)",
                }
    except Exception as e:
        print(f"[TriggerEngine] Weather API error for {city}: {e}")
    return None


async def fetch_real_aqi(city: str) -> float:
    """
    Fetch live AQI from WAQI (World Air Quality Index) API.
    Uses geo-based endpoint (lat/lon) — more reliable than city name slugs.
    Falls back to mock_aqi() if token missing or API unreachable.
    Get a free token at: https://aqicn.org/data-platform/token/
    """
    if not WAQI_API_TOKEN:
        return None  # no token → caller uses mock

    coords = CITY_COORDS.get(city, CITY_COORDS["Bangalore"])
    url = (
        f"https://api.waqi.info/feed/geo:{coords['lat']};{coords['lon']}/"
        f"?token={WAQI_API_TOKEN}"
    )
    try:
        async with httpx.AsyncClient(timeout=5.0) as client:
            r = await client.get(url)
            if r.status_code == 200:
                data = r.json()
                if data.get("status") == "ok":
                    aqi_val = data["data"].get("aqi", None)
                    if aqi_val is not None and isinstance(aqi_val, (int, float)):
                        print(f"[AQI] {city} live AQI = {aqi_val}")
                        return float(aqi_val)
    except Exception as e:
        print(f"[TriggerEngine] WAQI API error for {city}: {e}")
    return None


async def fetch_real_platform_status(platform: str) -> dict:
    """
    Check if a delivery platform is reachable via HTTP HEAD.
    Timeout > 6s or 5xx status → treat as DOWN.
    No API key required — just tests real reachability.
    """
    PLATFORM_URLS = {
        "Blinkit":   "https://blinkit.com",
        "Zepto":     "https://zeptonow.com",
        "Instamart": "https://www.swiggy.com",
        "Zomato":    "https://www.zomato.com",
        "Swiggy":    "https://www.swiggy.com",
        "Amazon":    "https://www.amazon.in",
        "Flipkart":  "https://www.flipkart.com",
    }
    url = PLATFORM_URLS.get(platform)
    if not url:
        return None  # unknown platform → caller uses mock

    import time
    start = time.monotonic()
    try:
        async with httpx.AsyncClient(timeout=6.0, follow_redirects=True) as client:
            r = await client.head(url)
        latency_ms = int((time.monotonic() - start) * 1000)
        is_down = r.status_code >= 500
        print(f"[Platform] {platform} HEAD -> {r.status_code} in {latency_ms}ms")
        return {
            "platform": platform,
            "status": "DOWN" if is_down else "UP",
            "latency_ms": latency_ms,
            "error_rate": 0.95 if is_down else 0.0,
            "http_status": r.status_code,
            "checked_at": datetime.utcnow().isoformat(),
            "source": "HTTP HEAD probe (live)",
        }
    except httpx.TimeoutException:
        latency_ms = int((time.monotonic() - start) * 1000)
        print(f"[Platform] {platform} HEAD timed out after {latency_ms}ms → DOWN")
        return {
            "platform": platform,
            "status": "DOWN",
            "latency_ms": latency_ms,
            "error_rate": 1.0,
            "http_status": 0,
            "checked_at": datetime.utcnow().isoformat(),
            "source": "HTTP HEAD probe (live)",
        }
    except Exception as e:
        print(f"[TriggerEngine] Platform probe error for {platform}: {e}")
        return None


async def fetch_civil_disruption_live(city: str) -> dict:
    """
    Query GDELT Project DOC 2.0 API for civil disruption news in a city.
    No API key required — GDELT is a free, open data platform.
    Returns active=True if >= 3 disruption articles found in the last 6 hours.
    https://blog.gdeltproject.org/gdelt-doc-2-0-api-debuts/
    """
    params = {
        "query":      f"(India OR {city}) AND (bandh OR protest OR strike OR curfew)",
        "mode":       "ArtList",
        "maxrecords": "25",
        "format":     "json",
        "timespan":   "6H",
        "sort":       "date",
    }
    try:
        async with httpx.AsyncClient(timeout=12.0) as client:
            r = await client.get("https://api.gdeltproject.org/api/v2/doc/doc", params=params)
            if r.status_code == 200:
                data = r.json()
                articles = data.get("articles") or []
                # Use articles_found if present (GDELT total count), else len of returned list
                count = data.get("articles_found") or len(articles)
                print(f"[GDELT] {city} — {count} disruption articles in last 6h")

                is_active = count >= 3
                # Extract disruption type hint from first headline if available
                disruption_type = None
                if articles:
                    title = articles[0].get("title", "").lower()
                    if "bandh" in title:
                        disruption_type = "Bandh"
                    elif "protest" in title:
                        disruption_type = "Protest"
                    elif "curfew" in title or "144" in title:
                        disruption_type = "Section 144 / Curfew"
                    elif "strike" in title:
                        disruption_type = "Strike"
                    else:
                        disruption_type = "Civil Disruption"

                return {
                    "city": city,
                    "active_restrictions": is_active,
                    "type": disruption_type if is_active else None,
                    "duration_hours": 6.0 if is_active else 0,
                    "article_count": count,
                    "source": "GDELT Project v2 (live)",
                }
    except Exception as e:
        print(f"[TriggerEngine] GDELT API error for {city}: {type(e).__name__}: {e}")
    return None


# ─────────────────────────────────────────────────────────────────
# MOCK DATA GENERATORS (for demo / fallback)
# ─────────────────────────────────────────────────────────────────
def mock_weather(city: str, scenario: str = "normal") -> dict:
    """Generate realistic mock weather data for demo."""
    scenarios = {
        "normal":   {"temp": 28, "rain_24h_mm": 5,    "aqi": 85},
        "rain":     {"temp": 24, "rain_24h_mm": 72,   "aqi": 60},
        "flooding": {"temp": 22, "rain_24h_mm": 215,  "aqi": 55},
        "heatwave": {"temp": 46, "rain_24h_mm": 0,    "aqi": 120},
        "aqi":      {"temp": 18, "rain_24h_mm": 0,    "aqi": 485},
    }
    return scenarios.get(scenario, scenarios["normal"])

def mock_aqi(city: str) -> float:
    """Mock AQI values (higher in Delhi winter)."""
    base = {"Delhi": 280, "Mumbai": 120, "Bangalore": 95,
            "Hyderabad": 110, "Chennai": 100, "Pune": 115}
    return base.get(city, 100) + random.uniform(-20, 40)

def mock_platform_status(platform: str) -> dict:
    """Mock platform uptime check."""
    # 5% chance of outage for demo realism
    is_down = random.random() < 0.05
    return {
        "platform": platform,
        "status": "DOWN" if is_down else "UP",
        "latency_ms": 9999 if is_down else random.randint(120, 350),
        "error_rate": 0.95 if is_down else random.uniform(0, 0.02),
        "checked_at": datetime.utcnow().isoformat(),
    }

def mock_civil_disruption(city: str) -> dict:
    """Mock civil disruption check via GDELT-style feed."""
    # ~8% chance of active disruption for demo realism
    is_active = random.random() < 0.08
    disruption_types = ["Protest / Bandh", "Section 144 Order", "Communal Tension", "Transport Strike"]
    return {
        "city": city,
        "active_restrictions": is_active,
        "type": random.choice(disruption_types) if is_active else None,
        "duration_hours": random.uniform(4.5, 10.0) if is_active else 0,
        "source": "GDELT (mock)",
    }

# ─────────────────────────────────────────────────────────────────
# TRIGGER EVALUATION
# ─────────────────────────────────────────────────────────────────
async def check_all_triggers(city: str, platform: str = "Blinkit") -> list[dict]:
    """
    Check all 5 triggers for a city.
    Returns list of fired triggers (empty if none).
    """
    fired = []

    # ── Fetch all 4 live data sources concurrently (H13: was sequential ~28s worst case) ──
    weather_result, aqi_result, platform_result, disruption_result = await asyncio.gather(
        fetch_real_weather(city),
        fetch_real_aqi(city),
        fetch_real_platform_status(platform),
        fetch_civil_disruption_live(city),
        return_exceptions=True,
    )
    # Treat exceptions as None (fallback to mock)
    live_weather = weather_result if not isinstance(weather_result, Exception) else None
    live_aqi = aqi_result if not isinstance(aqi_result, Exception) else None
    live_platform = platform_result if not isinstance(platform_result, Exception) else None
    live_disruption = disruption_result if not isinstance(disruption_result, Exception) else None

    if live_weather is None:
        # Fallback to mock — use "normal" unless demo mode
        live_weather = mock_weather(city, "normal")

    # ── TRIGGER 1: Heavy Rainfall
    rain_24h = live_weather.get("rain_24h_mm", 0)
    if rain_24h >= TRIGGERS["Heavy Rainfall"]["threshold"]:
        fired.append(_make_trigger("Heavy Rainfall", city, rain_24h, "high"))
    elif rain_24h >= 20:
        # Near-miss — useful for showing the system is monitoring
        pass

    # ── TRIGGER 2: Extreme Rain / Flooding
    if rain_24h >= TRIGGERS["Extreme Rain / Flooding"]["threshold"]:
        fired.append(_make_trigger("Extreme Rain / Flooding", city, rain_24h, "extreme"))

    # ── TRIGGER 3: Severe Heatwave
    temp = live_weather.get("temp", 30)
    if temp >= TRIGGERS["Severe Heatwave"]["threshold"]:
        fired.append(_make_trigger("Severe Heatwave", city, temp, "high"))

    # ── TRIGGER 4: Hazardous AQI — live WAQI, fallback to mock
    aqi = live_aqi if live_aqi is not None else mock_aqi(city)
    aqi_source = "WAQI API (live)" if live_aqi is not None else "Mock (no WAQI token)"
    if aqi >= TRIGGERS["Hazardous AQI"]["threshold"]:
        fired.append(_make_trigger("Hazardous AQI", city, aqi, "high",
                                   desc=f"AQI {aqi:.0f} in {city} exceeds hazardous threshold ({TRIGGERS['Hazardous AQI']['threshold']}) — source: {aqi_source}"))

    # ── TRIGGER 5: Platform Outage — live HTTP HEAD probe, fallback to mock
    platform_status = live_platform if live_platform is not None else mock_platform_status(platform)
    if platform_status["status"] == "DOWN":
        latency = platform_status.get("latency_ms", 9999)
        src = platform_status.get("source", "probe")
        fired.append(_make_trigger("Platform Outage", city, latency / 1000, "high",
                                   desc=f"{platform} unreachable — {latency}ms response ({src})"))

    # ── TRIGGER 6: Civil Disruption — live GDELT, fallback to mock
    disruption = live_disruption if live_disruption is not None else mock_civil_disruption(city)
    if disruption["active_restrictions"] and disruption["duration_hours"] >= TRIGGERS["Civil Disruption"]["threshold"]:
        articles_note = f" ({disruption.get('article_count', '')} GDELT articles)" if live_disruption else ""
        fired.append(_make_trigger("Civil Disruption", city, disruption["duration_hours"], "high",
                                   desc=f"{disruption.get('type', 'Disruption')} in {city}: movement restricted for {disruption['duration_hours']:.1f}h{articles_note}"))

    return fired


def simulate_trigger(trigger_type: str, city: str) -> dict:
    """
    Force-fire a specific trigger for demo purposes.
    Used in the /triggers/simulate endpoint.
    """
    demo_values = {
        "Heavy Rainfall":          72.5,
        "Extreme Rain / Flooding": 210.0,
        "Severe Heatwave":         46.2,
        "Hazardous AQI":           485.0,
        "Platform Outage":         20.0,
        "Civil Disruption":        6.0,
    }
    value = demo_values.get(trigger_type, 100.0)
    return _make_trigger(trigger_type, city, value, "high",
                         desc=f"Simulated {trigger_type} in {city} — parametric trigger fired")


def _make_trigger(trigger_type: str, city: str, value: float,
                  severity: str, desc: str = None) -> dict:
    cfg = TRIGGERS.get(trigger_type, {})
    threshold = cfg.get("threshold", 0)
    return {
        "trigger_type": trigger_type,
        "city": city,
        "measured_value": round(value, 2),
        "threshold_value": threshold,
        "unit": cfg.get("unit", ""),
        "source_primary": cfg.get("source_primary", "API"),
        "source_secondary": cfg.get("source_secondary", "Mock"),
        "is_validated": True,  # Dual-source validation placeholder — both sources checked but secondary is simulated
        "severity": severity,
        "description": desc or f"{trigger_type} threshold exceeded in {city}: {value:.1f} {cfg.get('unit','')} (threshold: {threshold})",
        "detected_at": datetime.utcnow().isoformat(),
        "expires_at": (datetime.utcnow() + timedelta(hours=6)).isoformat(),
    }

# ─────────────────────────────────────────────────────────────────
# FRAUD SCORER (ML-Enhanced)
# ─────────────────────────────────────────────────────────────────
def compute_authenticity_score(
    worker_city: str,
    trigger_city: str,
    claim_history: int = 0,
    same_week_claims: int = 0,
    device_attested: bool = True,
    trigger_type: str = None,
    payout_amount: float = None,
    disruption_streak: int = 0,
    # Phase 3 advanced parameters (when provided, uses full fraud engine)
    worker=None,
    trigger_event=None,
    claim_lat: float = None,
    claim_lng: float = None,
    db=None,
) -> dict:
    """
    Multi-signal authenticity scoring (0–100) — ML-augmented via RandomForestClassifier.

    Rule-based score drives the decision (for insurance auditability).
    ML score is computed as augmentation and exposed in the response for
    admin transparency, but does not influence the decision.

    Score >= 75 → Auto-approve
    Score 45–74 → Escrow hold (2hr review)
    Score < 45  → Manual review

    The ML model (200-tree RandomForest, trained on 2,000 synthetic samples)
    captures non-linear interactions (e.g. late-night + city mismatch + high payout
    = extreme fraud risk) and surfaces them as admin-visible signals.
    """
    # ── Phase 3: Delegate to advanced fraud engine when full data available ──
    if worker is not None and trigger_event is not None:
        try:
            from services.fraud_engine import compute_advanced_fraud_score
            return compute_advanced_fraud_score(
                worker=worker,
                trigger_event=trigger_event,
                claim_lat=claim_lat,
                claim_lng=claim_lng,
                same_week_claims=same_week_claims,
                db=db,
            )
        except Exception as e:
            print(f"[FraudEngine] Advanced fraud scoring failed, falling back to rule-based: {e}")

    # ── Legacy fallback: Rule-based scoring (backward compatible) ─────────
    city_match = worker_city.lower() == trigger_city.lower()

    # ── Rule-based score (original logic, kept for auditability) ──────────
    rule_score = 100.0
    flags = []

    if not city_match:
        rule_score -= 40
        flags.append("⚠️ Worker city doesn't match trigger city")
    if not device_attested:
        rule_score -= 20
        flags.append("⚠️ Device attestation failed")
    if same_week_claims > 0:
        rule_score -= min(20, same_week_claims * 10)
        flags.append(f"⚠️ {same_week_claims} other claim(s) this week")
    if claim_history > 5:
        rule_score -= 10
        flags.append(f"⚠️ High claim history ({claim_history} total)")
    rule_score = max(0.0, rule_score)

    # ── ML-based score ─────────────────────────────────────────────────────
    try:
        from ml.fraud_model import get_ml_fraud_decision
        ml_result = get_ml_fraud_decision(
            city_match=city_match,
            device_attested=device_attested,
            same_week_claims=same_week_claims,
            claim_history_count=claim_history,
            trigger_type=trigger_type,
            payout_amount=payout_amount,
            disruption_streak=disruption_streak,
        )
        ml_score = ml_result["ml_score"]
        ml_available = True
        # ML flags are stored separately — kept out of rule-based `flags` list
        # to preserve backward compatibility with existing tests and audit logic.
    except Exception as e:
        print(f"[FraudML] ML model unavailable, using rule-based only: {e}")
        ml_score = rule_score
        ml_result = {}
        ml_available = False

    # ── Final score and decision — rule-based for auditability ────────────
    # Insurance compliance requires deterministic, auditable decisions.
    # ML score augments (provides additional signals) but does NOT override
    # the rule-based determination, which is consistent and explainable.
    score = max(0.0, min(100.0, round(rule_score, 1)))

    if score >= 75:
        decision = "AUTO_APPROVED"
        decision_label = "✅ Auto-Approved"
    elif score >= 45:
        decision = "PENDING_REVIEW"
        decision_label = "⏳ Escrow Hold (2hr review)"
    else:
        decision = "MANUAL_REVIEW"
        decision_label = "🔍 Manual Review (24hr)"

    result = {
        "score": score,
        "decision": decision,
        "decision_label": decision_label,
        "flags": flags,
        "gps_valid": city_match,
        "activity_valid": same_week_claims == 0,
        "device_valid": device_attested,
        "cross_source_valid": True,
        # ML augmentation fields (displayed in admin panel, do not affect decision)
        "ml_available": ml_available,
        "ml_score": round(ml_score, 1) if ml_available else None,
    }

    # Include full ML metadata if available
    if ml_available and ml_result:
        result["ml_fraud_probability"] = ml_result.get("fraud_probability")
        result["ml_confidence"] = ml_result.get("model_confidence")
        result["ml_top_signals"] = ml_result.get("top_signals", [])

    return result
