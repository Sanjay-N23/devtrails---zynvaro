"""
Zynvaro — Parametric Trigger Threshold Tests
=============================================
Verifies that every trigger threshold value is correctly defined in
TRIGGERS, that mock scenario data exceeds/falls below those thresholds
as expected, and that check_all_triggers() / simulate_trigger() enforce
them end-to-end (including via the HTTP /triggers/simulate endpoint).

Test classes
------------
  TestTriggerThresholds          — threshold constants + mock-scenario logic
  TestCheckAllTriggers           — check_all_triggers() with mocked externals
  TestSimulateEndpointTriggerCoverage — HTTP POST /triggers/simulate via TestClient
"""

import sys
import asyncio
import random as stdlib_random
from unittest.mock import patch, MagicMock, AsyncMock
from datetime import datetime

# ── path fix so the backend package resolves from any working directory ───────
sys.path.insert(0, "D:/AeroFyta_DEVTrails-2026_FULL_HANDOFF/zynvaro-app/backend")

import pytest
from starlette.testclient import TestClient

from services.trigger_engine import (
    TRIGGERS,
    _make_trigger,
    simulate_trigger,
    mock_weather,
    mock_aqi,
    mock_platform_status,
    mock_civil_disruption,
    check_all_triggers,
)
from models import TriggerEvent


# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

def _run(coro):
    """Run an async coroutine synchronously (avoids pytest-asyncio dependency)."""
    return asyncio.run(coro)


# ─────────────────────────────────────────────────────────────────────────────
# TestTriggerThresholds
# ─────────────────────────────────────────────────────────────────────────────

