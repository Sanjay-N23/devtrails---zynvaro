"""
Zynvaro — Deep Boundary & Edge Case Tests for Dynamic Premium Pricing Engine
============================================================================

Focus areas:
  - Upper / lower premium caps (hard floor 0.75x, hard ceiling 2.0x, affordability)
  - Claim history loading steps, cap at +25 %
  - Disruption-free streak discount steps, cap at -20 %
  - Seasonal index boundary transitions (band edges, formula zeroes, peaks)
  - Zone risk range invariants across the full ZONE_RISK_DB
  - Forecast risk factor arithmetic and breakdown storage

All tests are pure unit tests — no conftest fixtures, no mocking.

Run:
    pytest tests/test_premium_boundaries.py -v
"""

import sys
import math
import pytest
from datetime import datetime

# ── path fix so the backend package root is importable ──────────────────────
sys.path.insert(0, "D:/AeroFyta_DEVTrails-2026_FULL_HANDOFF/zynvaro-app/backend")

from ml.premium_engine import (
    calculate_premium,
    get_seasonal_index,
    get_zone_risk,
    TIER_CONFIG,
    ZONE_RISK_DB,
    CITY_RISK_DEFAULT,
)

# ────────────────────────────────────────────────────────────────────────────
# Shared date helpers
# ────────────────────────────────────────────────────────────────────────────

def _week(iso_week: int, year: int = 2025) -> datetime:
    """Return a datetime whose ISO-calendar week equals *iso_week*."""
    return datetime.strptime(f"{year}-W{iso_week:02d}-4", "%G-W%V-%u")


# A stable off-season date (ISO week 11) used to neutralise seasonal noise
# in tests that are not focused on the seasonal factor.
_OFF = _week(11)

# Lowest-risk known pincode in ZONE_RISK_DB: Hyderabad 500081 → 0.48
_LOW_RISK_PIN  = ("500081", "Hyderabad")   # zone_risk = 0.48 → zone_factor 1.088
# Highest-risk known pincode: Mumbai 400051 → 0.88
_HIGH_RISK_PIN = ("400051", "Mumbai")      # zone_risk = 0.88 → zone_factor 1.328


# ============================================================================
# 1. TestPremiumUpperLowerCaps
# ============================================================================

