"""
Zynvaro — Dynamic Premium Pricing Engine
Actuarial rule-based pricing engine with AI-driven hyper-local risk factors.
Features: zone_risk (pincode-level), season_index, claim_history_loading,
streak_discount, tier_factor, forecast_risk_adjustment.
Designed for weekly premium calculation aligned with gig worker pay cycles.
"""

import hashlib
from datetime import datetime

# ─────────────────────────────────────────────────────────────────
# ZONE RISK DATABASE (pincode -> risk score)
# ─────────────────────────────────────────────────────────────────
ZONE_RISK_DB = {
    # Mumbai - High flood/rain risk
    "400001": 0.85, "400002": 0.83, "400051": 0.88, "400053": 0.82,
    "400070": 0.79, "400072": 0.80, "400063": 0.87, "400078": 0.84,
    # Delhi - High AQI + heat risk
    "110001": 0.75, "110002": 0.74, "110019": 0.72, "110025": 0.70,
    "110085": 0.68, "110092": 0.71,
    # Bangalore - Moderate flood/rain risk
    "560001": 0.55, "560034": 0.58, "560047": 0.62, "560095": 0.60,
    "560100": 0.53, "560076": 0.57,
    # Hyderabad - Moderate risk
    "500001": 0.50, "500016": 0.52, "500072": 0.55, "500081": 0.48,
    # Chennai - High cyclone + flood risk
    "600001": 0.78, "600020": 0.76, "600041": 0.80, "600119": 0.75,
    # Pune
    "411001": 0.60, "411045": 0.58, "411014": 0.62,
}

# Tier configuration
TIER_CONFIG = {
    "Basic Shield":    {"base": 29.0, "max_daily": 300,  "max_weekly": 600},
    "Standard Guard":  {"base": 49.0, "max_daily": 600,  "max_weekly": 1200},
    "Pro Armor":       {"base": 89.0, "max_daily": 1000, "max_weekly": 2000},
}

# City -> default risk
CITY_RISK_DEFAULT = {
    "Mumbai": 0.82, "Delhi": 0.72, "Bangalore": 0.57,
    "Hyderabad": 0.51, "Chennai": 0.77, "Pune": 0.60,
    "Kolkata": 0.68, "Ahmedabad": 0.55,
}

# Fix B: City-aware seasonal risk profiles
# Winter haze primarily affects North India (Delhi, Kolkata)
# Cyclone season affects coastal cities (Chennai, Mumbai)
CITY_SEASONAL_PROFILE = {
    "Delhi":     {"winter_haze": True, "monsoon": True, "pre_heat": True},
    "Kolkata":   {"winter_haze": True, "monsoon": True, "pre_heat": False},
    "Mumbai":    {"winter_haze": False, "monsoon": True, "pre_heat": False},
    "Chennai":   {"winter_haze": False, "monsoon": True, "pre_heat": False},  # cyclone
    "Bangalore": {"winter_haze": False, "monsoon": True, "pre_heat": False},
    "Hyderabad": {"winter_haze": False, "monsoon": True, "pre_heat": True},
    "Pune":      {"winter_haze": False, "monsoon": True, "pre_heat": False},
}


# ─────────────────────────────────────────────────────────────────
# SEASONAL INDEX (week of year -> risk multiplier)
# Fix B: Now city-aware — winter haze only for North India
# ─────────────────────────────────────────────────────────────────
def get_seasonal_index(date: datetime = None, city: str = None) -> float:
    """
    Returns seasonal risk multiplier (1.0 = neutral, up to 1.6 during monsoon).
    City-aware: winter haze uplift only applies to cities with winter_haze=True.
    """
    if date is None:
        date = datetime.utcnow()
    week = date.isocalendar()[1]  # 1-52

    profile = CITY_SEASONAL_PROFILE.get(city, {"winter_haze": False, "monsoon": True, "pre_heat": False})

    # Monsoon: weeks 24-40 (June-October) -> high risk (all cities)
    if 24 <= week <= 40 and profile.get("monsoon", True):
        peak = 32
        dist = abs(week - peak)
        return round(1.0 + max(0, (8 - dist) / 8) * 0.6, 3)
    # Winter haze: weeks 44-52, 1-6 (Nov-Feb) -> only for Delhi, Kolkata
    elif (week >= 44 or week <= 6) and profile.get("winter_haze", False):
        return round(1.0 + 0.25, 3)
    # Pre-monsoon heat: weeks 18-23 -> only for cities with pre_heat
    elif 18 <= week <= 23 and profile.get("pre_heat", False):
        return round(1.0 + 0.2, 3)
    else:
        return 1.0