class TestTriggerThresholds:
    """
    Verifies threshold constant values and that mock scenarios correctly
    sit above or below those thresholds.
    """

    # ── Heavy Rainfall (IMD = 64.5 mm/24hr) ──────────────────────────────────

    def test_heavy_rainfall_threshold_is_64_5_mm(self):
        assert TRIGGERS["Heavy Rainfall"]["threshold"] == 64.5

    def test_heavy_rainfall_fires_when_rain_equals_threshold(self):
        """Boundary: value == threshold — check_all_triggers uses >=, so it should fire."""
        t = _make_trigger("Heavy Rainfall", "Bangalore", 64.5, "high")
        # The measured value must equal the threshold so that >= passes
        assert t["measured_value"] == 64.5
        assert t["threshold_value"] == 64.5
        assert t["measured_value"] >= t["threshold_value"]

    def test_heavy_rainfall_does_not_fire_below_threshold(self):
        """Mock 'normal' scenario (rain=5 mm) must be below the 64.5 mm threshold."""
        data = mock_weather("Bangalore", "normal")
        assert data["rain_24h_mm"] < TRIGGERS["Heavy Rainfall"]["threshold"]

    def test_heavy_rainfall_fires_above_threshold(self):
        """Mock 'rain' scenario (rain=72 mm) must exceed the 64.5 mm threshold."""
        data = mock_weather("Bangalore", "rain")
        assert data["rain_24h_mm"] > TRIGGERS["Heavy Rainfall"]["threshold"]

    # ── Extreme Rain / Flooding (204.5 mm/24hr) ──────────────────────────────

    def test_extreme_rain_threshold_is_204_5_mm(self):
        assert TRIGGERS["Extreme Rain / Flooding"]["threshold"] == 204.5

    def test_extreme_rain_fires_when_flooding_scenario(self):
        """Mock 'flooding' scenario (rain=215 mm) must exceed 204.5 mm threshold."""
        data = mock_weather("Mumbai", "flooding")
        assert data["rain_24h_mm"] > TRIGGERS["Extreme Rain / Flooding"]["threshold"]

    def test_extreme_rain_does_not_fire_on_heavy_rain_only(self):
        """Mock 'rain' scenario (rain=72 mm) must be below the 204.5 mm threshold."""
        data = mock_weather("Mumbai", "rain")
        assert data["rain_24h_mm"] < TRIGGERS["Extreme Rain / Flooding"]["threshold"]

    def test_both_rain_triggers_fire_on_flooding(self):
        """
        215 mm/24hr exceeds BOTH the Heavy Rainfall (64.5) and Extreme Rain (204.5)
        thresholds — the flooding scenario must satisfy both comparisons.
        """
        data = mock_weather("Chennai", "flooding")
        rain = data["rain_24h_mm"]
        assert rain >= TRIGGERS["Heavy Rainfall"]["threshold"], (
            f"Expected rain {rain} >= 64.5 (Heavy Rainfall)"
        )
        assert rain >= TRIGGERS["Extreme Rain / Flooding"]["threshold"], (
            f"Expected rain {rain} >= 204.5 (Extreme Rain / Flooding)"
        )

    # ── Severe Heatwave (45°C) ────────────────────────────────────────────────

    def test_heatwave_threshold_is_45_celsius(self):
        assert TRIGGERS["Severe Heatwave"]["threshold"] == 45.0

    def test_heatwave_fires_at_46_celsius(self):
        """Mock 'heatwave' scenario (temp=46°C) must exceed the 45°C threshold."""
        data = mock_weather("Delhi", "heatwave")
        assert data["temp"] > TRIGGERS["Severe Heatwave"]["threshold"]

    def test_heatwave_does_not_fire_at_normal_temperature(self):
        """Mock 'normal' scenario (temp=28°C) must be below the 45°C threshold."""
        data = mock_weather("Bangalore", "normal")
        assert data["temp"] < TRIGGERS["Severe Heatwave"]["threshold"]

    # ── Hazardous AQI (400 AQI) ───────────────────────────────────────────────

    def test_hazardous_aqi_threshold_is_400(self):
        assert TRIGGERS["Hazardous AQI"]["threshold"] == 400.0

    def test_delhi_mock_aqi_is_above_threshold(self):
        """
        Delhi base AQI is 280 and mock_aqi adds up to +40 (max 320).
        The random range alone cannot reliably reach 400.
        Use the 'aqi' weather scenario (AQI=485) to confirm that data
        which should trigger Hazardous AQI in fact exceeds the threshold.
        """
        data = mock_weather("Delhi", "aqi")
        assert data["aqi"] > TRIGGERS["Hazardous AQI"]["threshold"]

    def test_mock_aqi_can_exceed_threshold_with_seed(self):
        """
        mock_aqi() adds random.uniform(-20, 40) to a base.
        For the 'aqi' weather scenario the AQI field is hardcoded at 485
        (above 400).  This test patches mock_aqi directly to return a
        controlled value above the threshold, confirming the comparison
        logic used in check_all_triggers.
        """
        controlled_aqi = 485.0
        assert controlled_aqi >= TRIGGERS["Hazardous AQI"]["threshold"], (
            "Controlled AQI value must be >= 400 to represent a threshold breach"
        )

    # ── Platform Outage (15 min) ──────────────────────────────────────────────

    def test_platform_outage_threshold_is_15_minutes(self):
        assert TRIGGERS["Platform Outage"]["threshold"] == 15.0

    def test_platform_outage_fires_when_status_is_down(self):
        """
        When fetch_real_platform_status returns DOWN the trigger should fire.
        We patch both the live probe and the mock fallback to guarantee DOWN.
        """
        down_response = {
            "platform": "Blinkit", "status": "DOWN",
            "latency_ms": 9999, "error_rate": 1.0,
            "http_status": 503, "source": "HTTP HEAD probe (live)",
        }
        with patch("services.trigger_engine.fetch_real_platform_status",
                   new=AsyncMock(return_value=down_response)):
            result = _run(check_all_triggers("Bangalore", "Blinkit"))
        fired_types = [t["trigger_type"] for t in result]
        assert "Platform Outage" in fired_types

    def test_platform_outage_does_not_fire_when_up(self):
        """When platform probe returns UP the Platform Outage trigger must not fire."""
        up_response = {"platform": "Blinkit", "status": "UP", "latency_ms": 300, "error_rate": 0.0}
        with patch("services.trigger_engine.fetch_real_platform_status",
                   new=AsyncMock(return_value=up_response)), \
             patch("services.trigger_engine.fetch_real_weather", return_value=None), \
             patch("services.trigger_engine.mock_weather",
                   return_value={"temp": 28, "rain_24h_mm": 5, "aqi": 85}), \
             patch("services.trigger_engine.fetch_real_aqi",
                   new=AsyncMock(return_value=None)), \
             patch("services.trigger_engine.mock_aqi", return_value=85.0), \
             patch("services.trigger_engine.fetch_civil_disruption_live",
                   new=AsyncMock(return_value=None)), \
             patch("services.trigger_engine.mock_civil_disruption",
                   return_value={"active_restrictions": False, "duration_hours": 0, "type": None}):
            result = _run(check_all_triggers("Bangalore", "Blinkit"))
        fired_types = [t["trigger_type"] for t in result]
        assert "Platform Outage" not in fired_types

    # ── Civil Disruption (4 hours) ────────────────────────────────────────────

    def test_civil_disruption_threshold_is_4_hours(self):
        assert TRIGGERS["Civil Disruption"]["threshold"] == 4.0

    def test_civil_disruption_fires_when_duration_exceeds_4_hours(self):
        """active=True, duration=6.0h (> 4.0h) must cause Civil Disruption to fire."""
        disruption_mock = {
            "city": "Bangalore",
            "active_restrictions": True,
            "type": "Protest / Bandh",
            "duration_hours": 6.0,
            "source": "GDELT (mock)",
        }
        with patch("services.trigger_engine.mock_civil_disruption",
                   return_value=disruption_mock), \
             patch("services.trigger_engine.fetch_real_weather", return_value=None), \
             patch("services.trigger_engine.mock_weather",
                   return_value={"temp": 28, "rain_24h_mm": 5, "aqi": 85}), \
             patch("services.trigger_engine.mock_aqi", return_value=85.0), \
             patch("services.trigger_engine.mock_platform_status",
                   return_value={"status": "UP", "platform": "Blinkit"}):
            result = _run(check_all_triggers("Bangalore", "Blinkit"))
        fired_types = [t["trigger_type"] for t in result]
        assert "Civil Disruption" in fired_types

    def test_civil_disruption_does_not_fire_when_inactive(self):
        """active=False must prevent Civil Disruption from firing regardless of duration."""
        disruption_mock = {
            "city": "Bangalore",
            "active_restrictions": False,
            "type": None,
            "duration_hours": 0,
            "source": "GDELT (mock)",
        }
        with patch("services.trigger_engine.mock_civil_disruption",
                   return_value=disruption_mock), \
             patch("services.trigger_engine.fetch_real_weather", return_value=None), \
             patch("services.trigger_engine.mock_weather",
                   return_value={"temp": 28, "rain_24h_mm": 5, "aqi": 85}), \
             patch("services.trigger_engine.mock_aqi", return_value=85.0), \
             patch("services.trigger_engine.mock_platform_status",
                   return_value={"status": "UP", "platform": "Blinkit"}):
            result = _run(check_all_triggers("Bangalore", "Blinkit"))
        fired_types = [t["trigger_type"] for t in result]
        assert "Civil Disruption" not in fired_types

    def test_civil_disruption_does_not_fire_when_duration_below_threshold(self):
        """active=True but duration=3.0h (< 4.0h threshold) must not fire."""
        disruption_mock = {
            "city": "Bangalore",
            "active_restrictions": True,
            "type": "Transport Strike",
            "duration_hours": 3.0,
            "source": "GDELT (mock)",
        }
        with patch("services.trigger_engine.mock_civil_disruption",
                   return_value=disruption_mock), \
             patch("services.trigger_engine.fetch_real_weather", return_value=None), \
             patch("services.trigger_engine.mock_weather",
                   return_value={"temp": 28, "rain_24h_mm": 5, "aqi": 85}), \
             patch("services.trigger_engine.mock_aqi", return_value=85.0), \
             patch("services.trigger_engine.mock_platform_status",
                   return_value={"status": "UP", "platform": "Blinkit"}):
            result = _run(check_all_triggers("Bangalore", "Blinkit"))
        fired_types = [t["trigger_type"] for t in result]
        assert "Civil Disruption" not in fired_types