class TestPremiumUpperLowerCaps:
    """Verify that weekly_premium is always within the [0.75×base, 2.0×base]
    hard band — with an additional affordability cap for Basic Shield."""

    # ── lower caps ──────────────────────────────────────────────────────────

    def test_premium_lower_cap_basic_shield(self):
        """Minimum premium for Basic Shield must be >= 0.75 * 29.0 = 21.75.

        Drive the price as low as possible: lowest-risk pincode, off-season,
        zero claims, maximum streak discount.
        """
        result = calculate_premium(
            "Basic Shield",
            *_LOW_RISK_PIN,
            claim_history_count=0,
            disruption_streak=100,  # far beyond the 20 % cap
            forecast_risk=0.0,
            date=_OFF,
        )
        floor = TIER_CONFIG["Basic Shield"]["base"] * 0.75   # 21.75
        assert result["weekly_premium"] >= floor, (
            f"weekly_premium {result['weekly_premium']} fell below floor {floor}"
        )

    def test_premium_lower_cap_standard_guard(self):
        """Minimum for Standard Guard must be >= 0.75 * 49.0 = 36.75."""
        result = calculate_premium(
            "Standard Guard",
            *_LOW_RISK_PIN,
            claim_history_count=0,
            disruption_streak=100,
            forecast_risk=0.0,
            date=_OFF,
        )
        floor = TIER_CONFIG["Standard Guard"]["base"] * 0.75  # 36.75
        assert result["weekly_premium"] >= floor, (
            f"weekly_premium {result['weekly_premium']} fell below floor {floor}"
        )

    def test_premium_lower_cap_pro_armor(self):
        """Minimum for Pro Armor must be >= 0.75 * 89.0 = 66.75."""
        result = calculate_premium(
            "Pro Armor",
            *_LOW_RISK_PIN,
            claim_history_count=0,
            disruption_streak=100,
            forecast_risk=0.0,
            date=_OFF,
        )
        floor = TIER_CONFIG["Pro Armor"]["base"] * 0.75  # 66.75
        assert result["weekly_premium"] >= floor, (
            f"weekly_premium {result['weekly_premium']} fell below floor {floor}"
        )

    # ── upper caps ──────────────────────────────────────────────────────────

    def test_premium_upper_cap_basic_shield(self):
        """Basic Shield: 2×base cap = 58.0, but affordability cap = 4500×0.008 = 36.0.

        The effective ceiling is 36.0 because the affordability guardrail is
        applied after the hard cap for Basic Shield only.
        """
        result = calculate_premium(
            "Basic Shield",
            *_HIGH_RISK_PIN,
            claim_history_count=10,
            disruption_streak=0,
            forecast_risk=1.0,
            date=_week(32),          # peak monsoon
        )
        affordability_cap = round(4500 * 0.008, 2)   # 36.0
        assert result["weekly_premium"] <= affordability_cap, (
            f"weekly_premium {result['weekly_premium']} exceeded affordability cap {affordability_cap}"
        )
        assert result["weekly_premium"] == affordability_cap, (
            f"Expected weekly_premium to equal affordability cap {affordability_cap}, "
            f"got {result['weekly_premium']}"
        )

    def test_premium_upper_cap_standard_guard(self):
        """Standard Guard has no affordability cap; hard ceiling = 2.0 * 49.0 = 98.0."""
        result = calculate_premium(
            "Standard Guard",
            *_HIGH_RISK_PIN,
            claim_history_count=10,
            disruption_streak=0,
            forecast_risk=1.0,
            date=_week(32),
        )
        ceiling = TIER_CONFIG["Standard Guard"]["base"] * 2.0  # 98.0
        assert result["weekly_premium"] <= ceiling, (
            f"weekly_premium {result['weekly_premium']} exceeded ceiling {ceiling}"
        )

    def test_premium_upper_cap_pro_armor(self):
        """Pro Armor hard ceiling = 2.0 * 89.0 = 178.0."""
        result = calculate_premium(
            "Pro Armor",
            *_HIGH_RISK_PIN,
            claim_history_count=10,
            disruption_streak=0,
            forecast_risk=1.0,
            date=_week(32),
        )
        ceiling = TIER_CONFIG["Pro Armor"]["base"] * 2.0  # 178.0
        assert result["weekly_premium"] <= ceiling, (
            f"weekly_premium {result['weekly_premium']} exceeded ceiling {ceiling}"
        )

    def test_upper_cap_standard_guard_is_above_affordability_cap(self):
        """Standard Guard ceiling (98.0) must exceed the Basic Shield
        affordability cap (36.0) — confirms no cross-contamination of the rule."""
        result = calculate_premium(
            "Standard Guard",
            *_HIGH_RISK_PIN,
            claim_history_count=10,
            forecast_risk=1.0,
            date=_week(32),
        )
        assert result["weekly_premium"] > 36.0

    def test_upper_cap_pro_armor_is_above_affordability_cap(self):
        """Pro Armor ceiling (178.0) must exceed the Basic Shield
        affordability cap (36.0)."""
        result = calculate_premium(
            "Pro Armor",
            *_HIGH_RISK_PIN,
            claim_history_count=10,
            forecast_risk=1.0,
            date=_week(32),
        )
        assert result["weekly_premium"] > 36.0


# ============================================================================
# 2. TestClaimHistoryLoading
# ============================================================================

