"""
Zynvaro — LLM-Powered Risk Profile Explainer (Option C)
Generates personalized worker risk narratives using Anthropic Claude.
Falls back to a rich rule-based template when no API key is set.

Flow:
  1. Pull actuarial factors (zone_risk, seasonal_index, premium breakdown)
  2. Try Anthropic claude-3-5-haiku → 3-sentence personal risk narrative
  3. Fallback: deterministic rule-based narrative (still informative)
  4. Return structured risk profile (narrative + numbers + key risks + tips)
"""

import os
from datetime import datetime
from ml.premium_engine import calculate_premium, get_zone_risk, get_seasonal_index, CITY_DAILY_INCOME

ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")

# ─────────────────────────────────────────────────────────────────
# KEY RISK CATALOGUE (city + season → top risks)
# ─────────────────────────────────────────────────────────────────
CITY_RISK_PROFILE = {
    "Mumbai":    {"primary": "Heavy Rainfall / Flooding", "secondary": "Coastal storms", "peak_months": "June–September"},
    "Delhi":     {"primary": "Hazardous AQI",             "secondary": "Heatwave (May–June)", "peak_months": "Nov–Feb (AQI), May–Jun (heat)"},
    "Bangalore": {"primary": "Civil Disruption",          "secondary": "Unseasonable rain",  "peak_months": "Oct–Nov"},
    "Hyderabad": {"primary": "Heavy Rainfall",            "secondary": "Flash flooding",      "peak_months": "Jul–Sep"},
    "Chennai":   {"primary": "Cyclones / Heavy Rain",     "secondary": "Coastal flooding",    "peak_months": "Oct–Dec"},
    "Pune":      {"primary": "Heavy Rainfall",            "secondary": "Urban flooding",      "peak_months": "Jul–Aug"},
    "Kolkata":   {"primary": "Cyclones / Flooding",       "secondary": "Extreme heat",        "peak_months": "May–Jun, Oct–Nov"},
}

SEASONAL_CONTEXT = {
    "high_monsoon":   "🌧️ You're entering peak monsoon — heavy rain claims spike 3× in your region.",
    "early_monsoon":  "🌩️ Pre-monsoon rains are building — weather triggers may activate soon.",
    "winter_haze":    "🌫️ Winter air quality season — AQI triggers are most active right now.",
    "pre_monsoon":    "☀️ Pre-monsoon heatwaves are your main risk this season.",
    "low_season":     "☀️ You're in a relatively calm weather window — good time to build streak.",
}

TIER_TIPS = {
    "Basic Shield":   "💡 Tip: Upgrading to Standard Guard triples your daily payout for just ₹20 more/week.",
    "Standard Guard": "💡 Tip: 3 more disruption-free weeks earns you a 10% streak discount.",
    "Pro Armor":      "💡 Tip: You have maximum protection — your payout covers 70%+ of daily income.",
}


# ─────────────────────────────────────────────────────────────────
# HELPER: SEASONAL CONTEXT
# ─────────────────────────────────────────────────────────────────
def _get_seasonal_context(seasonal_factor: float) -> str:
    week = datetime.utcnow().isocalendar()[1]
    if 24 <= week <= 40:
        return "high_monsoon" if seasonal_factor > 1.4 else "early_monsoon"
    elif week >= 44 or week <= 6:
        return "winter_haze"
    elif 18 <= week <= 23:
        return "pre_monsoon"
    return "low_season"