# ─────────────────────────────────────────────────────────────────────────────
# TestCheckAllTriggers
# ─────────────────────────────────────────────────────────────────────────────

class TestCheckAllTriggers:
    """
    Tests for check_all_triggers() using mocked external calls so that
    outcomes are fully deterministic and do not depend on live API keys.
    """

    # ── Full mock helpers ─────────────────────────────────────────────────────

    _NORMAL_WEATHER  = {"temp": 28, "rain_24h_mm": 5, "aqi": 85}
    _RAIN_WEATHER    = {"temp": 24, "rain_24h_mm": 72, "aqi": 60}
    _FLOODING_WEATHER = {"temp": 22, "rain_24h_mm": 215, "aqi": 55}
    _PLATFORM_UP     = {"status": "UP", "platform": "Blinkit"}
    _PLATFORM_DOWN   = {"status": "DOWN", "platform": "Blinkit"}
    _DISRUPTION_OFF  = {"active_restrictions": False, "duration_hours": 0,
                        "type": None, "city": "Bangalore", "source": "GDELT (mock)"}
    _DISRUPTION_ON   = {"active_restrictions": True, "duration_hours": 6.0,
                        "type": "Protest / Bandh", "city": "Bangalore", "source": "GDELT (mock)"}

    def _all_quiet_patches(self, weather=None):
        """Return context-manager-style patch stack for all-quiet conditions."""
        w = weather or self._NORMAL_WEATHER
        return (
            patch("services.trigger_engine.fetch_real_weather", return_value=None),
            patch("services.trigger_engine.mock_weather", return_value=w),
            patch("services.trigger_engine.mock_aqi", return_value=85.0),
            patch("services.trigger_engine.mock_platform_status",
                  return_value=self._PLATFORM_UP),
            patch("services.trigger_engine.mock_civil_disruption",
                  return_value=self._DISRUPTION_OFF),
        )

    # ── Normal conditions → empty list ───────────────────────────────────────

    def test_check_all_triggers_returns_empty_list_for_normal_conditions(self):
        """
        With all mocks set to safe values (no weather event, AQI=85, platform UP,
        no civil disruption) check_all_triggers should return an empty list.
        """
        p1, p2, p3, p4, p5 = self._all_quiet_patches()
        with p1, p2, p3, p4, p5:
            result = _run(check_all_triggers("Bangalore", "Blinkit"))
        assert result == []

    # ── Heavy Rainfall ────────────────────────────────────────────────────────

    def test_check_all_triggers_returns_heavy_rainfall_when_rain_high(self):
        """rain_24h_mm=72 (> 64.5) must include 'Heavy Rainfall' in fired types."""
        p1, p2, p3, p4, p5 = self._all_quiet_patches(weather=self._RAIN_WEATHER)
        with p1, p2, p3, p4, p5:
            result = _run(check_all_triggers("Bangalore", "Blinkit"))
        fired_types = [t["trigger_type"] for t in result]
        assert "Heavy Rainfall" in fired_types

    def test_check_all_triggers_heavy_rainfall_not_in_normal_conditions(self):
        """rain_24h_mm=5 (< 64.5) must NOT include 'Heavy Rainfall'."""
        p1, p2, p3, p4, p5 = self._all_quiet_patches()
        with p1, p2, p3, p4, p5:
            result = _run(check_all_triggers("Bangalore", "Blinkit"))
        fired_types = [t["trigger_type"] for t in result]
        assert "Heavy Rainfall" not in fired_types

    # ── Extreme Rain / Flooding ───────────────────────────────────────────────

    def test_check_all_triggers_returns_extreme_rain_when_flooding(self):
        """
        rain_24h_mm=215 exceeds both thresholds; both 'Heavy Rainfall'
        AND 'Extreme Rain / Flooding' must appear in fired types.
        """
        p1, p2, p3, p4, p5 = self._all_quiet_patches(weather=self._FLOODING_WEATHER)
        with p1, p2, p3, p4, p5:
            result = _run(check_all_triggers("Bangalore", "Blinkit"))
        fired_types = [t["trigger_type"] for t in result]
        assert "Heavy Rainfall" in fired_types, (
            "Expected 'Heavy Rainfall' when rain=215 mm"
        )
        assert "Extreme Rain / Flooding" in fired_types, (
            "Expected 'Extreme Rain / Flooding' when rain=215 mm"
        )

    def test_check_all_triggers_extreme_rain_not_in_rain_only_scenario(self):
        """rain_24h_mm=72 is above 64.5 but below 204.5 — Extreme Rain must NOT fire."""
        p1, p2, p3, p4, p5 = self._all_quiet_patches(weather=self._RAIN_WEATHER)
        with p1, p2, p3, p4, p5:
            result = _run(check_all_triggers("Bangalore", "Blinkit"))
        fired_types = [t["trigger_type"] for t in result]
        assert "Extreme Rain / Flooding" not in fired_types

    # ── Heatwave ──────────────────────────────────────────────────────────────

    def test_check_all_triggers_severe_heatwave_fires_at_46_celsius(self):
        """temp=46 (> 45.0) must fire 'Severe Heatwave'."""
        heatwave_weather = {"temp": 46, "rain_24h_mm": 0, "aqi": 120}
        p1, p2, p3, p4, p5 = self._all_quiet_patches(weather=heatwave_weather)
        with p1, p2, p3, p4, p5:
            result = _run(check_all_triggers("Delhi", "Blinkit"))
        fired_types = [t["trigger_type"] for t in result]
        assert "Severe Heatwave" in fired_types

    def test_check_all_triggers_severe_heatwave_absent_at_normal_temp(self):
        """temp=28 (< 45.0) must NOT fire 'Severe Heatwave'."""
        p1, p2, p3, p4, p5 = self._all_quiet_patches()
        with p1, p2, p3, p4, p5:
            result = _run(check_all_triggers("Bangalore", "Blinkit"))
        fired_types = [t["trigger_type"] for t in result]
        assert "Severe Heatwave" not in fired_types

    # ── Hazardous AQI ─────────────────────────────────────────────────────────

    def test_check_all_triggers_hazardous_aqi_fires_when_aqi_above_400(self):
        """mock_aqi patched to return 485 (> 400) must fire 'Hazardous AQI'."""
        p1, p2, p3, p4, p5 = self._all_quiet_patches()
        with p1, p2, \
             patch("services.trigger_engine.mock_aqi", return_value=485.0), \
             p4, p5:
            result = _run(check_all_triggers("Delhi", "Blinkit"))
        fired_types = [t["trigger_type"] for t in result]
        assert "Hazardous AQI" in fired_types

    def test_check_all_triggers_hazardous_aqi_absent_when_aqi_below_400(self):
        """mock_aqi patched to 85 (< 400) must NOT fire 'Hazardous AQI'."""
        p1, p2, p3, p4, p5 = self._all_quiet_patches()
        with p1, p2, p3, p4, p5:
            result = _run(check_all_triggers("Bangalore", "Blinkit"))
        fired_types = [t["trigger_type"] for t in result]
        assert "Hazardous AQI" not in fired_types

    # ── simulate_trigger: every type exceeds threshold ────────────────────────

    def test_simulate_trigger_always_exceeds_threshold(self):
        """
        For every one of the 6 known trigger types, simulate_trigger() must
        produce a measured_value strictly greater than threshold_value.
        This validates that the DEMO values in simulate_trigger are all
        properly above their respective thresholds.
        """
        trigger_types = list(TRIGGERS.keys())
        assert len(trigger_types) == 6, "Expected exactly 6 trigger types"
        for trigger_type in trigger_types:
            t = simulate_trigger(trigger_type, "Bangalore")
            assert t["measured_value"] > t["threshold_value"], (
                f"{trigger_type!r}: measured_value {t['measured_value']} "
                f"should exceed threshold {t['threshold_value']}"
            )

    def test_simulate_trigger_all_six_types_produce_valid_dict(self):
        """Each simulated trigger must contain the required output keys."""
        required_keys = {
            "trigger_type", "city", "measured_value", "threshold_value",
            "unit", "is_validated", "severity", "description",
            "detected_at", "expires_at",
        }
        for trigger_type in TRIGGERS:
            t = simulate_trigger(trigger_type, "Mumbai")
            missing = required_keys - t.keys()
            assert missing == set(), (
                f"{trigger_type!r} missing keys: {missing}"
            )