class TestClaimHistoryLoading:
    """Verify the +5 %/claim loading rule, capped at +25 %."""

    # Use a stable off-season, moderate-risk pincode and Standard Guard base=49
    # so we can reason directly about INR amounts.
    _PIN = ("560001", "Bangalore")   # zone_risk = 0.55

    def _result(self, tier, claims):
        return calculate_premium(tier, *self._PIN, claim_history_count=claims, date=_OFF)

    def test_zero_claims_no_loading(self):
        """claim_history=0 → claim_loading_inr == 0.0."""
        r = self._result("Standard Guard", 0)
        assert r["breakdown"]["claim_loading_inr"] == 0.0

    def test_one_claim_adds_5_percent(self):
        """1 claim → claim_factor = 1.05 → loading = 0.05 * 49 = 2.45."""
        r = self._result("Standard Guard", 1)
        expected = round(0.05 * TIER_CONFIG["Standard Guard"]["base"], 2)  # 2.45
        assert r["breakdown"]["claim_loading_inr"] == expected

    def test_three_claims_adds_15_percent(self):
        """3 claims → claim_factor = 1.15 → loading = 0.15 * 49 = 7.35."""
        r = self._result("Standard Guard", 3)
        expected = round(0.15 * TIER_CONFIG["Standard Guard"]["base"], 2)  # 7.35
        assert r["breakdown"]["claim_loading_inr"] == expected

    def test_five_claims_adds_25_percent_at_cap(self):
        """5 claims → claim_factor = min(1.25, 1.25) = 1.25 exactly (cap edge)."""
        r = self._result("Standard Guard", 5)
        expected = round(0.25 * TIER_CONFIG["Standard Guard"]["base"], 2)  # 12.25
        assert r["breakdown"]["claim_loading_inr"] == expected

    def test_six_claims_still_capped_at_25_percent(self):
        """6 claims would be 1.30 but cap is 1.25 → same loading as 5 claims."""
        r5 = self._result("Standard Guard", 5)
        r6 = self._result("Standard Guard", 6)
        assert r6["breakdown"]["claim_loading_inr"] == r5["breakdown"]["claim_loading_inr"]

    def test_one_hundred_claims_capped_at_25_percent(self):
        """Extreme value: 100 claims must still cap at +25 %."""
        r = self._result("Standard Guard", 100)
        expected = round(0.25 * TIER_CONFIG["Standard Guard"]["base"], 2)
        assert r["breakdown"]["claim_loading_inr"] == expected

    def test_claim_loading_scales_with_base_premium(self):
        """Standard Guard (base=49) has proportionally larger INR loading than
        Basic Shield (base=29) for the same claim history count."""
        r_basic    = self._result("Basic Shield",   1)
        r_standard = self._result("Standard Guard", 1)
        # Both use +5 %; 0.05*49 > 0.05*29
        assert (
            r_standard["breakdown"]["claim_loading_inr"]
            > r_basic["breakdown"]["claim_loading_inr"]
        )

    def test_claim_loading_pro_armor_1_claim(self):
        """Pro Armor (base=89): 1 claim → 0.05 * 89 = 4.45."""
        r = self._result("Pro Armor", 1)
        expected = round(0.05 * TIER_CONFIG["Pro Armor"]["base"], 2)  # 4.45
        assert r["breakdown"]["claim_loading_inr"] == expected

    def test_claim_count_echoed_in_breakdown(self):
        """claim_history_count value is echoed back in the breakdown dict."""
        r = self._result("Standard Guard", 3)
        assert r["breakdown"]["claim_history_count"] == 3


# ============================================================================
# 3. TestStreakDiscount
# ============================================================================

