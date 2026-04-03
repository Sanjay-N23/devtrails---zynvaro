"""
Unit tests for services/trigger_engine.py — trigger evaluation logic.

Covers:
  - _make_trigger()
  - simulate_trigger()
  - mock_weather()
  - mock_aqi()
  - mock_civil_disruption()
  - mock_platform_status()
"""

import sys
import unittest
from unittest.mock import patch
from datetime import datetime

sys.path.insert(0, "D:/AeroFyta_DEVTrails-2026_FULL_HANDOFF/zynvaro-app/backend")

from services.trigger_engine import (
    TRIGGERS,
    _make_trigger,
    simulate_trigger,
    mock_weather,
    mock_aqi,
    mock_civil_disruption,
    mock_platform_status,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

REQUIRED_TRIGGER_KEYS = {
    "trigger_type", "city", "measured_value", "threshold_value", "unit",
    "source_primary", "source_secondary", "is_validated", "severity",
    "description", "detected_at", "expires_at",
}

REQUIRED_PLATFORM_KEYS = {"platform", "status", "latency_ms", "error_rate", "checked_at"}

REQUIRED_DISRUPTION_KEYS = {"city", "active_restrictions", "type", "duration_hours", "source"}

KNOWN_DISRUPTION_TYPES = {
    "Protest / Bandh", "Section 144 Order", "Communal Tension", "Transport Strike"
}


def _is_iso_string(value: str) -> bool:
    """Return True when value can be parsed as an ISO 8601 datetime string."""
    try:
        datetime.fromisoformat(value)
        return True
    except (TypeError, ValueError):
        return False


# ─────────────────────────────────────────────────────────────────────────────
# _make_trigger() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMakeTrigger(unittest.TestCase):

    def _make(self, trigger_type="Heavy Rainfall", city="Mumbai",
              value=72.5, severity="high", **kwargs):
        return _make_trigger(trigger_type, city, value, severity, **kwargs)

    # ── Structure ────────────────────────────────────────────────────────────

    def test_returns_all_required_keys(self):
        result = self._make()
        missing = REQUIRED_TRIGGER_KEYS - result.keys()
        self.assertEqual(missing, set(), f"Missing keys: {missing}")

    def test_is_validated_is_always_true(self):
        result = self._make()
        self.assertIs(result["is_validated"], True)

    def test_is_validated_always_true_for_unknown_trigger(self):
        result = _make_trigger("NonExistent", "Delhi", 50.0, "low")
        self.assertIs(result["is_validated"], True)

    # ── Value correctness ────────────────────────────────────────────────────

    def test_measured_value_rounded_to_2_decimals(self):
        result = _make_trigger("Heavy Rainfall", "Mumbai", 72.512, "high")
        self.assertEqual(result["measured_value"], 72.51)

    def test_measured_value_rounded_up_at_third_decimal(self):
        result = _make_trigger("Heavy Rainfall", "Mumbai", 72.515, "high")
        self.assertEqual(result["measured_value"], round(72.515, 2))

    def test_trigger_type_and_city_stored_verbatim(self):
        result = _make_trigger("Severe Heatwave", "Chennai", 46.0, "high")
        self.assertEqual(result["trigger_type"], "Severe Heatwave")
        self.assertEqual(result["city"], "Chennai")

    def test_severity_stored_verbatim(self):
        result = _make_trigger("Hazardous AQI", "Delhi", 450.0, "extreme")
        self.assertEqual(result["severity"], "extreme")

    # ── Known trigger — Heavy Rainfall ───────────────────────────────────────

    def test_known_trigger_heavy_rainfall_threshold(self):
        result = self._make(trigger_type="Heavy Rainfall")
        self.assertEqual(result["threshold_value"], 64.5)

    def test_known_trigger_heavy_rainfall_unit(self):
        result = self._make(trigger_type="Heavy Rainfall")
        self.assertEqual(result["unit"], "mm/24hr")

    # ── All known triggers — threshold and unit round-trip ───────────────────

    def test_extreme_rain_flooding_threshold_and_unit(self):
        result = _make_trigger("Extreme Rain / Flooding", "Pune", 210.0, "extreme")
        self.assertEqual(result["threshold_value"], 204.5)
        self.assertEqual(result["unit"], "mm/24hr")

    def test_severe_heatwave_threshold_and_unit(self):
        result = _make_trigger("Severe Heatwave", "Delhi", 46.0, "high")
        self.assertEqual(result["threshold_value"], 45.0)
        self.assertEqual(result["unit"], "°C")

    def test_hazardous_aqi_threshold_and_unit(self):
        result = _make_trigger("Hazardous AQI", "Delhi", 450.0, "high")
        self.assertEqual(result["threshold_value"], 400.0)
        self.assertEqual(result["unit"], "AQI")

    def test_platform_outage_threshold_and_unit(self):
        result = _make_trigger("Platform Outage", "Mumbai", 20.0, "high")
        self.assertEqual(result["threshold_value"], 15.0)
        self.assertEqual(result["unit"], "minutes down")

    def test_civil_disruption_threshold_and_unit(self):
        result = _make_trigger("Civil Disruption", "Bangalore", 6.0, "high")
        self.assertEqual(result["threshold_value"], 4.0)
        self.assertEqual(result["unit"], "hours restricted")

    # ── Unknown trigger type ─────────────────────────────────────────────────

    def test_unknown_trigger_type_threshold_is_zero(self):
        result = _make_trigger("Solar Flare", "Mumbai", 99.9, "low")
        self.assertEqual(result["threshold_value"], 0)

    def test_unknown_trigger_type_unit_is_empty_string(self):
        result = _make_trigger("Solar Flare", "Mumbai", 99.9, "low")
        self.assertEqual(result["unit"], "")

    def test_unknown_trigger_type_source_primary_default(self):
        result = _make_trigger("Solar Flare", "Mumbai", 99.9, "low")
        self.assertEqual(result["source_primary"], "API")

    def test_unknown_trigger_type_source_secondary_default(self):
        result = _make_trigger("Solar Flare", "Mumbai", 99.9, "low")
        self.assertEqual(result["source_secondary"], "Mock")

    # ── Description ──────────────────────────────────────────────────────────

    def test_custom_desc_overrides_default_description(self):
        custom = "My custom description text"
        result = _make_trigger("Heavy Rainfall", "Mumbai", 72.5, "high", desc=custom)
        self.assertEqual(result["description"], custom)

    def test_default_description_contains_trigger_type(self):
        result = _make_trigger("Severe Heatwave", "Hyderabad", 46.0, "high")
        self.assertIn("Severe Heatwave", result["description"])

    def test_default_description_contains_city(self):
        result = _make_trigger("Severe Heatwave", "Hyderabad", 46.0, "high")
        self.assertIn("Hyderabad", result["description"])

    def test_none_desc_uses_default_description(self):
        result = _make_trigger("Heavy Rainfall", "Mumbai", 72.5, "high", desc=None)
        self.assertIn("Heavy Rainfall", result["description"])
        self.assertIn("Mumbai", result["description"])

    # ── Timestamps ───────────────────────────────────────────────────────────

    def test_detected_at_is_iso_format_string(self):
        result = self._make()
        self.assertTrue(_is_iso_string(result["detected_at"]),
                        f"detected_at is not ISO: {result['detected_at']!r}")

    def test_expires_at_is_iso_format_string(self):
        result = self._make()
        self.assertTrue(_is_iso_string(result["expires_at"]),
                        f"expires_at is not ISO: {result['expires_at']!r}")

    def test_expires_at_is_after_detected_at(self):
        result = self._make()
        detected = datetime.fromisoformat(result["detected_at"])
        expires = datetime.fromisoformat(result["expires_at"])
        self.assertGreater(expires, detected)

    def test_expires_at_is_approximately_6_hours_after_detected_at(self):
        result = self._make()
        detected = datetime.fromisoformat(result["detected_at"])
        expires = datetime.fromisoformat(result["expires_at"])
        delta_hours = (expires - detected).total_seconds() / 3600
        # Allow a tiny margin for execution time between the two datetime.utcnow() calls
        self.assertAlmostEqual(delta_hours, 6.0, delta=0.01)


# ─────────────────────────────────────────────────────────────────────────────
# simulate_trigger() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestSimulateTrigger(unittest.TestCase):

    def test_heavy_rainfall_demo_value(self):
        result = simulate_trigger("Heavy Rainfall", "Mumbai")
        self.assertEqual(result["measured_value"], 72.5)

    def test_extreme_rain_flooding_demo_value(self):
        result = simulate_trigger("Extreme Rain / Flooding", "Chennai")
        self.assertEqual(result["measured_value"], 210.0)

    def test_severe_heatwave_demo_value(self):
        result = simulate_trigger("Severe Heatwave", "Delhi")
        self.assertEqual(result["measured_value"], 46.2)

    def test_hazardous_aqi_demo_value(self):
        result = simulate_trigger("Hazardous AQI", "Delhi")
        self.assertEqual(result["measured_value"], 485.0)

    def test_platform_outage_demo_value(self):
        result = simulate_trigger("Platform Outage", "Bangalore")
        self.assertEqual(result["measured_value"], 20.0)

    def test_civil_disruption_demo_value(self):
        result = simulate_trigger("Civil Disruption", "Pune")
        self.assertEqual(result["measured_value"], 6.0)

    def test_all_six_trigger_types_have_specific_demo_values_not_fallback(self):
        """None of the 6 known types should fall back to 100.0."""
        known_types = [
            "Heavy Rainfall", "Extreme Rain / Flooding", "Severe Heatwave",
            "Hazardous AQI", "Platform Outage", "Civil Disruption",
        ]
        for t in known_types:
            with self.subTest(trigger_type=t):
                result = simulate_trigger(t, "Mumbai")
                self.assertNotEqual(
                    result["measured_value"], 100.0,
                    f"{t!r} should not fall back to 100.0",
                )

    def test_description_contains_demo_prefix(self):
        result = simulate_trigger("Severe Heatwave", "Delhi")
        self.assertIn("[DEMO]", result["description"])

    def test_description_contains_trigger_type(self):
        result = simulate_trigger("Platform Outage", "Mumbai")
        self.assertIn("Platform Outage", result["description"])

    def test_description_contains_city(self):
        result = simulate_trigger("Platform Outage", "Mumbai")
        self.assertIn("Mumbai", result["description"])

    def test_unknown_trigger_type_falls_back_to_100(self):
        result = simulate_trigger("Volcanic Eruption", "Bangalore")
        self.assertEqual(result["measured_value"], 100.0)

    def test_unknown_trigger_type_description_has_demo_prefix(self):
        result = simulate_trigger("Volcanic Eruption", "Bangalore")
        self.assertIn("[DEMO]", result["description"])

    def test_returns_all_required_trigger_keys(self):
        result = simulate_trigger("Heavy Rainfall", "Mumbai")
        missing = REQUIRED_TRIGGER_KEYS - result.keys()
        self.assertEqual(missing, set(), f"Missing keys: {missing}")


# ─────────────────────────────────────────────────────────────────────────────
# mock_weather() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMockWeather(unittest.TestCase):

    # ── Values exceed known thresholds ───────────────────────────────────────

    def test_rain_scenario_rain_above_heavy_rainfall_threshold(self):
        """72 mm/24hr > 64.5 mm/24hr (Heavy Rainfall threshold)."""
        data = mock_weather("Mumbai", "rain")
        self.assertGreater(data["rain_24h_mm"], TRIGGERS["Heavy Rainfall"]["threshold"])

    def test_rain_scenario_rain_exact_value(self):
        data = mock_weather("Delhi", "rain")
        self.assertEqual(data["rain_24h_mm"], 72)

    def test_flooding_scenario_rain_above_extreme_rain_threshold(self):
        """215 mm/24hr > 204.5 mm/24hr (Extreme Rain / Flooding threshold)."""
        data = mock_weather("Mumbai", "flooding")
        self.assertGreater(data["rain_24h_mm"], TRIGGERS["Extreme Rain / Flooding"]["threshold"])

    def test_flooding_scenario_rain_exact_value(self):
        data = mock_weather("Chennai", "flooding")
        self.assertEqual(data["rain_24h_mm"], 215)

    def test_heatwave_scenario_temp_above_severe_heatwave_threshold(self):
        """46°C > 45.0°C (Severe Heatwave threshold)."""
        data = mock_weather("Delhi", "heatwave")
        self.assertGreater(data["temp"], TRIGGERS["Severe Heatwave"]["threshold"])

    def test_heatwave_scenario_temp_exact_value(self):
        data = mock_weather("Delhi", "heatwave")
        self.assertEqual(data["temp"], 46)

    def test_aqi_scenario_aqi_above_hazardous_aqi_threshold(self):
        """485 > 400.0 (Hazardous AQI threshold)."""
        data = mock_weather("Delhi", "aqi")
        self.assertGreater(data["aqi"], TRIGGERS["Hazardous AQI"]["threshold"])

    def test_aqi_scenario_aqi_exact_value(self):
        data = mock_weather("Delhi", "aqi")
        self.assertEqual(data["aqi"], 485)

    # ── Normal scenario stays below all thresholds ───────────────────────────

    def test_normal_scenario_rain_below_heavy_rainfall_threshold(self):
        data = mock_weather("Bangalore", "normal")
        self.assertLess(data["rain_24h_mm"], TRIGGERS["Heavy Rainfall"]["threshold"])

    def test_normal_scenario_rain_exact_value(self):
        data = mock_weather("Bangalore", "normal")
        self.assertEqual(data["rain_24h_mm"], 5)

    def test_normal_scenario_temp_below_heatwave_threshold(self):
        data = mock_weather("Bangalore", "normal")
        self.assertLess(data["temp"], TRIGGERS["Severe Heatwave"]["threshold"])

    def test_normal_scenario_temp_exact_value(self):
        data = mock_weather("Bangalore", "normal")
        self.assertEqual(data["temp"], 28)

    def test_normal_scenario_aqi_below_hazardous_threshold(self):
        data = mock_weather("Bangalore", "normal")
        self.assertLess(data["aqi"], TRIGGERS["Hazardous AQI"]["threshold"])

    # ── Unknown scenario falls back to normal ────────────────────────────────

    def test_unknown_scenario_falls_back_to_normal(self):
        data = mock_weather("Mumbai", "blizzard")
        normal = mock_weather("Mumbai", "normal")
        self.assertEqual(data, normal)

    def test_unknown_scenario_has_normal_rain_value(self):
        data = mock_weather("Delhi", "thunderstorm")
        self.assertEqual(data["rain_24h_mm"], 5)

    # ── city argument is accepted (no effect on deterministic scenarios) ─────

    def test_city_parameter_accepted_without_error(self):
        for city in ["Mumbai", "Delhi", "Bangalore", "Hyderabad", "Chennai", "Pune"]:
            with self.subTest(city=city):
                data = mock_weather(city, "normal")
                self.assertIn("temp", data)

    # ── Return keys present ──────────────────────────────────────────────────

    def test_all_scenarios_return_temp_rain_aqi_keys(self):
        for scenario in ["normal", "rain", "flooding", "heatwave", "aqi"]:
            with self.subTest(scenario=scenario):
                data = mock_weather("Mumbai", scenario)
                for key in ("temp", "rain_24h_mm", "aqi"):
                    self.assertIn(key, data, f"Key {key!r} missing in scenario {scenario!r}")


# ─────────────────────────────────────────────────────────────────────────────
# mock_aqi() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMockAqi(unittest.TestCase):
    """
    random.uniform(-20, 40) is added to the base value, so:
      result ∈ [base - 20, base + 40]
    Delhi base = 280  → [260, 320]
    Unknown city base = 100 → [80, 140]
    """

    def test_delhi_result_within_expected_range(self):
        for _ in range(50):
            result = mock_aqi("Delhi")
            self.assertGreaterEqual(result, 260,
                                    f"Delhi AQI {result} below minimum 260")
            self.assertLessEqual(result, 320,
                                 f"Delhi AQI {result} above maximum 320")

    def test_mumbai_result_within_expected_range(self):
        """Mumbai base = 120 → [100, 160]."""
        for _ in range(50):
            result = mock_aqi("Mumbai")
            self.assertGreaterEqual(result, 100)
            self.assertLessEqual(result, 160)

    def test_bangalore_result_within_expected_range(self):
        """Bangalore base = 95 → [75, 135]."""
        for _ in range(50):
            result = mock_aqi("Bangalore")
            self.assertGreaterEqual(result, 75)
            self.assertLessEqual(result, 135)

    def test_hyderabad_result_within_expected_range(self):
        """Hyderabad base = 110 → [90, 150]."""
        for _ in range(50):
            result = mock_aqi("Hyderabad")
            self.assertGreaterEqual(result, 90)
            self.assertLessEqual(result, 150)

    def test_chennai_result_within_expected_range(self):
        """Chennai base = 100 → [80, 140]."""
        for _ in range(50):
            result = mock_aqi("Chennai")
            self.assertGreaterEqual(result, 80)
            self.assertLessEqual(result, 140)

    def test_pune_result_within_expected_range(self):
        """Pune base = 115 → [95, 155]."""
        for _ in range(50):
            result = mock_aqi("Pune")
            self.assertGreaterEqual(result, 95)
            self.assertLessEqual(result, 155)

    def test_unknown_city_result_within_expected_range(self):
        """Unknown city base = 100 → [80, 140]."""
        for _ in range(50):
            result = mock_aqi("Kolkata")
            self.assertGreaterEqual(result, 80)
            self.assertLessEqual(result, 140)

    def test_result_is_float(self):
        """random.uniform always returns float; result must not be a plain int."""
        result = mock_aqi("Delhi")
        self.assertIsInstance(result, float)

    def test_unknown_city_returns_float(self):
        result = mock_aqi("Surat")
        self.assertIsInstance(result, float)


# ─────────────────────────────────────────────────────────────────────────────
# mock_civil_disruption() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMockCivilDisruption(unittest.TestCase):

    # ── Structure ────────────────────────────────────────────────────────────

    def test_returns_all_required_keys(self):
        result = mock_civil_disruption("Mumbai")
        missing = REQUIRED_DISRUPTION_KEYS - result.keys()
        self.assertEqual(missing, set(), f"Missing keys: {missing}")

    def test_city_stored_verbatim(self):
        result = mock_civil_disruption("Hyderabad")
        self.assertEqual(result["city"], "Hyderabad")

    def test_source_always_gdelt_mock(self):
        for _ in range(20):
            result = mock_civil_disruption("Delhi")
            self.assertEqual(result["source"], "GDELT (mock)")

    # ── Inactive disruption branch ────────────────────────────────────────────

    def test_inactive_disruption_type_is_none(self):
        # Patch random.random to return a value > 0.08, guaranteeing inactive
        with patch("services.trigger_engine.random.random", return_value=0.50):
            result = mock_civil_disruption("Mumbai")
        self.assertFalse(result["active_restrictions"])
        self.assertIsNone(result["type"])

    def test_inactive_disruption_duration_hours_is_zero(self):
        with patch("services.trigger_engine.random.random", return_value=0.50):
            result = mock_civil_disruption("Mumbai")
        self.assertEqual(result["duration_hours"], 0)

    # ── Active disruption branch ──────────────────────────────────────────────

    def test_active_disruption_type_is_in_known_types(self):
        # Patch random.random to return a value < 0.08, guaranteeing active
        # Also patch random.choice to return a deterministic value
        with patch("services.trigger_engine.random.random", return_value=0.01), \
             patch("services.trigger_engine.random.uniform", return_value=6.0), \
             patch("services.trigger_engine.random.choice", return_value="Transport Strike"):
            result = mock_civil_disruption("Delhi")
        self.assertTrue(result["active_restrictions"])
        self.assertIn(result["type"], KNOWN_DISRUPTION_TYPES)

    def test_active_disruption_duration_hours_in_valid_range(self):
        """duration_hours must be in [4.5, 10.0] when active."""
        # Patch random.uniform to a value inside the valid range
        with patch("services.trigger_engine.random.random", return_value=0.01), \
             patch("services.trigger_engine.random.uniform", return_value=7.25), \
             patch("services.trigger_engine.random.choice", return_value="Protest / Bandh"):
            result = mock_civil_disruption("Chennai")
        self.assertGreaterEqual(result["duration_hours"], 4.5)
        self.assertLessEqual(result["duration_hours"], 10.0)

    def test_active_disruption_active_restrictions_is_true(self):
        with patch("services.trigger_engine.random.random", return_value=0.01), \
             patch("services.trigger_engine.random.uniform", return_value=5.0), \
             patch("services.trigger_engine.random.choice", return_value="Section 144 Order"):
            result = mock_civil_disruption("Bangalore")
        self.assertTrue(result["active_restrictions"])

    # ── Boundary: exactly 0.08 is not active (strict less-than) ─────────────

    def test_probability_boundary_at_exactly_0_08_is_inactive(self):
        """is_active = random.random() < 0.08 — at exactly 0.08 it is False."""
        with patch("services.trigger_engine.random.random", return_value=0.08):
            result = mock_civil_disruption("Pune")
        self.assertFalse(result["active_restrictions"])

    def test_probability_boundary_just_below_0_08_is_active(self):
        with patch("services.trigger_engine.random.random", return_value=0.0799), \
             patch("services.trigger_engine.random.uniform", return_value=5.0), \
             patch("services.trigger_engine.random.choice", return_value="Protest / Bandh"):
            result = mock_civil_disruption("Pune")
        self.assertTrue(result["active_restrictions"])

    # ── Statistical test: approximately 8% active rate ───────────────────────

    def test_statistical_active_rate_approximately_8_percent(self):
        """
        Over 1000 samples the activation rate should be within [3%, 13%]
        (99% CI for a Binomial(1000, 0.08) is roughly 4.5%–11.5%;
        we use the wider [3%, 13%] band for robustness).
        """
        import random as stdlib_random
        stdlib_random.seed(42)  # reproducible but still calls real random.random
        active_count = sum(
            1 for _ in range(1000) if mock_civil_disruption("Delhi")["active_restrictions"]
        )
        rate = active_count / 1000
        self.assertGreaterEqual(rate, 0.03,
                                f"Active rate {rate:.3f} too low (expected ~0.08)")
        self.assertLessEqual(rate, 0.13,
                             f"Active rate {rate:.3f} too high (expected ~0.08)")


# ─────────────────────────────────────────────────────────────────────────────
# mock_platform_status() tests
# ─────────────────────────────────────────────────────────────────────────────

class TestMockPlatformStatus(unittest.TestCase):

    # ── Structure ────────────────────────────────────────────────────────────

    def test_returns_all_required_keys(self):
        result = mock_platform_status("Blinkit")
        missing = REQUIRED_PLATFORM_KEYS - result.keys()
        self.assertEqual(missing, set(), f"Missing keys: {missing}")

    def test_platform_name_stored_verbatim(self):
        result = mock_platform_status("Zepto")
        self.assertEqual(result["platform"], "Zepto")

    def test_status_is_up_or_down(self):
        for _ in range(30):
            result = mock_platform_status("Swiggy")
            self.assertIn(result["status"], {"UP", "DOWN"})

    def test_checked_at_is_iso_format_string(self):
        result = mock_platform_status("Blinkit")
        self.assertTrue(_is_iso_string(result["checked_at"]),
                        f"checked_at is not ISO: {result['checked_at']!r}")

    # ── DOWN branch ──────────────────────────────────────────────────────────

    def test_down_status_latency_is_9999(self):
        with patch("services.trigger_engine.random.random", return_value=0.01):
            result = mock_platform_status("Blinkit")
        self.assertEqual(result["status"], "DOWN")
        self.assertEqual(result["latency_ms"], 9999)

    def test_down_status_error_rate_is_0_95(self):
        with patch("services.trigger_engine.random.random", return_value=0.01):
            result = mock_platform_status("Blinkit")
        self.assertEqual(result["error_rate"], 0.95)

    # ── UP branch ────────────────────────────────────────────────────────────

    def test_up_status_latency_in_valid_range(self):
        """latency_ms must be in [120, 350] when UP."""
        with patch("services.trigger_engine.random.random", return_value=0.50), \
             patch("services.trigger_engine.random.randint", return_value=200):
            result = mock_platform_status("Zepto")
        self.assertEqual(result["status"], "UP")
        self.assertGreaterEqual(result["latency_ms"], 120)
        self.assertLessEqual(result["latency_ms"], 350)

    def test_up_status_error_rate_below_0_02(self):
        with patch("services.trigger_engine.random.random", return_value=0.50), \
             patch("services.trigger_engine.random.randint", return_value=250), \
             patch("services.trigger_engine.random.uniform", return_value=0.01):
            result = mock_platform_status("Swiggy")
        self.assertEqual(result["status"], "UP")
        self.assertLess(result["error_rate"], 0.02)

    def test_up_status_error_rate_non_negative(self):
        with patch("services.trigger_engine.random.random", return_value=0.50), \
             patch("services.trigger_engine.random.randint", return_value=300), \
             patch("services.trigger_engine.random.uniform", return_value=0.005):
            result = mock_platform_status("Blinkit")
        self.assertGreaterEqual(result["error_rate"], 0.0)

    # ── Probability boundary ─────────────────────────────────────────────────

    def test_probability_boundary_at_exactly_0_05_is_up(self):
        """is_down = random.random() < 0.05 — at exactly 0.05 it is False (UP)."""
        with patch("services.trigger_engine.random.random", return_value=0.05), \
             patch("services.trigger_engine.random.randint", return_value=200), \
             patch("services.trigger_engine.random.uniform", return_value=0.01):
            result = mock_platform_status("Blinkit")
        self.assertEqual(result["status"], "UP")

    def test_probability_boundary_just_below_0_05_is_down(self):
        with patch("services.trigger_engine.random.random", return_value=0.0499):
            result = mock_platform_status("Blinkit")
        self.assertEqual(result["status"], "DOWN")


if __name__ == "__main__":
    unittest.main(verbosity=2)