# ─────────────────────────────────────────────────────────────────
# ZONE RISK LOOKUP
# Fix A: Local RNG instead of mutating global np.random.seed()
# ─────────────────────────────────────────────────────────────────
def get_zone_risk(pincode: str, city: str) -> float:
    """Returns zone risk score (0-1) for a given pincode."""
    if not pincode:
        return CITY_RISK_DEFAULT.get(city, 0.55)
    if pincode in ZONE_RISK_DB:
        return ZONE_RISK_DB[pincode]
    # Fallback: deterministic noise from pincode hash (no global RNG mutation)
    city_risk = CITY_RISK_DEFAULT.get(city, 0.55)
    h = hashlib.md5(pincode.encode()).hexdigest()
    noise = (int(h[:8], 16) / 0xFFFFFFFF - 0.5) * 0.16  # range [-0.08, +0.08]
    return round(min(0.95, max(0.2, city_risk + noise)), 3)


# ─────────────────────────────────────────────────────────────────
# CORE PRICING ENGINE
# ─────────────────────────────────────────────────────────────────
def calculate_premium(
    tier: str,
    pincode: str,
    city: str,
    claim_history_count: int = 0,
    disruption_streak: int = 0,
    forecast_risk: float = None,
    date: datetime = None,
) -> dict:
    """
    Calculate weekly premium using actuarial dynamic pricing with hyper-local risk factors.

    Formula:
        premium = base x zone_factor x seasonal_factor x claim_loading - streak_discount

    Returns breakdown dict with human-readable explanation for each factor.

    Raises ValueError for unknown tier (Fix C: no silent fallback).
    """
    # Fix C: Raise error on unknown tier instead of silent fallback
    if tier not in TIER_CONFIG:
        raise ValueError(f"Unknown policy tier '{tier}'. Must be one of: {', '.join(TIER_CONFIG.keys())}")

    cfg = TIER_CONFIG[tier]
    base = cfg["base"]

    # 1. Zone risk factor (0.8 to 1.4)
    zone_risk = get_zone_risk(pincode, city)
    zone_factor = round(0.8 + zone_risk * 0.6, 3)
    zone_loading = round((zone_factor - 1.0) * base, 2)

    # 2. Seasonal factor (Fix B: city-aware)
    seasonal_factor = get_seasonal_index(date, city)
    seasonal_loading = round((seasonal_factor - 1.0) * base, 2)

    # 3. Claim history loading (+5% per past claim, capped at +25%)
    claim_history_count = max(0, int(claim_history_count))  # Fix F: sanitize input
    claim_factor = min(1.25, 1.0 + claim_history_count * 0.05)
    claim_loading = round((claim_factor - 1.0) * base, 2)

    # 4. Disruption-free streak discount (-10% per 3 clean weeks, max -20%)
    disruption_streak = max(0, int(disruption_streak))  # Fix F: sanitize input
    streak_discount_pct = min(0.20, (disruption_streak // 3) * 0.10)
    streak_discount = round(streak_discount_pct * base, 2)

    # 5. Forecast risk adjustment (if weather API data available)
    forecast_factor = 1.0
    if forecast_risk is not None:
        forecast_risk = max(0.0, min(1.0, float(forecast_risk)))  # Fix F: clamp
        forecast_factor = round(1.0 + forecast_risk * 0.15, 3)
    forecast_loading = round((forecast_factor - 1.0) * base, 2)

    # Final premium
    raw = base * zone_factor * seasonal_factor * claim_factor * forecast_factor
    raw -= streak_discount
    final_premium = round(max(cfg["base"] * 0.75, min(cfg["base"] * 2.0, raw)), 2)

    # Fix D: Affordability guardrail using city-specific income tables
    city_income = CITY_DAILY_INCOME.get(city, DEFAULT_DAILY_INCOME)
    weekly_income = city_income.get(tier, DEFAULT_DAILY_INCOME.get(tier, 800)) * 7
    max_affordable = round(weekly_income * 0.008, 2)  # 0.8% of weekly income
    affordability_capped = False
    if final_premium > max_affordable and tier == "Basic Shield":
        final_premium = max_affordable
        affordability_capped = True

    return {
        "tier": tier,
        "base_premium": base,
        "weekly_premium": final_premium,
        "max_daily_payout": cfg["max_daily"],
        "max_weekly_payout": cfg["max_weekly"],
        "breakdown": {
            "zone_risk_score": zone_risk,
            "zone_factor": zone_factor,
            "zone_loading_inr": zone_loading,
            "seasonal_factor": seasonal_factor,
            "seasonal_loading_inr": seasonal_loading,
            "claim_history_count": claim_history_count,
            "claim_loading_inr": claim_loading,
            "streak_weeks": disruption_streak,
            "streak_discount_inr": -streak_discount,
            "forecast_factor": forecast_factor,
            "forecast_loading_inr": forecast_loading,
            "affordability_capped": affordability_capped,
        },
        # Fix E: Complete explanation with ALL factors
        "explanation": _build_explanation(
            zone_risk, seasonal_factor, claim_history_count,
            disruption_streak, city, tier, forecast_factor,
            affordability_capped, final_premium,
        ),
    }


# Fix E: Explanation now covers forecast, affordability cap, and final premium
def _build_explanation(zone_risk, seasonal_factor, claims, streak, city, tier,
                       forecast_factor=1.0, affordability_capped=False, final_premium=0):
    """Human-readable premium explanation covering all pricing factors."""
    reasons = []
    if zone_risk > 0.75:
        reasons.append(f"Zone: {city} is high-risk for weather disruptions (+loading)")
    elif zone_risk < 0.45:
        reasons.append(f"Zone: {city} has low historical disruption risk (-loading)")
    else:
        reasons.append(f"Zone: {city} has moderate risk profile")

    if seasonal_factor > 1.3:
        reasons.append("Season: Peak monsoon - significantly elevated risk")
    elif seasonal_factor > 1.1:
        reasons.append("Season: Winter haze / pre-monsoon heat - moderate uplift")
    else:
        reasons.append("Season: Low-risk period - no seasonal adjustment")

    if claims > 0:
        reasons.append(f"History: {claims} past claim(s) - risk loading applied (+{min(25, claims*5)}%)")
    else:
        reasons.append("History: No claim history - clean record")

    if streak >= 3:
        discount_pct = min(20, (streak // 3) * 10)
        reasons.append(f"Loyalty: {streak}-week disruption-free streak - {discount_pct}% discount applied!")

    if forecast_factor > 1.0:
        reasons.append(f"Forecast: Weather risk detected - {round((forecast_factor-1)*100)}% uplift")

    if affordability_capped:
        reasons.append(f"Affordability: Premium capped at 0.8% of estimated weekly income")

    return reasons


# ─────────────────────────────────────────────────────────────────
# INCOME-REPLACEMENT PAYOUT ENGINE
# ─────────────────────────────────────────────────────────────────

CITY_DAILY_INCOME = {
    "Mumbai":    {"Basic Shield": 900,  "Standard Guard": 1100, "Pro Armor": 1400},
    "Delhi":     {"Basic Shield": 850,  "Standard Guard": 1050, "Pro Armor": 1350},
    "Bangalore": {"Basic Shield": 950,  "Standard Guard": 1150, "Pro Armor": 1450},
    "Hyderabad": {"Basic Shield": 800,  "Standard Guard": 1000, "Pro Armor": 1300},
    "Chennai":   {"Basic Shield": 800,  "Standard Guard": 1000, "Pro Armor": 1300},
    "Pune":      {"Basic Shield": 800,  "Standard Guard": 1000, "Pro Armor": 1250},
    "Kolkata":   {"Basic Shield": 750,  "Standard Guard": 950,  "Pro Armor": 1200},
}
DEFAULT_DAILY_INCOME = {"Basic Shield": 800, "Standard Guard": 1000, "Pro Armor": 1300}

TRIGGER_REPLACEMENT_RATES = {
    "Heavy Rainfall":          {"Basic Shield": 0.35, "Standard Guard": 0.55, "Pro Armor": 0.70},
    "Extreme Rain / Flooding": {"Basic Shield": 0.55, "Standard Guard": 0.72, "Pro Armor": 0.90},
    "Severe Heatwave":         {"Basic Shield": 0.35, "Standard Guard": 0.55, "Pro Armor": 0.70},
    "Hazardous AQI":           {"Basic Shield": 0.35, "Standard Guard": 0.55, "Pro Armor": 0.70},
    "Platform Outage":         {"Basic Shield": 0.25, "Standard Guard": 0.45, "Pro Armor": 0.65},
    "Civil Disruption":        {"Basic Shield": 0.35, "Standard Guard": 0.55, "Pro Armor": 0.75},
}


def get_payout_amount(trigger_type: str, tier: str, city: str = None) -> float:
    """
    Returns income-proportional payout for a single trigger event + tier + city.
    Payout = estimated_daily_income x replacement_rate, capped at tier's max_daily.

    NOTE: Weekly aggregate caps must be enforced at claim-creation layer
    (see routers/triggers.py::_auto_generate_claims).
    """
    city_income = CITY_DAILY_INCOME.get(city, DEFAULT_DAILY_INCOME) if city else DEFAULT_DAILY_INCOME
    daily_income = city_income.get(tier, DEFAULT_DAILY_INCOME.get(tier, 1000))

    rates = TRIGGER_REPLACEMENT_RATES.get(trigger_type, {})
    rate = rates.get(tier, 0.0)
    if rate == 0.0:
        return 0.0

    payout = round(daily_income * rate / 10) * 10  # Round to nearest 10

    max_daily = TIER_CONFIG.get(tier, {}).get("max_daily", 300)
    return float(min(payout, max_daily))