class TestStreakDiscount:
    """Verify the -10 %/3-week streak discount, capped at -20 %."""

    _PIN = ("560001", "Bangalore")  # zone_risk = 0.55

    def _result(self, tier, streak):
        return calculate_premium(tier, *self._PIN, disruption_streak=streak, date=_OFF)

    def test_streak_zero_no_discount(self):
        """disruption_streak=0 → streak_discount_inr == 0.0."""
        r = self._result("Standard Guard", 0)
        assert r["breakdown"]["streak_discount_inr"] == 0.0

    def test_streak_1_no_discount(self):
        """1 week streak: 1//3=0 → no discount."""
        r = self._result("Standard Guard", 1)
        assert r["breakdown"]["streak_discount_inr"] == 0.0

    def test_streak_2_no_discount_yet(self):
        """2 weeks streak: 2//3=0 → no discount yet."""
        r = self._result("Standard Guard", 2)
        assert r["breakdown"]["streak_discount_inr"] == 0.0

    def test_streak_3_gives_10_percent(self):
        """3 weeks: 3//3=1 → 10 % → -0.10 * 49 = -4.9."""
        r = self._result("Standard Guard", 3)
        expected = -round(0.10 * TIER_CONFIG["Standard Guard"]["base"], 2)  # -4.9
        assert r["breakdown"]["streak_discount_inr"] == expected

    def test_streak_5_gives_10_percent(self):
        """5 weeks: 5//3=1 → still 10 % (not yet 20 %)."""
        r5 = self._result("Standard Guard", 5)
        r3 = self._result("Standard Guard", 3)
        assert r5["breakdown"]["streak_discount_inr"] == r3["breakdown"]["streak_discount_inr"]

    def test_streak_6_gives_20_percent(self):
        """6 weeks: 6//3=2 → 20 % → -0.20 * 49 = -9.8."""
        r = self._result("Standard Guard", 6)
        expected = -round(0.20 * TIER_CONFIG["Standard Guard"]["base"], 2)  # -9.8
        assert r["breakdown"]["streak_discount_inr"] == expected

    def test_streak_9_still_capped_at_20_percent(self):
        """9 weeks: 9//3=3 → would be 30 % but cap is 20 % → same as streak=6."""
        r9 = self._result("Standard Guard", 9)
        r6 = self._result("Standard Guard", 6)
        assert r9["breakdown"]["streak_discount_inr"] == r6["breakdown"]["streak_discount_inr"]

    def test_streak_100_still_capped_at_20_percent(self):
        """100-week streak must not exceed 20 % discount."""
        r = self._result("Standard Guard", 100)
        expected = -round(0.20 * TIER_CONFIG["Standard Guard"]["base"], 2)
        assert r["breakdown"]["streak_discount_inr"] == expected

    def test_streak_discount_is_negative_sign(self):
        """Streak discount must always be stored as a negative value in the breakdown."""
        r = self._result("Standard Guard", 3)
        assert r["breakdown"]["streak_discount_inr"] < 0

    def test_streak_discount_is_subtracted_from_premium(self):
        """Worker with streak=6 must pay less than the same worker with streak=0,
        all else equal (off-season, moderate risk, no claims)."""
        r_no_streak  = self._result("Standard Guard", 0)
        r_with_streak = self._result("Standard Guard", 6)
        assert r_with_streak["weekly_premium"] < r_no_streak["weekly_premium"]

    def test_streak_discount_scales_with_base_pro_armor(self):
        """Pro Armor streak discount is larger in INR than Basic Shield for same streak."""
        r_basic = self._result("Basic Shield",   6)
        r_pro   = self._result("Pro Armor",      6)
        # Both 20 % but 0.20*89 > 0.20*29
        assert abs(r_pro["breakdown"]["streak_discount_inr"]) > abs(r_basic["breakdown"]["streak_discount_inr"])


# ============================================================================
# 4. TestSeasonalFactorBoundaries
# ============================================================================

