"""
test_income_replacement.py
==========================
Validates Zynvaro's income-replacement payout model introduced in the Apr-2026
PS-compliance overhaul.  The old flat PAYOUT_TABLE is replaced by:

    payout = round(city_daily_income × replacement_rate / 10) * 10
    capped at tier max_daily

Tests are organised into four classes:
    TestCityCalibration        — city-specific income table correctness
    TestReplacementRateLogic   — rate ordering and coverage guarantees
    TestPayoutCalculation      — exact arithmetic verification + invariants
    TestPSCompliance           — regression guard vs. old zero-payout bug
"""

import sys
sys.path.insert(0, "D:/AeroFyta_DEVTrails-2026_FULL_HANDOFF/zynvaro-app/backend")

import pytest
from ml.premium_engine import (
    CITY_DAILY_INCOME,
    DEFAULT_DAILY_INCOME,
    TRIGGER_REPLACEMENT_RATES,
    TIER_CONFIG,
    get_payout_amount,
)

# ── convenience constants ────────────────────────────────────────────────────

ALL_TIERS = ["Basic Shield", "Standard Guard", "Pro Armor"]
ALL_TRIGGERS = list(TRIGGER_REPLACEMENT_RATES.keys())  # 6 triggers
ALL_CITIES = list(CITY_DAILY_INCOME.keys())            # 7 cities


# ════════════════════════════════════════════════════════════════════════════
# TestCityCalibration
# ════════════════════════════════════════════════════════════════════════════

class TestCityCalibration:
    """City-specific daily income table is correct and drives payout differences."""

    def test_mumbai_has_higher_income_than_kolkata(self):
        """Mumbai daily income exceeds Kolkata for every tier."""
        for tier in ALL_TIERS:
            assert CITY_DAILY_INCOME["Mumbai"][tier] > CITY_DAILY_INCOME["Kolkata"][tier], (
                f"Mumbai income should exceed Kolkata for {tier}"
            )

    def test_bangalore_pro_armor_is_highest_daily_income(self):
        """Bangalore Pro Armor (1450) is the highest single entry in the table."""
        blr_pro = CITY_DAILY_INCOME["Bangalore"]["Pro Armor"]
        assert blr_pro == 1450
        for city, incomes in CITY_DAILY_INCOME.items():
            for tier, income in incomes.items():
                if city == "Bangalore" and tier == "Pro Armor":
                    continue
                assert income <= blr_pro, (
                    f"{city}/{tier}={income} should not exceed Bangalore Pro Armor={blr_pro}"
                )

    def test_kolkata_basic_shield_is_lowest_daily_income(self):
        """Kolkata Basic Shield (750) is the lowest single entry in the table."""
        kol_basic = CITY_DAILY_INCOME["Kolkata"]["Basic Shield"]
        assert kol_basic == 750
        for city, incomes in CITY_DAILY_INCOME.items():
            for tier, income in incomes.items():
                assert income >= kol_basic, (
                    f"{city}/{tier}={income} should not be below Kolkata Basic Shield={kol_basic}"
                )

    def test_unknown_city_falls_back_to_default_income(self):
        """An unrecognised city ('Shimla') falls back to DEFAULT_DAILY_INCOME."""
        # Shimla is not in CITY_DAILY_INCOME, so the fallback path is taken.
        # DEFAULT_DAILY_INCOME["Standard Guard"] = 1000
        # 1000 * 0.55 = 550 → round(55.0)*10 = 550 → min(550, 600) = 550
        expected = get_payout_amount("Heavy Rainfall", "Standard Guard", city=None)
        result = get_payout_amount("Heavy Rainfall", "Standard Guard", city="Shimla")
        assert result == expected, (
            f"Unknown city should produce same payout as default; got {result} vs {expected}"
        )

    def test_none_city_falls_back_to_default_income(self):
        """Passing city=None explicitly uses DEFAULT_DAILY_INCOME."""
        # DEFAULT_DAILY_INCOME["Standard Guard"] = 1000
        # 1000 * 0.55 = 550 → 550.0
        result = get_payout_amount("Heavy Rainfall", "Standard Guard", city=None)
        assert result == 550.0


# ════════════════════════════════════════════════════════════════════════════
# TestReplacementRateLogic
# ════════════════════════════════════════════════════════════════════════════