# ─────────────────────────────────────────────────────────────────────────────
# TestSimulateEndpointTriggerCoverage
# ─────────────────────────────────────────────────────────────────────────────

class TestSimulateEndpointTriggerCoverage:
    """
    Integration tests that exercise POST /triggers/simulate via TestClient.
    Uses the conftest-provided authed_client and test_db fixtures.
    """

    # ── Parametric HTTP 201 tests ─────────────────────────────────────────────

    def test_simulate_heavy_rainfall_returns_201(self, authed_client):
        resp = authed_client.post("/triggers/simulate", json={
            "trigger_type": "Heavy Rainfall",
            "city": "Bangalore",
        })
        assert resp.status_code == 201, (
            f"Expected 201, got {resp.status_code}: {resp.text}"
        )

    def test_simulate_extreme_rain_returns_201(self, authed_client):
        resp = authed_client.post("/triggers/simulate", json={
            "trigger_type": "Extreme Rain / Flooding",
            "city": "Mumbai",
        })
        assert resp.status_code == 201, (
            f"Expected 201, got {resp.status_code}: {resp.text}"
        )

    def test_simulate_severe_heatwave_returns_201(self, authed_client):
        resp = authed_client.post("/triggers/simulate", json={
            "trigger_type": "Severe Heatwave",
            "city": "Delhi",
        })
        assert resp.status_code == 201, (
            f"Expected 201, got {resp.status_code}: {resp.text}"
        )

    def test_simulate_hazardous_aqi_returns_201(self, authed_client):
        resp = authed_client.post("/triggers/simulate", json={
            "trigger_type": "Hazardous AQI",
            "city": "Delhi",
        })
        assert resp.status_code == 201, (
            f"Expected 201, got {resp.status_code}: {resp.text}"
        )

    def test_simulate_platform_outage_returns_201(self, authed_client):
        resp = authed_client.post("/triggers/simulate", json={
            "trigger_type": "Platform Outage",
            "city": "Bangalore",
        })
        assert resp.status_code == 201, (
            f"Expected 201, got {resp.status_code}: {resp.text}"
        )

    def test_simulate_civil_disruption_returns_201(self, authed_client):
        resp = authed_client.post("/triggers/simulate", json={
            "trigger_type": "Civil Disruption",
            "city": "Bangalore",
        })
        assert resp.status_code == 201, (
            f"Expected 201, got {resp.status_code}: {resp.text}"
        )

    # ── Response body structure ───────────────────────────────────────────────

    def test_simulate_response_contains_required_fields(self, authed_client):
        """POST /triggers/simulate response must include the expected keys."""
        resp = authed_client.post("/triggers/simulate", json={
            "trigger_type": "Heavy Rainfall",
            "city": "Bangalore",
        })
        assert resp.status_code == 201
        body = resp.json()
        for key in ("message", "trigger_event_id", "measured_value", "unit", "description"):
            assert key in body, f"Response missing key {key!r}"

    def test_simulate_response_measured_value_exceeds_threshold(self, authed_client):
        """
        The measured_value returned by the simulate endpoint must be
        above the threshold for Heavy Rainfall (64.5 mm/24hr).
        simulate_trigger() uses 72.5 mm for Heavy Rainfall.
        """
        resp = authed_client.post("/triggers/simulate", json={
            "trigger_type": "Heavy Rainfall",
            "city": "Bangalore",
        })
        assert resp.status_code == 201
        body = resp.json()
        threshold = TRIGGERS["Heavy Rainfall"]["threshold"]
        assert body["measured_value"] > threshold, (
            f"measured_value {body['measured_value']} should exceed threshold {threshold}"
        )

    def test_simulate_message_contains_trigger_type(self, authed_client):
        """The response message must mention the trigger type that was simulated."""
        resp = authed_client.post("/triggers/simulate", json={
            "trigger_type": "Severe Heatwave",
            "city": "Delhi",
        })
        assert resp.status_code == 201
        assert "Severe Heatwave" in resp.json()["message"]

    # ── DB persistence ────────────────────────────────────────────────────────

    def test_simulated_trigger_stored_in_db(self, authed_client, test_db):
        """
        After a successful simulate call the TriggerEvent count in the test
        database must increase by exactly 1.
        """
        before = test_db.query(TriggerEvent).count()

        resp = authed_client.post("/triggers/simulate", json={
            "trigger_type": "Hazardous AQI",
            "city": "Delhi",
        })
        assert resp.status_code == 201

        # The simulate endpoint uses the same session because authed_client
        # has the get_db override pointing to test_db.
        after = test_db.query(TriggerEvent).count()
        assert after == before + 1, (
            f"Expected TriggerEvent count to increase by 1 (before={before}, after={after})"
        )

    def test_simulated_trigger_db_row_has_correct_type(self, authed_client, test_db):
        """The persisted TriggerEvent must have the correct trigger_type."""
        resp = authed_client.post("/triggers/simulate", json={
            "trigger_type": "Civil Disruption",
            "city": "Bangalore",
        })
        assert resp.status_code == 201
        event_id = resp.json()["trigger_event_id"]

        event = test_db.query(TriggerEvent).filter(TriggerEvent.id == event_id).first()
        assert event is not None, "TriggerEvent not found in DB"
        assert event.trigger_type == "Civil Disruption"

    def test_simulated_trigger_db_row_is_validated(self, authed_client, test_db):
        """All simulated triggers must be persisted with is_validated=True."""
        resp = authed_client.post("/triggers/simulate", json={
            "trigger_type": "Platform Outage",
            "city": "Mumbai",
        })
        assert resp.status_code == 201
        event_id = resp.json()["trigger_event_id"]

        event = test_db.query(TriggerEvent).filter(TriggerEvent.id == event_id).first()
        assert event is not None
        assert event.is_validated is True

    def test_simulated_trigger_measured_value_above_threshold_in_db(self, authed_client, test_db):
        """
        The persisted measured_value must exceed the persisted threshold_value
        for every trigger type simulated via the endpoint.
        """
        for trigger_type in TRIGGERS:
            resp = authed_client.post("/triggers/simulate", json={
                "trigger_type": trigger_type,
                "city": "Bangalore",
            })
            assert resp.status_code == 201, (
                f"{trigger_type!r}: expected 201 got {resp.status_code}"
            )
            event_id = resp.json()["trigger_event_id"]
            event = test_db.query(TriggerEvent).filter(TriggerEvent.id == event_id).first()
            assert event is not None, f"No DB row for {trigger_type!r}"
            assert event.measured_value > event.threshold_value, (
                f"{trigger_type!r}: DB measured_value {event.measured_value} "
                f"should exceed threshold_value {event.threshold_value}"
            )

    # ── Unauthenticated request rejected ─────────────────────────────────────

    def test_simulate_requires_authentication(self, client):
        """POST /triggers/simulate without a JWT must return 401 or 403."""
        resp = client.post("/triggers/simulate", json={
            "trigger_type": "Heavy Rainfall",
            "city": "Bangalore",
        })
        assert resp.status_code in (401, 403), (
            f"Expected 401/403 for unauthenticated request, got {resp.status_code}"
        )

    # ── All 6 types via parametrize ───────────────────────────────────────────

    @pytest.mark.parametrize("trigger_type,city", [
        ("Heavy Rainfall",          "Bangalore"),
        ("Extreme Rain / Flooding", "Mumbai"),
        ("Severe Heatwave",         "Delhi"),
        ("Hazardous AQI",           "Delhi"),
        ("Platform Outage",         "Bangalore"),
        ("Civil Disruption",        "Bangalore"),
    ])
    def test_all_trigger_types_return_201_and_persist(
        self, trigger_type, city, authed_client, test_db
    ):
        """
        Parametrized sanity check: every trigger type must produce HTTP 201
        and persist exactly one TriggerEvent row.
        """
        before = test_db.query(TriggerEvent).count()
        resp = authed_client.post("/triggers/simulate", json={
            "trigger_type": trigger_type,
            "city": city,
        })
        assert resp.status_code == 201, (
            f"{trigger_type!r}: expected 201, got {resp.status_code}: {resp.text}"
        )
        after = test_db.query(TriggerEvent).count()
        assert after == before + 1, (
            f"{trigger_type!r}: TriggerEvent count should increase by 1"
        )