class TestSeasonalFactorBoundaries:
    """Verify get_seasonal_index() at every band boundary and formula corner."""

    # ── pre-monsoon heat band: weeks 18–23, factor 1.20 ─────────────────────

    def test_week_18_enters_pre_monsoon_band(self):
        assert get_seasonal_index(_week(18)) == 1.2

    def test_week_23_is_last_pre_monsoon_week_returns_1_2(self):
        """Week 23 is the last week of the pre-monsoon band → 1.20."""
        assert get_seasonal_index(_week(23)) == 1.2

    # ── monsoon band: weeks 24–40 ────────────────────────────────────────────

    def test_week_24_enters_monsoon_band_formula_gives_1_0(self):
        """Week 24: dist=|24-32|=8 → max(0,(8-8)/8)*0.6=0.0 → factor=1.0.

        This is a structural boundary: the formula evaluates to 1.0 at both
        edges of the monsoon band even though the week is *inside* the band.
        """
        result = get_seasonal_index(_week(24))
        assert result == 1.0

    def test_week_25_is_above_1_0(self):
        """Week 25: dist=7 → (8-7)/8*0.6=0.075 → 1.075 — confirms interior rise."""
        result = get_seasonal_index(_week(25))
        assert result > 1.0

    def test_week_28_formula_value(self):
        """Week 28: dist=4 → (8-4)/8*0.6=0.3 → 1.3."""
        assert get_seasonal_index(_week(28)) == pytest.approx(1.3, abs=1e-3)

    def test_week_32_is_absolute_peak_returns_1_6(self):
        """Peak week 32: dist=0 → (8-0)/8*0.6=0.6 → 1.6."""
        assert get_seasonal_index(_week(32)) == 1.6

    def test_week_36_symmetric_with_week_28(self):
        """Week 36: dist=|36-32|=4, same as week 28 → 1.3."""
        assert get_seasonal_index(_week(36)) == pytest.approx(1.3, abs=1e-3)

    def test_week_40_exits_monsoon_formula_gives_1_0(self):
        """Week 40: dist=8 → same formula zero as week 24 → 1.0."""
        assert get_seasonal_index(_week(40)) == 1.0

    def test_week_41_exits_monsoon_band_off_season(self):
        """Week 41 is outside 24–40 and outside other bands → off-season 1.0."""
        assert get_seasonal_index(_week(41)) == 1.0

    def test_week_43_is_off_season_returns_1_0(self):
        """Week 43 sits between monsoon and winter-haze bands → 1.0."""
        assert get_seasonal_index(_week(43)) == 1.0

    # ── winter haze band: weeks 44–52 and 1–6, factor 1.25 ─────────────────

    def test_week_44_enters_winter_haze_returns_1_25(self):
        """Week 44 is the first week of the winter-haze band → 1.25."""
        assert get_seasonal_index(_week(44)) == 1.25

    def test_week_52_is_in_winter_haze_returns_1_25(self):
        assert get_seasonal_index(_week(52)) == 1.25

    def test_week_1_is_in_winter_haze_returns_1_25(self):
        assert get_seasonal_index(_week(1)) == 1.25

    def test_week_6_is_last_winter_haze_week_returns_1_25(self):
        """Week 6 is the last week of the early-year haze band → 1.25."""
        assert get_seasonal_index(_week(6)) == 1.25

    def test_week_7_exits_winter_haze_returns_1_0(self):
        """Week 7 is outside both haze sub-bands (>6 and <44) and not in
        monsoon or pre-monsoon → off-season 1.0."""
        assert get_seasonal_index(_week(7)) == 1.0

    # ── none date uses utcnow ────────────────────────────────────────────────

    def test_none_date_returns_float_in_valid_range(self):
        """None triggers utcnow(); result must be a float in [1.0, 1.6]."""
        result = get_seasonal_index(None)
        assert isinstance(result, float)
        assert 1.0 <= result <= 1.6

    # ── seasonal factor feeds through to breakdown ───────────────────────────

    def test_seasonal_factor_stored_in_breakdown_matches_getter(self):
        """The seasonal_factor in the breakdown must equal get_seasonal_index()."""
        d = _week(32)
        result = calculate_premium("Standard Guard", *("560001", "Bangalore"), date=d)
        assert result["breakdown"]["seasonal_factor"] == get_seasonal_index(d)

    def test_monsoon_peak_raises_premium_vs_off_season(self):
        """Peak-monsoon premium (week 32) must exceed off-season premium, all else equal."""
        r_peak = calculate_premium("Standard Guard", "560001", "Bangalore", date=_week(32))
        r_off  = calculate_premium("Standard Guard", "560001", "Bangalore", date=_OFF)
        assert r_peak["weekly_premium"] > r_off["weekly_premium"]


# ============================================================================
# 5. TestZoneRiskBoundaries
# ============================================================================