class TestReplacementRateLogic:
    """Replacement rates are well-formed: non-zero, ordered by tier, extremes correct."""

    def test_all_6_triggers_cover_basic_shield(self):
        """Every trigger type has a non-zero rate for Basic Shield."""
        assert len(TRIGGER_REPLACEMENT_RATES) == 6
        for trigger, rates in TRIGGER_REPLACEMENT_RATES.items():
            assert "Basic Shield" in rates, f"Basic Shield missing from {trigger}"
            assert rates["Basic Shield"] > 0.0, f"Rate is 0 for Basic Shield / {trigger}"

    def test_all_6_triggers_cover_standard_guard(self):
        """Every trigger type has a non-zero rate for Standard Guard."""
        for trigger, rates in TRIGGER_REPLACEMENT_RATES.items():
            assert "Standard Guard" in rates, f"Standard Guard missing from {trigger}"
            assert rates["Standard Guard"] > 0.0, f"Rate is 0 for Standard Guard / {trigger}"

    def test_all_6_triggers_cover_pro_armor(self):
        """Every trigger type has a non-zero rate for Pro Armor."""
        for trigger, rates in TRIGGER_REPLACEMENT_RATES.items():
            assert "Pro Armor" in rates, f"Pro Armor missing from {trigger}"
            assert rates["Pro Armor"] > 0.0, f"Rate is 0 for Pro Armor / {trigger}"

    def test_pro_armor_rate_gte_standard_guard_for_all_triggers(self):
        """Pro Armor replacement rate >= Standard Guard for every trigger."""
        for trigger, rates in TRIGGER_REPLACEMENT_RATES.items():
            assert rates["Pro Armor"] >= rates["Standard Guard"], (
                f"{trigger}: Pro Armor rate {rates['Pro Armor']} < "
                f"Standard Guard rate {rates['Standard Guard']}"
            )

    def test_standard_guard_rate_gte_basic_shield_for_all_triggers(self):
        """Standard Guard replacement rate >= Basic Shield for every trigger."""
        for trigger, rates in TRIGGER_REPLACEMENT_RATES.items():
            assert rates["Standard Guard"] >= rates["Basic Shield"], (
                f"{trigger}: Standard Guard rate {rates['Standard Guard']} < "
                f"Basic Shield rate {rates['Basic Shield']}"
            )

    def test_extreme_rain_has_highest_rate_for_basic_shield(self):
        """Extreme Rain / Flooding (0.55) has the highest Basic Shield rate."""
        extreme_rate = TRIGGER_REPLACEMENT_RATES["Extreme Rain / Flooding"]["Basic Shield"]
        for trigger, rates in TRIGGER_REPLACEMENT_RATES.items():
            if trigger == "Extreme Rain / Flooding":
                continue
            assert extreme_rate >= rates["Basic Shield"], (
                f"Extreme Rain Basic Shield rate {extreme_rate} should be >= "
                f"{trigger} rate {rates['Basic Shield']}"
            )

    def test_extreme_rain_has_highest_rate_for_pro_armor(self):
        """Extreme Rain / Flooding (0.90) has the highest Pro Armor rate."""
        extreme_rate = TRIGGER_REPLACEMENT_RATES["Extreme Rain / Flooding"]["Pro Armor"]
        for trigger, rates in TRIGGER_REPLACEMENT_RATES.items():
            if trigger == "Extreme Rain / Flooding":
                continue
            assert extreme_rate >= rates["Pro Armor"], (
                f"Extreme Rain Pro Armor rate {extreme_rate} should be >= "
                f"{trigger} rate {rates['Pro Armor']}"
            )


# ════════════════════════════════════════════════════════════════════════════
# TestPayoutCalculation
# ════════════════════════════════════════════════════════════════════════════

