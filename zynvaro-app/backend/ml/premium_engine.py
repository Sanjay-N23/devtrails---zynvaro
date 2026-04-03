"""
Zynvaro — Dynamic Premium Pricing Engine
Actuarial rule-based pricing engine with AI-driven hyper-local risk factors.
Features: zone_risk (pincode-level), season_index, claim_history_loading,
streak_discount, tier_factor, forecast_risk_adjustment.
Designed for weekly premium calculation aligned with gig worker pay cycles.
"""

import numpy as np
import json
from datetime import datetime

# ─────────────────────────────────────────────────────────────────
# ZONE RISK DATABASE (pincode → risk score)
# ─────────────────────────────────────────────────────────────────
ZONE_RISK_DB = {
    # Mumbai — High flood/rain risk
    "400001": 0.85, "400002": 0.83, "400051": 0.88, "400053": 0.82,
    "400070": 0.79, "400072": 0.80, "400063": 0.87, "400078": 0.84,
    # Delhi — High AQI + heat risk
    "110001": 0.75, "110002": 0.74, "110019": 0.72, "110025": 0.70,
    "110085": 0.68, "110092": 0.71,
    # Bangalore — Moderate flood/rain risk
    "560001": 0.55, "560034": 0.58, "560047": 0.62, "560095": 0.60,
    "560100": 0.53, "560076": 0.57,
    # Hyderabad — Moderate risk
    "500001": 0.50, "500016": 0.52, "500072": 0.55, "500081": 0.48,
    # Chennai — High cyclone + flood risk
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

# City → default risk
CITY_RISK_DEFAULT = {
    "Mumbai": 0.82, "Delhi": 0.72, "Bangalore": 0.57,
    "Hyderabad": 0.51, "Chennai": 0.77, "Pune": 0.60,
    "Kolkata": 0.68, "Ahmedabad": 0.55,
}

# ─────────────────────────────────────────────────────────────────
# SEASONAL INDEX (week of year → risk multiplier)
# ─────────────────────────────────────────────────────────────────
def get_seasonal_index(date: datetime = None) -> float:
    """Returns seasonal risk multiplier (1.0 = neutral, up to 1.6 during monsoon)."""
    if date is None:
        date = datetime.utcnow()
    week = date.isocalendar()[1]  # 1–52

    # Monsoon: weeks 24–40 (June–October) → high risk
    if 24 <= week <= 40:
        peak = 32  # peak monsoon ~week 32
        dist = abs(week - peak)
        return round(1.0 + max(0, (8 - dist) / 8) * 0.6, 3)
    # Winter haze: weeks 44–52, 1–6 (Nov–Feb) for Delhi AQI
    elif week >= 44 or week <= 6:
        return round(1.0 + 0.25, 3)
    # Pre-monsoon heat: weeks 18–23
    elif 18 <= week <= 23:
        return round(1.0 + 0.2, 3)
    else:
        return 1.0

# ─────────────────────────────────────────────────────────────────
# ZONE RISK LOOKUP
# ─────────────────────────────────────────────────────────────────
def get_zone_risk(pincode: str, city: str) -> float:
    """Returns zone risk score (0–1) for a given pincode."""
    if not pincode:
        return CITY_RISK_DEFAULT.get(city, 0.55)
    if pincode in ZONE_RISK_DB:
        return ZONE_RISK_DB[pincode]
    # Fallback to city default + random noise for unknown pincodes
    city_risk = CITY_RISK_DEFAULT.get(city, 0.55)
    np.random.seed(int(pincode[:4]) if pincode.isdigit() else 42)
    noise = np.random.uniform(-0.08, 0.08)
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
        premium = base × zone_factor × seasonal_factor × claim_loading × streak_discount

    Returns breakdown dict with human-readable AI explanation for each factor.
    """
    cfg = TIER_CONFIG.get(tier, TIER_CONFIG["Standard Guard"])
    base = cfg["base"]

    # 1. Zone risk factor (0.8 to 1.4)
    zone_risk = get_zone_risk(pincode, city)
    zone_factor = round(0.8 + zone_risk * 0.6, 3)        # e.g. 0.82 risk → factor 1.292
    zone_loading = round((zone_factor - 1.0) * base, 2)

    # 2. Seasonal factor
    seasonal_factor = get_seasonal_index(date)
    seasonal_loading = round((seasonal_factor - 1.0) * base, 2)

    # 3. Claim history loading (+5% per past claim, capped at +25%)
    claim_factor = min(1.25, 1.0 + claim_history_count * 0.05)
    claim_loading = round((claim_factor - 1.0) * base, 2)

    # 4. Disruption-free streak discount (-10% per 3 clean weeks, max -20%)
    streak_discount_pct = min(0.20, (disruption_streak // 3) * 0.10)
    streak_discount = round(streak_discount_pct * base, 2)

    # 5. Forecast risk adjustment (if weather API data available)
    forecast_factor = 1.0
    if forecast_risk is not None:
        forecast_factor = round(1.0 + forecast_risk * 0.15, 3)
    forecast_loading = round((forecast_factor - 1.0) * base, 2)

    # Final premium
    raw = base * zone_factor * seasonal_factor * claim_factor * forecast_factor
    raw -= streak_discount
    final_premium = round(max(cfg["base"] * 0.75, min(cfg["base"] * 2.0, raw)), 2)

    # Affordability guardrail: cap at 0.8% of estimated weekly income
    # Assume min weekly income ₹4500 (₹18000/month) for Q-Commerce gig workers
    max_affordable = round(4500 * 0.008, 2)
    if final_premium > max_affordable and tier == "Basic Shield":
        final_premium = max_affordable

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
        },
        "explanation": _build_explanation(
            zone_risk, seasonal_factor, claim_history_count,
            disruption_streak, city, tier
        ),
    }

def _build_explanation(zone_risk, seasonal_factor, claims, streak, city, tier):
    """Human-readable premium explanation (SHAP-style waterfall)."""
    reasons = []
    if zone_risk > 0.75:
        reasons.append(f"📍 {city} zone is high-risk for weather disruptions (+loading)")
    elif zone_risk < 0.45:
        reasons.append(f"📍 {city} zone has low historical disruption risk (−loading)")
    else:
        reasons.append(f"📍 {city} zone has moderate risk profile")

    if seasonal_factor > 1.3:
        reasons.append("🌧️ Peak monsoon season — significantly elevated risk")
    elif seasonal_factor > 1.1:
        reasons.append("🌫️ Winter haze / pre-monsoon heat — moderate seasonal uplift")
    else:
        reasons.append("☀️ Low-risk season — no seasonal adjustment")

    if claims > 0:
        reasons.append(f"📋 {claims} past claim(s) — small risk loading applied")
    else:
        reasons.append("✅ No claim history — clean record")

    if streak >= 3:
        reasons.append(f"🏆 {streak}-week disruption-free streak — loyalty discount applied!")

    return reasons


# ─────────────────────────────────────────────────────────────────
# INCOME-REPLACEMENT PAYOUT ENGINE
# Payouts are a % of estimated daily gig income, not flat amounts.
# This makes them proportional to actual income loss per the PS.
# ─────────────────────────────────────────────────────────────────

# Estimated daily gig income (INR) by city and tier (tier proxies for hours/experience)
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

# Replacement rate (fraction of estimated daily income) per trigger type and tier
# Higher tiers get higher replacement — more complete income protection
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
    Payout = estimated_daily_income × replacement_rate, capped at tier's max_daily.

    NOTE (H7/H8): This function enforces per-trigger caps only (max_daily per event).
    Weekly aggregate caps (Policy.max_weekly_payout) and daily aggregate caps must be
    enforced at the claim-creation layer (see routers/triggers.py::_auto_generate_claims)
    because this engine has no visibility into prior payouts.
    """
    city_income = CITY_DAILY_INCOME.get(city, DEFAULT_DAILY_INCOME) if city else DEFAULT_DAILY_INCOME
    daily_income = city_income.get(tier, DEFAULT_DAILY_INCOME.get(tier, 1000))

    rates = TRIGGER_REPLACEMENT_RATES.get(trigger_type, {})
    rate = rates.get(tier, 0.0)
    if rate == 0.0:
        return 0.0

    payout = round(daily_income * rate / 10) * 10  # Round to nearest ₹10

    # Enforce tier max daily cap
    max_daily = TIER_CONFIG.get(tier, {}).get("max_daily", 300)
    return float(min(payout, max_daily))