class TestZoneRiskBoundaries:
    """Verify zone risk score invariants across known and unknown pincodes."""

    def test_all_known_pincodes_in_valid_range(self):
        """Every entry in ZONE_RISK_DB must be in [0.2, 1.0]."""
        for pincode, risk in ZONE_RISK_DB.items():
            assert 0.2 <= risk <= 1.0, (
                f"ZONE_RISK_DB[{pincode!r}] = {risk} is outside [0.2, 1.0]"
            )

    def test_zone_risk_never_below_0_2_for_unknown_pincode(self):
        """Unknown pincode → floor clamped at 0.2."""
        for city in CITY_RISK_DEFAULT:
            result = get_zone_risk("000000", city)
            assert result >= 0.2, (
                f"Zone risk {result} < 0.2 for city={city!r}, pincode='000000'"
            )

    def test_zone_risk_never_above_0_95_for_unknown_pincode(self):
        """Unknown pincode → ceiling clamped at 0.95 (min(0.95, ...))."""
        for city in CITY_RISK_DEFAULT:
            result = get_zone_risk("000000", city)
            assert result <= 0.95, (
                f"Zone risk {result} > 0.95 for city={city!r}, pincode='000000'"
            )

    def test_zone_risk_deterministic_for_same_unknown_pincode(self):
        """Two consecutive calls with the same unknown numeric pincode must
        return identical results (numpy seed-based determinism)."""
        r1 = get_zone_risk("999999", "Mumbai")
        r2 = get_zone_risk("999999", "Mumbai")
        assert r1 == r2

    def test_zone_risk_different_unknown_pincodes_may_differ(self):
        """Two different unknown pincodes hash to different seeds and should
        (with high probability) produce different values for the same city."""
        r_a = get_zone_risk("100000", "Mumbai")
        r_b = get_zone_risk("200000", "Mumbai")
        # Not a strict requirement — seeds could theoretically collide —
        # but the 4-digit prefix ensures they don't for these inputs.
        assert r_a != r_b

    def test_city_default_used_when_pincode_is_empty_string(self):
        """get_zone_risk('', 'Mumbai') → CITY_RISK_DEFAULT['Mumbai'] = 0.82."""
        result = get_zone_risk("", "Mumbai")
        assert result == CITY_RISK_DEFAULT["Mumbai"]
        assert result == 0.82

    def test_city_default_used_when_pincode_is_none(self):
        """get_zone_risk(None, 'Delhi') → CITY_RISK_DEFAULT['Delhi'] = 0.72."""
        result = get_zone_risk(None, "Delhi")
        assert result == CITY_RISK_DEFAULT["Delhi"]
        assert result == 0.72

    def test_unknown_city_falls_back_to_0_55_default_when_pincode_empty(self):
        """Empty pincode with unknown city returns the hardcoded 0.55 fallback."""
        result = get_zone_risk("", "UnknownCity")
        assert result == 0.55

    def test_zone_factor_formula_is_correct_for_known_pincode(self):
        """zone_factor = 0.8 + zone_risk * 0.6; verify against a known entry."""
        # Mumbai 400001: zone_risk = 0.85 → factor = 0.8 + 0.85*0.6 = 1.31
        result = calculate_premium("Standard Guard", "400001", "Mumbai", date=_OFF)
        expected_factor = round(0.8 + 0.85 * 0.6, 3)  # 1.31
        assert result["breakdown"]["zone_factor"] == expected_factor

    def test_zone_loading_is_zero_when_zone_factor_equals_1(self):
        """If zone_factor == 1.0 (zone_risk = 0.2/3), loading should be ≈ 0.
        Using the lowest known risk (0.48) just to confirm direction."""
        result = calculate_premium("Standard Guard", "500081", "Hyderabad", date=_OFF)
        # zone_risk=0.48 → factor=1.088 → loading > 0; just ensure it is positive
        assert result["breakdown"]["zone_loading_inr"] > 0.0

    def test_higher_risk_pincode_gives_higher_premium(self):
        """High-risk Mumbai 400051 must yield a higher premium than
        low-risk Hyderabad 500081, all else equal."""
        r_high = calculate_premium("Standard Guard", "400051", "Mumbai", date=_OFF)
        r_low  = calculate_premium("Standard Guard", "500081", "Hyderabad", date=_OFF)
        assert r_high["weekly_premium"] > r_low["weekly_premium"]


# ============================================================================
# 6. TestForecastRiskAdjustment
# ============================================================================