class TestPayoutCalculation:
    """Exact arithmetic verification for representative cases plus invariants."""

    # ── exact spot-checks ───────────────────────────────────────────────────

    def test_mumbai_standard_heavy_rainfall_payout(self):
        """
        Mumbai Standard Guard + Heavy Rainfall:
            1100 * 0.55 = 605 → round(60.5)*10 = 600 (Python banker's rounding)
            min(600, max_daily=600) = 600
        """
        result = get_payout_amount("Heavy Rainfall", "Standard Guard", "Mumbai")
        assert result == 600.0

    def test_bangalore_basic_shield_heavy_rainfall_payout(self):
        """
        Bangalore Basic Shield + Heavy Rainfall:
            950 * 0.35 = 332.5 → round(33.25)*10 = 330
            min(330, max_daily=300) = 300  (cap applies)
        """
        result = get_payout_amount("Heavy Rainfall", "Basic Shield", "Bangalore")
        assert result == 300.0

    def test_mumbai_pro_armor_extreme_rain_capped(self):
        """
        Mumbai Pro Armor + Extreme Rain / Flooding:
            1400 * 0.90 = 1260 → round(126)*10 = 1260
            min(1260, max_daily=1000) = 1000  (cap applies)
        """
        result = get_payout_amount("Extreme Rain / Flooding", "Pro Armor", "Mumbai")
        assert result == 1000.0

    def test_delhi_standard_aqi_payout(self):
        """
        Delhi Standard Guard + Hazardous AQI:
            1050 * 0.55 = 577.5 → round(57.75)*10 = 580
            min(580, max_daily=600) = 580
        """
        result = get_payout_amount("Hazardous AQI", "Standard Guard", "Delhi")
        assert result == 580.0

    def test_bangalore_platform_outage_basic_shield(self):
        """
        Bangalore Basic Shield + Platform Outage:
            950 * 0.25 = 237.5 → round(23.75)*10 = 240
            min(240, max_daily=300) = 240
        """
        result = get_payout_amount("Platform Outage", "Basic Shield", "Bangalore")
        assert result == 240.0

    # ── invariants across all 126 combinations ──────────────────────────────

    def test_payout_is_always_multiple_of_10(self):
        """
        For all 6 triggers × 3 tiers × 7 cities the payout is a multiple of ₹10.
        The round(... / 10) * 10 formula guarantees this before capping; caps are
        also multiples of 10, so the property is preserved.
        """
        for trigger in ALL_TRIGGERS:
            for tier in ALL_TIERS:
                for city in ALL_CITIES:
                    payout = get_payout_amount(trigger, tier, city)
                    assert payout % 10 == 0, (
                        f"{trigger}/{tier}/{city}: payout {payout} is not a multiple of 10"
                    )

    def test_payout_never_exceeds_tier_max_daily(self):
        """No payout ever breaches its tier's max_daily cap."""
        for trigger in ALL_TRIGGERS:
            for tier in ALL_TIERS:
                cap = TIER_CONFIG[tier]["max_daily"]
                for city in ALL_CITIES:
                    payout = get_payout_amount(trigger, tier, city)
                    assert payout <= cap, (
                        f"{trigger}/{tier}/{city}: payout {payout} exceeds cap {cap}"
                    )

    def test_return_type_is_always_float(self):
        """get_payout_amount always returns a Python float, never int or None."""
        for trigger in ALL_TRIGGERS:
            for tier in ALL_TIERS:
                for city in ALL_CITIES:
                    result = get_payout_amount(trigger, tier, city)
                    assert isinstance(result, float), (
                        f"{trigger}/{tier}/{city}: expected float, got {type(result).__name__}"
                    )


# ════════════════════════════════════════════════════════════════════════════
# TestPSCompliance
# ════════════════════════════════════════════════════════════════════════════

class TestPSCompliance:
    """
    Regression guard: the old flat PAYOUT_TABLE returned ₹0 for Basic Shield
    on Heavy Rainfall and several other triggers — a direct PS violation because
    gig workers on the entry tier received no income replacement.  These tests
    ensure the income-replacement model fixes that permanently.
    """

    def test_basic_shield_gets_nonzero_for_heavy_rainfall(self):
        """
        The primary PS bug: Basic Shield / Heavy Rainfall returned 0 in the old table.
        Income-replacement must produce a positive payout for every city.
        """
        result = get_payout_amount("Heavy Rainfall", "Basic Shield", "Bangalore")
        assert result > 0, (
            "PS regression: Basic Shield / Heavy Rainfall / Bangalore must not be 0"
        )

    def test_basic_shield_gets_nonzero_for_all_triggers(self):
        """Basic Shield receives a positive payout for every one of the 6 triggers."""
        for trigger in ALL_TRIGGERS:
            result = get_payout_amount(trigger, "Basic Shield", "Mumbai")
            assert result > 0, (
                f"PS regression: Basic Shield / {trigger} returned 0 — must be > 0"
            )

    def test_all_tiers_all_cities_all_triggers_nonzero(self):
        """
        Full exhaustive check: all 6 × 3 × 7 = 126 combinations yield positive
        payouts.  Any zero would indicate a gap in coverage and a PS violation.
        """
        failures = []
        for trigger in ALL_TRIGGERS:
            for tier in ALL_TIERS:
                for city in ALL_CITIES:
                    payout = get_payout_amount(trigger, tier, city)
                    if payout <= 0:
                        failures.append(f"{trigger} / {tier} / {city} → {payout}")

        assert not failures, (
            f"PS violation: {len(failures)} zero-payout combinations found:\n"
            + "\n".join(failures)
        )
