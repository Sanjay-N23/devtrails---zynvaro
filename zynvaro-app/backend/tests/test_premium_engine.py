"""
Unit tests for ml/premium_engine.py

Run with:
    cd zynvaro-app/backend
    pytest tests/test_premium_engine.py -v
"""

import sys
import os
import datetime

# Ensure the backend package root is on the path so ml.premium_engine can be imported
sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from ml.premium_engine import (
    get_seasonal_index,
    get_zone_risk,
    calculate_premium,
    get_payout_amount,
    CITY_RISK_DEFAULT,
    TIER_CONFIG,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _date_for_iso_week(iso_week: int, year: int = 2025) -> datetime.datetime:
    """Return a datetime whose ISO calendar week equals *iso_week*."""
    return datetime.datetime.strptime(f"{year}-W{iso_week:02d}-4", "%G-W%V-%u")


# Stable off-season date (ISO week 11 -> seasonal factor 1.0) used across many
# premium tests so seasonal variance does not interfere with other assertions.
_OFF_SEASON = datetime.datetime(2025, 3, 15)  # ISO week 11


# ===========================================================================
# A. get_seasonal_index()
# ===========================================================================

# --- get_seasonal_index ---

def test_seasonal_index_peak_monsoon_week32_returns_1_6():
    d = _date_for_iso_week(32)
    assert get_seasonal_index(d) == 1.6


def test_seasonal_index_monsoon_boundary_week24_returns_1_0():
    # Week 24 is the opening boundary: dist from peak 32 is 8, so (8-8)/8 = 0 -> factor 1.0
    d = _date_for_iso_week(24)
    assert get_seasonal_index(d) == 1.0


def test_seasonal_index_monsoon_boundary_week40_returns_1_0():
    # Week 40 is the closing boundary: dist = 8, same calculation -> 1.0
    d = _date_for_iso_week(40)
    assert get_seasonal_index(d) == 1.0


def test_seasonal_index_monsoon_interior_week28_returns_above_1_0():
    # Week 28: dist=4, (8-4)/8 * 0.6 = 0.3 -> 1.3 — confirms interior weeks are > 1.0
    d = _date_for_iso_week(28)
    assert get_seasonal_index(d) > 1.0


def test_seasonal_index_winter_haze_week1_returns_1_25():
    d = _date_for_iso_week(1)
    assert get_seasonal_index(d) == 1.25


def test_seasonal_index_winter_haze_week52_returns_1_25():
    d = _date_for_iso_week(52)
    assert get_seasonal_index(d) == 1.25


def test_seasonal_index_winter_haze_week44_returns_1_25():
    # First week of the winter-haze band (>= 44)
    d = _date_for_iso_week(44)
    assert get_seasonal_index(d) == 1.25


def test_seasonal_index_winter_haze_week6_returns_1_25():
    # Last week of the early-year haze band (<= 6)
    d = _date_for_iso_week(6)
    assert get_seasonal_index(d) == 1.25


def test_seasonal_index_pre_monsoon_week20_returns_1_2():
    d = _date_for_iso_week(20)
    assert get_seasonal_index(d) == 1.2


def test_seasonal_index_off_season_week12_returns_1_0():
    d = _date_for_iso_week(12)
    assert get_seasonal_index(d) == 1.0


def test_seasonal_index_none_uses_current_datetime_returns_float_in_range():
    result = get_seasonal_index(None)
    assert isinstance(result, float)
    assert 1.0 <= result <= 1.6


# ===========================================================================
# B. get_zone_risk()
# ===========================================================================

# --- get_zone_risk ---

def test_get_zone_risk_known_pincode_400051_returns_0_88():
    assert get_zone_risk("400051", "Mumbai") == 0.88


def test_get_zone_risk_known_pincode_560047_returns_0_62():
    assert get_zone_risk("560047", "Bangalore") == 0.62


def test_get_zone_risk_none_pincode_mumbai_returns_city_default():
    assert get_zone_risk(None, "Mumbai") == CITY_RISK_DEFAULT["Mumbai"]
    assert get_zone_risk(None, "Mumbai") == 0.82


def test_get_zone_risk_none_pincode_unknown_city_returns_0_55_default():
    assert get_zone_risk(None, "UnknownCity") == 0.55


def test_get_zone_risk_unknown_pincode_returns_value_in_valid_range():
    result = get_zone_risk("999999", "Mumbai")
    assert 0.2 <= result <= 0.95


def test_get_zone_risk_unknown_pincode_is_deterministic():
    # Calling twice with the same unknown numeric pincode must yield identical values
    result_a = get_zone_risk("999999", "Mumbai")
    result_b = get_zone_risk("999999", "Mumbai")
    assert result_a == result_b


def test_get_zone_risk_non_numeric_pincode_uses_seed_42_is_deterministic():
    # Non-numeric pincode falls through to seed 42 — result must be stable
    result_a = get_zone_risk("ABCDEF", "Mumbai")
    result_b = get_zone_risk("ABCDEF", "Mumbai")
    assert result_a == result_b
    assert result_a == 0.8  # Confirmed value with seed 42 and Mumbai city_risk 0.82


def test_get_zone_risk_known_pincode_110001_returns_0_75():
    assert get_zone_risk("110001", "Delhi") == 0.75


def test_get_zone_risk_known_pincode_600041_returns_0_80():
    assert get_zone_risk("600041", "Chennai") == 0.80


# ===========================================================================
# C. calculate_premium()
# ===========================================================================

# --- calculate_premium ---

# -- Return structure --

def test_calculate_premium_returns_all_required_top_level_keys():
    result = calculate_premium("Basic Shield", "400001", "Mumbai", date=_OFF_SEASON)
    required = {"tier", "base_premium", "weekly_premium", "max_daily_payout", "max_weekly_payout", "breakdown", "explanation"}
    assert required.issubset(result.keys())


def test_calculate_premium_breakdown_contains_all_required_keys():
    result = calculate_premium("Standard Guard", "110001", "Delhi", date=_OFF_SEASON)
    required = {
        "zone_risk_score", "zone_factor", "zone_loading_inr",
        "seasonal_factor", "seasonal_loading_inr",
        "claim_history_count", "claim_loading_inr",
        "streak_weeks", "streak_discount_inr",
        "forecast_factor", "forecast_loading_inr",
    }
    assert required.issubset(result["breakdown"].keys())


def test_calculate_premium_explanation_is_list_of_strings():
    result = calculate_premium("Pro Armor", "560001", "Bangalore", date=_OFF_SEASON)
    assert isinstance(result["explanation"], list)
    assert len(result["explanation"]) >= 1
    for item in result["explanation"]:
        assert isinstance(item, str)


# -- Base premium per tier --

def test_calculate_premium_basic_shield_base_is_29():
    result = calculate_premium("Basic Shield", "400001", "Mumbai", date=_OFF_SEASON)
    assert result["base_premium"] == 29.0


def test_calculate_premium_standard_guard_base_is_49():
    result = calculate_premium("Standard Guard", "110001", "Delhi", date=_OFF_SEASON)
    assert result["base_premium"] == 49.0


def test_calculate_premium_pro_armor_base_is_89():
    result = calculate_premium("Pro Armor", "560001", "Bangalore", date=_OFF_SEASON)
    assert result["base_premium"] == 89.0


# -- Tier payout config passthrough --

def test_calculate_premium_basic_shield_max_daily_payout_is_300():
    result = calculate_premium("Basic Shield", "400001", "Mumbai", date=_OFF_SEASON)
    assert result["max_daily_payout"] == 300


def test_calculate_premium_basic_shield_max_weekly_payout_is_600():
    result = calculate_premium("Basic Shield", "400001", "Mumbai", date=_OFF_SEASON)
    assert result["max_weekly_payout"] == 600


def test_calculate_premium_standard_guard_max_daily_payout_is_600():
    result = calculate_premium("Standard Guard", "110001", "Delhi", date=_OFF_SEASON)
    assert result["max_daily_payout"] == 600


def test_calculate_premium_pro_armor_max_daily_payout_is_1000():
    result = calculate_premium("Pro Armor", "560001", "Bangalore", date=_OFF_SEASON)
    assert result["max_daily_payout"] == 1000


# -- Premium caps --

def test_calculate_premium_weekly_never_exceeds_2x_base_standard_guard():
    # Use maximum risk conditions: high-risk pincode, peak monsoon, max claims
    peak_date = _date_for_iso_week(32)
    result = calculate_premium(
        "Standard Guard", "400051", "Mumbai",
        claim_history_count=10, forecast_risk=1.0, date=peak_date
    )
    assert result["weekly_premium"] <= TIER_CONFIG["Standard Guard"]["base"] * 2.0


def test_calculate_premium_weekly_never_exceeds_2x_base_pro_armor():
    peak_date = _date_for_iso_week(32)
    result = calculate_premium(
        "Pro Armor", "400051", "Mumbai",
        claim_history_count=10, forecast_risk=1.0, date=peak_date
    )
    assert result["weekly_premium"] <= TIER_CONFIG["Pro Armor"]["base"] * 2.0


def test_calculate_premium_weekly_never_below_0_75x_base_standard_guard():
    # Minimum-risk scenario: safe pincode, off-season, no claims, max streak discount
    result = calculate_premium(
        "Standard Guard", "500081", "Hyderabad",
        disruption_streak=9, date=_OFF_SEASON
    )
    assert result["weekly_premium"] >= TIER_CONFIG["Standard Guard"]["base"] * 0.75


def test_calculate_premium_weekly_never_below_0_75x_base_pro_armor():
    result = calculate_premium(
        "Pro Armor", "500081", "Hyderabad",
        disruption_streak=9, date=_OFF_SEASON
    )
    assert result["weekly_premium"] >= TIER_CONFIG["Pro Armor"]["base"] * 0.75


# -- Basic Shield affordability cap --

def test_calculate_premium_basic_shield_affordability_cap_is_36():
    # Peak conditions push raw premium above 2x base; affordability cap (4500*0.008=36) applies
    peak_date = _date_for_iso_week(32)
    result = calculate_premium(
        "Basic Shield", "400051", "Mumbai",
        claim_history_count=5, date=peak_date
    )
    assert result["weekly_premium"] == 36.0


def test_calculate_premium_affordability_cap_not_applied_to_standard_guard():
    # Standard Guard must NOT be capped at 36 even under high-risk conditions
    peak_date = _date_for_iso_week(32)
    result = calculate_premium(
        "Standard Guard", "400051", "Mumbai",
        claim_history_count=5, date=peak_date
    )
    assert result["weekly_premium"] > 36.0


# -- Claim history loading --

def test_calculate_premium_claim_history_5_applies_25_pct_loading():
    result = calculate_premium("Standard Guard", "560001", "Bangalore",
                               claim_history_count=5, date=_OFF_SEASON)
    # 5 * 5% = 25%, capped at 25%; loading = 0.25 * 49 = 12.25
    assert result["breakdown"]["claim_loading_inr"] == 12.25


def test_calculate_premium_claim_history_6_still_caps_at_25_pct_loading():
    result = calculate_premium("Standard Guard", "560001", "Bangalore",
                               claim_history_count=6, date=_OFF_SEASON)
    # 6 * 5% = 30% but cap is 25%; loading must equal 12.25, not 14.7
    assert result["breakdown"]["claim_loading_inr"] == 12.25


def test_calculate_premium_claim_history_5_and_6_produce_same_loading():
    r5 = calculate_premium("Standard Guard", "560001", "Bangalore",
                           claim_history_count=5, date=_OFF_SEASON)
    r6 = calculate_premium("Standard Guard", "560001", "Bangalore",
                           claim_history_count=6, date=_OFF_SEASON)
    assert r5["breakdown"]["claim_loading_inr"] == r6["breakdown"]["claim_loading_inr"]


def test_calculate_premium_no_claims_produces_zero_claim_loading():
    result = calculate_premium("Standard Guard", "560001", "Bangalore",
                               claim_history_count=0, date=_OFF_SEASON)
    assert result["breakdown"]["claim_loading_inr"] == 0.0


# -- Disruption streak discount --

def test_calculate_premium_disruption_streak_3_gives_10_pct_discount():
    result = calculate_premium("Standard Guard", "560001", "Bangalore",
                               disruption_streak=3, date=_OFF_SEASON)
    # 10% of 49 = 4.9; stored as negative in breakdown
    assert result["breakdown"]["streak_discount_inr"] == -4.9


def test_calculate_premium_disruption_streak_6_gives_20_pct_discount():
    result = calculate_premium("Standard Guard", "560001", "Bangalore",
                               disruption_streak=6, date=_OFF_SEASON)
    # 20% of 49 = 9.8
    assert result["breakdown"]["streak_discount_inr"] == -9.8


def test_calculate_premium_disruption_streak_9_caps_at_20_pct_discount():
    result = calculate_premium("Standard Guard", "560001", "Bangalore",
                               disruption_streak=9, date=_OFF_SEASON)
    # Max is 20%; streak=9 -> (9//3)*10% = 30% but capped at 20% -> -9.8
    assert result["breakdown"]["streak_discount_inr"] == -9.8


def test_calculate_premium_disruption_streak_6_and_9_same_discount():
    r6 = calculate_premium("Standard Guard", "560001", "Bangalore",
                           disruption_streak=6, date=_OFF_SEASON)
    r9 = calculate_premium("Standard Guard", "560001", "Bangalore",
                           disruption_streak=9, date=_OFF_SEASON)
    assert r6["breakdown"]["streak_discount_inr"] == r9["breakdown"]["streak_discount_inr"]


def test_calculate_premium_streak_discount_is_negative_value():
    result = calculate_premium("Standard Guard", "560001", "Bangalore",
                               disruption_streak=3, date=_OFF_SEASON)
    assert result["breakdown"]["streak_discount_inr"] < 0


def test_calculate_premium_zero_streak_produces_zero_discount():
    result = calculate_premium("Standard Guard", "560001", "Bangalore",
                               disruption_streak=0, date=_OFF_SEASON)
    assert result["breakdown"]["streak_discount_inr"] == 0.0


# -- Forecast risk loading --

def test_calculate_premium_forecast_risk_0_5_factor_is_1_075():
    result = calculate_premium("Standard Guard", "560001", "Bangalore",
                               forecast_risk=0.5, date=_OFF_SEASON)
    assert result["breakdown"]["forecast_factor"] == 1.075


def test_calculate_premium_forecast_risk_0_5_loading_is_3_67():
    result = calculate_premium("Standard Guard", "560001", "Bangalore",
                               forecast_risk=0.5, date=_OFF_SEASON)
    # (1.075 - 1.0) * 49 = 3.675 -> rounded to 3.67 (Python round)
    assert result["breakdown"]["forecast_loading_inr"] == 3.67


def test_calculate_premium_no_forecast_risk_factor_is_1_0():
    result = calculate_premium("Standard Guard", "560001", "Bangalore",
                               forecast_risk=None, date=_OFF_SEASON)
    assert result["breakdown"]["forecast_factor"] == 1.0


def test_calculate_premium_no_forecast_risk_loading_is_0():
    result = calculate_premium("Standard Guard", "560001", "Bangalore",
                               forecast_risk=None, date=_OFF_SEASON)
    assert result["breakdown"]["forecast_loading_inr"] == 0.0


# -- Unknown tier fallback --

def test_calculate_premium_unknown_tier_falls_back_to_standard_guard_base():
    result = calculate_premium("Nonexistent Tier", "560001", "Bangalore", date=_OFF_SEASON)
    assert result["base_premium"] == 49.0


def test_calculate_premium_unknown_tier_falls_back_to_standard_guard_max_daily():
    result = calculate_premium("Nonexistent Tier", "560001", "Bangalore", date=_OFF_SEASON)
    assert result["max_daily_payout"] == 600


def test_calculate_premium_unknown_tier_falls_back_to_standard_guard_max_weekly():
    result = calculate_premium("Nonexistent Tier", "560001", "Bangalore", date=_OFF_SEASON)
    assert result["max_weekly_payout"] == 1200


# -- tier field echoed back --

def test_calculate_premium_tier_field_echoes_input():
    result = calculate_premium("Pro Armor", "560001", "Bangalore", date=_OFF_SEASON)
    assert result["tier"] == "Pro Armor"


# ===========================================================================
# D. get_payout_amount()
# ===========================================================================

# --- get_payout_amount ---

def test_get_payout_heavy_rainfall_basic_shield_bangalore_capped_at_300():
    # daily_income=950, rate=0.35 -> 332.5 -> round to nearest 10 = 330
    # but max_daily for Basic Shield = 300, so capped -> 300.0
    result = get_payout_amount("Heavy Rainfall", "Basic Shield", "Bangalore")
    assert result == 300.0


def test_get_payout_extreme_rain_pro_armor_mumbai_capped_at_1000():
    # daily_income=1400, rate=0.90 -> 1260 -> capped at max_daily=1000
    result = get_payout_amount("Extreme Rain / Flooding", "Pro Armor", "Mumbai")
    assert result == 1000.0


def test_get_payout_hazardous_aqi_standard_guard_delhi_rounds_to_580():
    # daily_income=1050, rate=0.55 -> 577.5 -> round to nearest 10 = 580
    # max_daily for Standard Guard = 600, so no cap applied -> 580.0
    result = get_payout_amount("Hazardous AQI", "Standard Guard", "Delhi")
    assert result == 580.0


def test_get_payout_platform_outage_basic_shield_bangalore_rounds_to_240():
    # daily_income=950, rate=0.25 -> 237.5 -> round to nearest 10 = 240
    # max_daily for Basic Shield = 300, no cap -> 240.0
    result = get_payout_amount("Platform Outage", "Basic Shield", "Bangalore")
    assert result == 240.0


def test_get_payout_unknown_trigger_returns_0():
    result = get_payout_amount("Space Weather", "Basic Shield", "Mumbai")
    assert result == 0.0


def test_get_payout_city_none_uses_default_daily_income():
    # DEFAULT_DAILY_INCOME["Standard Guard"] = 1000, rate for Heavy Rainfall = 0.55
    # 1000 * 0.55 = 550 -> nearest 10 = 550, cap = 600 -> 550.0
    result = get_payout_amount("Heavy Rainfall", "Standard Guard", None)
    assert result == 550.0


def test_get_payout_returns_float():
    result = get_payout_amount("Heavy Rainfall", "Basic Shield", "Mumbai")
    assert isinstance(result, float)


# -- All 6 trigger types produce non-zero payouts for all 3 tiers --

_ALL_TRIGGERS = [
    "Heavy Rainfall",
    "Extreme Rain / Flooding",
    "Severe Heatwave",
    "Hazardous AQI",
    "Platform Outage",
    "Civil Disruption",
]
_ALL_TIERS = ["Basic Shield", "Standard Guard", "Pro Armor"]


def test_get_payout_all_triggers_basic_shield_nonzero():
    for trigger in _ALL_TRIGGERS:
        result = get_payout_amount(trigger, "Basic Shield", "Mumbai")
        assert result > 0.0, f"Expected non-zero payout for trigger={trigger!r}, tier='Basic Shield'"


def test_get_payout_all_triggers_standard_guard_nonzero():
    for trigger in _ALL_TRIGGERS:
        result = get_payout_amount(trigger, "Standard Guard", "Mumbai")
        assert result > 0.0, f"Expected non-zero payout for trigger={trigger!r}, tier='Standard Guard'"


def test_get_payout_all_triggers_pro_armor_nonzero():
    for trigger in _ALL_TRIGGERS:
        result = get_payout_amount(trigger, "Pro Armor", "Mumbai")
        assert result > 0.0, f"Expected non-zero payout for trigger={trigger!r}, tier='Pro Armor'"


def test_get_payout_pro_armor_higher_than_basic_shield_same_trigger_city():
    # Pro Armor has higher replacement rates, so payout must be >= Basic Shield
    for trigger in _ALL_TRIGGERS:
        basic = get_payout_amount(trigger, "Basic Shield", "Mumbai")
        pro = get_payout_amount(trigger, "Pro Armor", "Mumbai")
        assert pro >= basic, (
            f"Pro Armor payout ({pro}) should be >= Basic Shield ({basic}) "
            f"for trigger={trigger!r}"
        )


# -- Payout respects max_daily cap for each tier --

def test_get_payout_basic_shield_never_exceeds_max_daily_300():
    for trigger in _ALL_TRIGGERS:
        result = get_payout_amount(trigger, "Basic Shield", "Mumbai")
        assert result <= 300.0, f"Basic Shield payout {result} exceeds max_daily=300 for trigger={trigger!r}"


def test_get_payout_standard_guard_never_exceeds_max_daily_600():
    for trigger in _ALL_TRIGGERS:
        result = get_payout_amount(trigger, "Standard Guard", "Mumbai")
        assert result <= 600.0, f"Standard Guard payout {result} exceeds max_daily=600 for trigger={trigger!r}"


def test_get_payout_pro_armor_never_exceeds_max_daily_1000():
    for trigger in _ALL_TRIGGERS:
        result = get_payout_amount(trigger, "Pro Armor", "Mumbai")
        assert result <= 1000.0, f"Pro Armor payout {result} exceeds max_daily=1000 for trigger={trigger!r}"


# -- Civil Disruption and Extreme Rain Pro Armor hit max_daily cap --

def test_get_payout_civil_disruption_pro_armor_mumbai_capped_at_1000():
    # 1400 * 0.75 = 1050 -> nearest 10 = 1050 -> capped at 1000
    result = get_payout_amount("Civil Disruption", "Pro Armor", "Mumbai")
    assert result == 1000.0


def test_get_payout_severe_heatwave_standard_guard_mumbai_capped_at_600():
    # 1100 * 0.55 = 605 -> nearest 10 = 610 -> capped at 600
    result = get_payout_amount("Severe Heatwave", "Standard Guard", "Mumbai")
    assert result == 600.0