# ─────────────────────────────────────────────────────────────────
# RULE-BASED NARRATIVE FALLBACK
# ─────────────────────────────────────────────────────────────────
def _template_narrative(ctx: dict) -> str:
    city      = ctx["city"]
    tier      = ctx["tier"]
    platform  = ctx["platform"]
    shift     = ctx["shift"]
    zone_risk = ctx["zone_risk"]
    seasonal  = ctx["seasonal_context_key"]
    premium   = ctx["weekly_premium"]
    payout    = ctx["max_daily_payout"]
    history   = ctx["claim_history"]
    streak    = ctx["disruption_streak"]
    daily_inc = ctx["estimated_daily_income"]

    # Sentence 1: zone + platform risk
    risk_profile = CITY_RISK_PROFILE.get(city, {"primary": "weather disruptions", "secondary": "civil events", "peak_months": "monsoon"})
    zone_label = "high-risk" if zone_risk > 0.72 else ("moderate-risk" if zone_risk > 0.50 else "lower-risk")
    s1 = (
        f"As a {platform} delivery partner in {city}'s {zone_label} zone "
        f"(risk score {zone_risk:.2f}), your primary exposure is {risk_profile['primary']} "
        f"— especially during {risk_profile['peak_months']}."
    )

    # Sentence 2: shift + income protection
    shift_note = "late-night riding adds additional safety risk" if "Evening" in shift or "2AM" in shift else "your daytime shift faces peak traffic and weather exposure"
    s2 = (
        f"During {shift}, {shift_note}; "
        f"your {tier} plan pays up to ₹{int(payout):,}/day "
        f"(≈{int(payout / daily_inc * 100)}% income replacement) "
        f"the moment a parametric trigger is confirmed — no claim forms, no waiting."
    )

    # Sentence 3: personalised loyalty/history note
    if streak >= 6:
        s3 = f"Your {streak}-week disruption-free streak earns a loyalty discount and signals that you're a reliable, low-risk rider — keep it going!"
    elif streak >= 3:
        s3 = f"You're building a {streak}-week clean streak — 3 more weeks adds a 10% loyalty discount to your ₹{premium}/week premium."
    elif history == 0:
        s3 = f"With a clean claim record, your ₹{premium}/week premium is at the lowest loading — stay disruption-free to unlock streak rewards."
    else:
        s3 = f"Your ₹{premium}/week premium includes a small loading for {history} past claim(s); consistent clean weeks will bring this down over time."

    return f"{s1} {s2} {s3}"


# ─────────────────────────────────────────────────────────────────
# LLM NARRATIVE (Anthropic Claude)
# ─────────────────────────────────────────────────────────────────
def _llm_narrative(ctx: dict) -> str:
    """
    Generate a personalized 3-sentence risk narrative using Anthropic Claude.
    Requires ANTHROPIC_API_KEY in environment. Falls back to template on error.
    """
    try:
        import anthropic

        prompt = f"""You are a friendly insurance advisor for Zynvaro, an AI-powered parametric income insurance app for gig delivery workers in India.

IMPORTANT: Do not guarantee payouts or make specific coverage promises. Use phrases like 'may be eligible' and 'subject to trigger verification'. This is parametric insurance — payouts depend on verified trigger conditions, not traditional claims.

Write exactly 3 clear, friendly sentences explaining the personal insurance risk profile for this worker. Be specific, use the numbers provided, and make it feel personal. Do not use generic language.

Worker profile:
- Name context: gig delivery partner
- City: {ctx['city']} (zone risk score: {ctx['zone_risk']:.2f} out of 1.0)
- Platform: {ctx['platform']}
- Shift: {ctx['shift']}
- Tier: {ctx['tier']}
- Weekly premium: ₹{ctx['weekly_premium']}
- Max daily payout: ₹{ctx['max_daily_payout']}
- Estimated daily income: ₹{ctx['estimated_daily_income']}
- Claim history: {ctx['claim_history']} past claims
- Disruption-free streak: {ctx['disruption_streak']} weeks
- Primary city risk: {ctx['primary_risk']}
- Peak risk period: {ctx['peak_months']}
- Current season context: {ctx['seasonal_context_key']}
- Premium factors: {', '.join(ctx['factors'])}

Write 3 sentences. Be warm but precise. Use rupee amounts and percentages. Mention the zero-paperwork auto-payout feature."""

        client = anthropic.Anthropic(api_key=ANTHROPIC_API_KEY, timeout=10.0)
        message = client.messages.create(
            model="claude-3-5-haiku-20241022",
            max_tokens=300,
            messages=[{"role": "user", "content": prompt}],
        )
        return message.content[0].text.strip()

    except ImportError:
        print("[RiskExplainer] anthropic package not installed — using template fallback")
        return _template_narrative(ctx)
    except Exception as e:
        print(f"[RiskExplainer] LLM API error: {type(e).__name__}: {e} — using template fallback")
        return _template_narrative(ctx)