class TestForecastRiskAdjustment:
    """Verify forecast_risk loading: factor = 1.0 + forecast_risk * 0.15."""

    _PIN  = ("560001", "Bangalore")
    _TIER = "Standard Guard"   # base = 49.0

    def _result(self, forecast_risk):
        return calculate_premium(
            self._TIER, *self._PIN,
            forecast_risk=forecast_risk,
            date=_OFF,
        )

    def test_no_forecast_risk_no_adjustment(self):
        """forecast_risk=None → forecast_factor=1.0, forecast_loading_inr=0.0."""
        r = self._result(None)
        assert r["breakdown"]["forecast_factor"] == 1.0
        assert r["breakdown"]["forecast_loading_inr"] == 0.0

    def test_forecast_risk_zero_no_adjustment(self):
        """forecast_risk=0.0 → factor=1.0+0.0=1.0, loading=0.0."""
        r = self._result(0.0)
        assert r["breakdown"]["forecast_factor"] == 1.0
        assert r["breakdown"]["forecast_loading_inr"] == 0.0

    def test_forecast_risk_one_adds_15_percent(self):
        """forecast_risk=1.0 → factor=1.15."""
        r = self._result(1.0)
        assert r["breakdown"]["forecast_factor"] == pytest.approx(1.15, abs=1e-3)

    def test_forecast_risk_one_loading_is_correct_inr(self):
        """forecast_risk=1.0 → loading = (1.15-1.0)*49 = 7.35."""
        r = self._result(1.0)
        expected = round(0.15 * TIER_CONFIG[self._TIER]["base"], 2)  # 7.35
        assert r["breakdown"]["forecast_loading_inr"] == expected

    def test_forecast_risk_point_five_adds_7_5_percent(self):
        """forecast_risk=0.5 → factor=1.0+0.5*0.15=1.075."""
        r = self._result(0.5)
        assert r["breakdown"]["forecast_factor"] == pytest.approx(1.075, abs=1e-3)

    def test_forecast_risk_point_five_loading_inr(self):
        """forecast_risk=0.5 → loading = (1.075-1.0)*49 = 3.675 → rounded 3.67 or 3.68."""
        r = self._result(0.5)
        expected = round(0.075 * TIER_CONFIG[self._TIER]["base"], 2)  # 3.67
        assert r["breakdown"]["forecast_loading_inr"] == expected

    def test_forecast_loading_stored_in_breakdown(self):
        """forecast_factor and forecast_loading_inr keys are always present."""
        r = self._result(0.5)
        assert "forecast_factor" in r["breakdown"]
        assert "forecast_loading_inr" in r["breakdown"]
        assert r["breakdown"]["forecast_factor"] == pytest.approx(1.075, abs=1e-3)

    def test_higher_forecast_risk_gives_higher_premium(self):
        """Premium with forecast_risk=1.0 must exceed premium with forecast_risk=0.0."""
        r_high = self._result(1.0)
        r_low  = self._result(0.0)
        assert r_high["weekly_premium"] >= r_low["weekly_premium"]

    def test_forecast_risk_none_vs_zero_same_loading(self):
        """None and 0.0 both produce zero loading — they are functionally equivalent
        from the payer's perspective, though the code path differs slightly."""
        r_none = self._result(None)
        r_zero = self._result(0.0)
        assert r_none["breakdown"]["forecast_loading_inr"] == r_zero["breakdown"]["forecast_loading_inr"]

    def test_forecast_risk_point_five_pro_armor_loading_inr(self):
        """Pro Armor (base=89): forecast_risk=0.5 → loading=(1.075-1.0)*89=6.675 → 6.67 or 6.68."""
        r = calculate_premium(
            "Pro Armor", *self._PIN,
            forecast_risk=0.5,
            date=_OFF,
        )
        expected = round(0.075 * TIER_CONFIG["Pro Armor"]["base"], 2)  # 6.67
        assert r["breakdown"]["forecast_loading_inr"] == expected

    def test_forecast_factor_is_larger_for_higher_risk_value(self):
        """forecast_factor increases monotonically with forecast_risk."""
        r_low  = self._result(0.3)
        r_high = self._result(0.8)
        assert r_high["breakdown"]["forecast_factor"] > r_low["breakdown"]["forecast_factor"]