# ─────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────
def generate_risk_profile(
    worker_city: str,
    worker_pincode: str,
    worker_platform: str,
    worker_shift: str,
    tier: str,
    claim_history: int = 0,
    disruption_streak: int = 0,
) -> dict:
    """
    Generate a complete personalized risk profile for a worker.
    Called after registration or on demand from /policies/risk-profile.

    Returns:
        narrative:           3-sentence personalized risk explanation
        risk_score:          0-100 zone risk score
        weekly_premium:      calculated premium (INR)
        max_daily_payout:    tier daily cap (INR)
        income_replacement:  payout as % of estimated daily income
        key_risks:           top 2 disruption risks for this city
        seasonal_alert:      current season context message
        tier_tip:            upgrade/streak hint
        factors:             SHAP-style premium breakdown list
        llm_powered:         True if narrative from Claude, False if template
    """
    pricing = calculate_premium(tier, worker_pincode, worker_city, claim_history, disruption_streak)
    zone_risk = get_zone_risk(worker_pincode, worker_city)
    seasonal = get_seasonal_index()
    seasonal_key = _get_seasonal_context(seasonal)
    city_income = CITY_DAILY_INCOME.get(worker_city, {})
    daily_income = city_income.get(tier, 1000)
    risk_profile = CITY_RISK_PROFILE.get(worker_city, {
        "primary": "weather disruptions", "secondary": "civil events", "peak_months": "monsoon"
    })

    income_replacement_pct = round(pricing["max_daily_payout"] / daily_income * 100, 1)

    ctx = {
        "city":                  worker_city,
        "platform":              worker_platform,
        "shift":                 worker_shift,
        "tier":                  tier,
        "zone_risk":             zone_risk,
        "seasonal_context_key":  seasonal_key,
        "weekly_premium":        pricing["weekly_premium"],
        "max_daily_payout":      pricing["max_daily_payout"],
        "estimated_daily_income": daily_income,
        "claim_history":         claim_history,
        "disruption_streak":     disruption_streak,
        "primary_risk":          risk_profile["primary"],
        "peak_months":           risk_profile["peak_months"],
        "factors":               pricing["explanation"],
    }

    # Try LLM first if key present, else use template
    if ANTHROPIC_API_KEY:
        narrative = _llm_narrative(ctx)
        # llm_powered is True only if the LLM narrative differs from template fallback
        template_fallback = _template_narrative(ctx)
        llm_powered = (narrative != template_fallback)
    else:
        narrative = _template_narrative(ctx)
        llm_powered = False

    return {
        "narrative":            narrative,
        "llm_powered":          llm_powered,
        "risk_score":           round(zone_risk * 100, 1),
        "weekly_premium":       pricing["weekly_premium"],
        "max_daily_payout":     pricing["max_daily_payout"],
        "income_replacement":   income_replacement_pct,
        # key_risks as a list so the frontend can call .map() on it directly
        "key_risks": [
            risk_profile["primary"],
            risk_profile.get("secondary", "Platform outages"),
        ],
        "seasonal_alert":       SEASONAL_CONTEXT.get(seasonal_key, ""),
        "tier_tip":             TIER_TIPS.get(tier, ""),
        "factors":              pricing["explanation"],
        "premium_breakdown":    pricing["breakdown"],
    }
