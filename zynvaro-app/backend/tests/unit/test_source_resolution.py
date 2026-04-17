"""
backend/tests/unit/test_source_resolution.py
=============================================
Table-driven unit tests for resolve_authoritative_source().
Covers:
  - A. Source availability matrix
  - B. All invalidity reasons that trigger fallback
  - C. Conflict / disagreement scenarios
  - D. Trigger-specific hierarchy rules (civil, outage)
  - E. Freshness / SLA boundaries
  - F. Snapshot field presence
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

from services.source_hierarchy import (
    REASON_BOTH_SOURCES_INVALID,
    REASON_OFFICIAL_MALFORMED_FALLBACK_USED,
    REASON_OFFICIAL_REQUIRED_FOR_TRIGGER,
    REASON_OFFICIAL_STALE_FALLBACK_USED,
    REASON_OFFICIAL_TIMEOUT_FALLBACK_USED,
    REASON_OFFICIAL_VALID_SELECTED,
    REASON_SOURCE_CONFLICT_UNDER_REVIEW,
    REASON_SOURCE_CONFIG_UNKNOWN,
    resolve_authoritative_source,
)

# ── Deterministic "now" used throughout ───────────────────────────────
_NOW = datetime(2026, 4, 17, 7, 0, 0)  # UTC, noon IST
_CTX = {"as_of": _NOW}


# ── Payload factories ─────────────────────────────────────────────────

def _weather_official(
    *,
    measured_value: float = 82.0,
    threshold_value: float = 64.5,
    unit: str = "mm/24hr",
    event_offset_seconds: int = -300,   # 5 min ago — fresh
    stale: bool = False,
    timeout: bool = False,
    malformed: bool = False,
    missing_field: str | None = None,
    impossible_value: bool = False,
    bad_signature: bool = False,
    status_code: int | None = None,
    geocode_precision: str = "district",
    source_name: str = "IMD district weather feed",
    record_id: str = "official-001",
) -> dict:
    if stale:
        event_offset_seconds = -(16 * 60)   # 16 min > 15 min SLA
    payload: dict = {
        "source_type": "official",
        "source_name": source_name,
        "record_id": record_id,
        "measured_value": measured_value,
        "measured_unit": unit,
        "threshold_value": threshold_value,
        "event_time": _NOW + timedelta(seconds=event_offset_seconds),
        "fetch_time": _NOW - timedelta(seconds=30),
        "geocode_precision": geocode_precision,
        "priority": 0,
    }
    if timeout:
        payload = {"source_type": "official", "source_name": source_name, "timeout": True}
    elif status_code is not None:
        payload["status_code"] = status_code
        payload["timeout"] = False
    elif malformed:
        payload["malformed"] = True
    elif missing_field:
        payload.pop(missing_field, None)
        payload["required_fields"] = ["measured_value", "measured_unit", "event_time", missing_field]
    elif impossible_value:
        payload["impossible_value"] = True
        payload["measured_value"] = -99.9
    elif bad_signature:
        payload["signature_valid"] = False
    return payload


def _weather_fallback(
    *,
    measured_value: float = 80.0,
    threshold_value: float = 64.5,
    unit: str = "mm/24hr",
    event_offset_seconds: int = -120,
    stale: bool = False,
    timeout: bool = False,
    malformed: bool = False,
    geocode_precision: str = "city",
    source_name: str = "OpenWeatherMap continuity feed",
    record_id: str = "fallback-001",
) -> dict:
    if stale:
        event_offset_seconds = -(16 * 60)
    payload: dict = {
        "source_type": "fallback",
        "source_name": source_name,
        "record_id": record_id,
        "measured_value": measured_value,
        "measured_unit": unit,
        "threshold_value": threshold_value,
        "event_time": _NOW + timedelta(seconds=event_offset_seconds),
        "fetch_time": _NOW - timedelta(seconds=10),
        "geocode_precision": geocode_precision,
        "priority": 10,
    }
    if timeout:
        payload = {"source_type": "fallback", "source_name": source_name, "timeout": True}
    elif malformed:
        payload["malformed"] = True
    return payload


# ─────────────────────────────────────────────────────────────────────
# A — Source Availability Matrix
# ─────────────────────────────────────────────────────────────────────

def test_resolver_prefers_official_source_when_both_valid():
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official()],
        fallback_payloads=[_weather_fallback()],
        event_context=_CTX,
    )
    assert result.authoritative_source_type == "official"
    assert result.fallback_used is False
    assert result.resolution_reason_code == REASON_OFFICIAL_VALID_SELECTED


def test_resolver_uses_fallback_when_official_is_stale():
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official(stale=True)],
        fallback_payloads=[_weather_fallback()],
        event_context=_CTX,
    )
    assert result.authoritative_source_type == "fallback"
    assert result.fallback_used is True
    assert result.freshness_status == "fresh"
    assert result.resolution_reason_code == REASON_OFFICIAL_STALE_FALLBACK_USED


def test_resolver_uses_fallback_when_official_times_out():
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official(timeout=True)],
        fallback_payloads=[_weather_fallback()],
        event_context=_CTX,
    )
    assert result.fallback_used is True
    assert result.resolution_reason_code == REASON_OFFICIAL_TIMEOUT_FALLBACK_USED


def test_resolver_uses_fallback_when_official_is_malformed():
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official(malformed=True)],
        fallback_payloads=[_weather_fallback()],
        event_context=_CTX,
    )
    assert result.fallback_used is True
    assert result.resolution_reason_code == REASON_OFFICIAL_MALFORMED_FALLBACK_USED


def test_resolver_uses_fallback_when_official_has_missing_field():
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official(missing_field="location")],
        fallback_payloads=[_weather_fallback()],
        event_context=_CTX,
    )
    assert result.fallback_used is True
    assert result.resolution_reason_code == REASON_OFFICIAL_MALFORMED_FALLBACK_USED


def test_resolver_uses_fallback_when_official_has_impossible_value():
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official(impossible_value=True)],
        fallback_payloads=[_weather_fallback()],
        event_context=_CTX,
    )
    assert result.fallback_used is True


def test_resolver_uses_fallback_when_official_has_bad_signature():
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official(bad_signature=True)],
        fallback_payloads=[_weather_fallback()],
        event_context=_CTX,
    )
    assert result.fallback_used is True
    assert result.resolution_reason_code == REASON_OFFICIAL_MALFORMED_FALLBACK_USED


def test_resolver_uses_fallback_when_official_has_5xx_status():
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official(status_code=503)],
        fallback_payloads=[_weather_fallback()],
        event_context=_CTX,
    )
    assert result.fallback_used is True


def test_resolver_marks_both_invalid_when_all_sources_fail():
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official(timeout=True)],
        fallback_payloads=[_weather_fallback(timeout=True)],
        event_context=_CTX,
    )
    assert result.authoritative_source_type is None
    assert result.resolution_reason_code == REASON_BOTH_SOURCES_INVALID


def test_resolver_both_invalid_when_no_sources_provided():
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[],
        fallback_payloads=[],
        event_context=_CTX,
    )
    assert result.authoritative_source_type is None
    assert result.resolution_reason_code == REASON_BOTH_SOURCES_INVALID


def test_resolver_unknown_trigger_type_returns_config_unknown():
    result = resolve_authoritative_source(
        "Unknown Trigger XYZ",
        official_payloads=[_weather_official()],
        fallback_payloads=[],
        event_context=_CTX,
    )
    assert result.resolution_reason_code == REASON_SOURCE_CONFIG_UNKNOWN


def test_resolver_official_wins_even_when_fallback_has_higher_value():
    """Fallback cannot override official just because it reports a larger measurement."""
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official(measured_value=66.0)],
        fallback_payloads=[_weather_fallback(measured_value=99.0)],
        event_context=_CTX,
    )
    assert result.authoritative_source_type == "official"
    assert result.measured_value == pytest.approx(66.0)


# ─────────────────────────────────────────────────────────────────────
# B — Invalidity reasons produce correct fallback_reason codes
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("make_official", "expected_resolution_code"),
    [
        (lambda: _weather_official(stale=True),          REASON_OFFICIAL_STALE_FALLBACK_USED),
        (lambda: _weather_official(timeout=True),         REASON_OFFICIAL_TIMEOUT_FALLBACK_USED),
        (lambda: _weather_official(malformed=True),       REASON_OFFICIAL_MALFORMED_FALLBACK_USED),
        (lambda: _weather_official(missing_field="location"), REASON_OFFICIAL_MALFORMED_FALLBACK_USED),
        (lambda: _weather_official(bad_signature=True),   REASON_OFFICIAL_MALFORMED_FALLBACK_USED),
    ],
    ids=["stale", "timeout", "malformed", "missing_field", "bad_signature"],
)
def test_resolver_invalidity_reason_codes(make_official, expected_resolution_code):
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[make_official()],
        fallback_payloads=[_weather_fallback()],
        event_context=_CTX,
    )
    assert result.resolution_reason_code == expected_resolution_code


# ─────────────────────────────────────────────────────────────────────
# C — Disagreement / conflict scenarios
# ─────────────────────────────────────────────────────────────────────

def test_resolver_source_conflict_when_threshold_crossed_disagree():
    """Official says threshold crossed; fallback says not crossed → conflict."""
    official = _weather_official(measured_value=70.0, threshold_value=64.5)
    fallback = _weather_fallback(measured_value=60.0, threshold_value=64.5)
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[official],
        fallback_payloads=[fallback],
        event_context=_CTX,
    )
    assert result.agreement_status in {"threshold_conflict", "conflict"}
    assert result.resolution_reason_code == REASON_SOURCE_CONFLICT_UNDER_REVIEW
    assert result.provisional_status is True


def test_resolver_agree_when_values_within_tolerance():
    official = _weather_official(measured_value=75.0)
    fallback = _weather_fallback(measured_value=76.5)  # diff ≈ 2%, well within 12%
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[official],
        fallback_payloads=[fallback],
        event_context=_CTX,
    )
    assert result.agreement_status == "agree"
    assert result.resolution_reason_code == REASON_OFFICIAL_VALID_SELECTED


def test_resolver_conflict_when_values_beyond_tolerance():
    """75 vs 52 → threshold_conflict fires first since threshold_crossed values differ (True vs False)."""
    official = _weather_official(measured_value=75.0)
    fallback = _weather_fallback(measured_value=52.0)
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[official],
        fallback_payloads=[fallback],
        event_context=_CTX,
    )
    # 75 crosses 64.5 threshold; 52 does not → threshold_conflict is the correct status
    assert result.agreement_status in {"conflict", "threshold_conflict"}
    assert result.resolution_reason_code == REASON_SOURCE_CONFLICT_UNDER_REVIEW


def test_resolver_no_corroborator_when_fallback_invalid():
    """Both sources present but fallback invalid → no_corroborator, official wins cleanly."""
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official()],
        fallback_payloads=[_weather_fallback(timeout=True)],
        event_context=_CTX,
    )
    assert result.agreement_status == "no_corroborator"
    assert result.authoritative_source_type == "official"


def test_resolver_no_corroborator_when_no_fallback_provided():
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official()],
        fallback_payloads=[],
        event_context=_CTX,
    )
    assert result.agreement_status == "no_corroborator"


# ─────────────────────────────────────────────────────────────────────
# D — Trigger-specific hierarchy rules
# ─────────────────────────────────────────────────────────────────────

def _civil_official(
    *,
    event_offset_seconds: int = -300,
    timeout: bool = False,
    source_name: str = "District Magistrate Order",
) -> dict:
    payload: dict = {
        "source_type": "official",
        "source_name": source_name,
        "record_id": "civil-official-001",
        "measured_value": 6.0,
        "measured_unit": "hours restricted",
        "threshold_value": 4.0,
        "event_time": _NOW + timedelta(seconds=event_offset_seconds),
        "fetch_time": _NOW - timedelta(seconds=30),
        "geocode_precision": "district",
        "priority": 0,
    }
    if timeout:
        payload = {"source_type": "official", "source_name": source_name, "timeout": True}
    return payload


def _civil_fallback(source_name: str = "GDELT news signal") -> dict:
    return {
        "source_type": "fallback",
        "source_name": source_name,
        "record_id": "civil-fallback-001",
        "measured_value": 6.0,
        "measured_unit": "hours restricted",
        "threshold_value": 4.0,
        "event_time": _NOW - timedelta(minutes=5),
        "fetch_time": _NOW - timedelta(seconds=10),
        "geocode_precision": "city",
        "priority": 10,
    }


def test_resolver_civil_disruption_with_official_source_succeeds():
    result = resolve_authoritative_source(
        "Civil Disruption",
        official_payloads=[_civil_official()],
        fallback_payloads=[_civil_fallback()],
        event_context=_CTX,
    )
    assert result.authoritative_source_type == "official"


def test_resolver_civil_disruption_without_official_selects_fallback():
    """Civil disruption falls back to news signal when official unavailable."""
    result = resolve_authoritative_source(
        "Civil Disruption",
        official_payloads=[_civil_official(timeout=True)],
        fallback_payloads=[_civil_fallback()],
        event_context=_CTX,
    )
    # Resolver selects fallback (settlement gating blocks it, but resolver picks best available)
    assert result.fallback_used is True


def _outage_official_telemetry(
    *,
    event_offset_seconds: int = -30,
    timeout: bool = False,
) -> dict:
    payload: dict = {
        "source_type": "official",
        "source_name": "Blinkit partner heartbeat feed",
        "record_id": "outage-official-001",
        "measured_value": 20.0,
        "measured_unit": "minutes down",
        "threshold_value": 15.0,
        "event_time": _NOW + timedelta(seconds=event_offset_seconds),
        "fetch_time": _NOW - timedelta(seconds=5),
        "geocode_precision": "zone",
        "priority": 0,
    }
    if timeout:
        payload = {"source_type": "official", "source_name": "Blinkit partner heartbeat feed", "timeout": True}
    return payload


def _outage_fallback_probe(*, event_offset_seconds: int = -30) -> dict:
    return {
        "source_type": "fallback",
        "source_name": "Synthetic probe (DownDetector)",
        "record_id": "outage-fallback-001",
        "measured_value": 20.0,
        "measured_unit": "minutes down",
        "threshold_value": 15.0,
        "event_time": _NOW + timedelta(seconds=event_offset_seconds),
        "fetch_time": _NOW - timedelta(seconds=5),
        "geocode_precision": "city",
        "priority": 10,
    }


def test_resolver_platform_outage_with_official_telemetry():
    result = resolve_authoritative_source(
        "Platform Outage",
        official_payloads=[_outage_official_telemetry()],
        fallback_payloads=[_outage_fallback_probe()],
        event_context=_CTX,
    )
    assert result.authoritative_source_type == "official"
    assert result.resolution_reason_code == REASON_OFFICIAL_VALID_SELECTED


def test_resolver_platform_outage_synthetic_only_selects_fallback():
    """No telemetry → resolver still picks synthetic probe as best available."""
    result = resolve_authoritative_source(
        "Platform Outage",
        official_payloads=[_outage_official_telemetry(timeout=True)],
        fallback_payloads=[_outage_fallback_probe()],
        event_context=_CTX,
    )
    assert result.fallback_used is True
    # Settlement gating (separate function) blocks this — resolver just resolves
    assert result.authoritative_source_type == "fallback"


# ─────────────────────────────────────────────────────────────────────
# E — Freshness / SLA boundary conditions
# ─────────────────────────────────────────────────────────────────────

def test_resolver_rejects_official_source_just_outside_freshness_sla():
    """Event time is 901 seconds (15 min + 1 sec) old for a 15-min SLA → stale."""
    official = _weather_official(event_offset_seconds=-901)
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[official],
        fallback_payloads=[_weather_fallback()],
        event_context=_CTX,
    )
    assert result.fallback_used is True
    assert result.resolution_reason_code == REASON_OFFICIAL_STALE_FALLBACK_USED


def test_resolver_accepts_official_source_just_inside_freshness_sla():
    """Event time is 899 seconds old for a 15-min SLA → still fresh."""
    official = _weather_official(event_offset_seconds=-899)
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[official],
        fallback_payloads=[],
        event_context=_CTX,
    )
    assert result.authoritative_source_type == "official"
    assert result.fallback_used is False


def test_resolver_accepts_source_exactly_on_freshness_sla_boundary():
    """Event time is exactly 900 seconds from now → valid (boundary inclusive)."""
    official = _weather_official(event_offset_seconds=-900)
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[official],
        fallback_payloads=[],
        event_context=_CTX,
    )
    assert result.authoritative_source_type == "official"


def test_resolver_rejects_source_with_future_timestamp():
    """Source timestamp is 10 minutes in future → future_skew → invalid."""
    official = _weather_official(event_offset_seconds=610)  # 10 min 10 sec future > 5 min skew
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[official],
        fallback_payloads=[_weather_fallback()],
        event_context=_CTX,
    )
    # Future-skewed official → fallback
    assert result.fallback_used is True


def test_resolver_handles_utc_timezone_aware_event_time():
    """Timezone-aware UTC datetime is correctly normalized and passes freshness check."""
    payload = {
        "source_type": "official",
        "source_name": "IMD",
        "record_id": "tz-001",
        "measured_value": 80.0,
        "measured_unit": "mm/24hr",
        "threshold_value": 64.5,
        "event_time": _NOW.replace(tzinfo=timezone.utc) - timedelta(minutes=3),
        "fetch_time": _NOW - timedelta(seconds=10),
        "geocode_precision": "district",
        "priority": 0,
    }
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[payload],
        fallback_payloads=[],
        event_context=_CTX,
    )
    assert result.authoritative_source_type == "official"


def test_resolver_both_stale_returns_no_authoritative():
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official(stale=True)],
        fallback_payloads=[_weather_fallback(stale=True)],
        event_context=_CTX,
    )
    assert result.authoritative_source_type is None
    assert result.resolution_reason_code == REASON_BOTH_SOURCES_INVALID


# ─────────────────────────────────────────────────────────────────────
# F — Result field presence / snapshot completeness
# ─────────────────────────────────────────────────────────────────────

def test_resolver_result_has_all_required_fields_when_official_valid():
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official()],
        fallback_payloads=[_weather_fallback()],
        event_context=_CTX,
    )
    assert result.trigger_type == "Heavy Rainfall"
    assert result.authoritative_source_name is not None
    assert result.authoritative_source_record_id is not None
    assert result.source_event_time is not None
    assert result.freshness_seconds is not None
    assert result.freshness_status == "fresh"
    assert result.data_quality_status is not None
    assert result.measured_value is not None
    assert result.measured_unit == "mm/24hr"
    assert result.threshold_crossed is True   # 82.0 >= 64.5
    assert result.source_config_version is not None
    assert isinstance(result.source_chain, list)
    assert len(result.source_chain) >= 1


def test_resolver_invalid_result_records_all_invalid_sources():
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official(timeout=True)],
        fallback_payloads=[_weather_fallback(timeout=True)],
        event_context=_CTX,
    )
    assert len(result.invalid_sources) == 2
    quality_statuses = {s["quality_status"] for s in result.invalid_sources}
    assert "timeout" in quality_statuses


def test_resolver_deterministic_for_duplicate_official_entries():
    """Two identical official entries → deterministic winner, no crash."""
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official(record_id="A"), _weather_official(record_id="B")],
        fallback_payloads=[],
        event_context=_CTX,
    )
    assert result.authoritative_source_type == "official"
    assert result.authoritative_source_record_id in {"A", "B"}


def test_resolver_unsupported_unit_triggers_fallback():
    """Official source returns wrong unit (e.g. 'inches') → invalid → fallback."""
    official = _weather_official(unit="inches")  # not in allowed_units for Heavy Rainfall
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[official],
        fallback_payloads=[_weather_fallback()],
        event_context=_CTX,
    )
    assert result.fallback_used is True


def test_resolver_marks_provisional_when_fallback_used_for_rainfall():
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official(timeout=True)],
        fallback_payloads=[_weather_fallback()],
        event_context=_CTX,
    )
    assert result.provisional_status is True


def test_resolver_not_provisional_when_official_valid_and_agrees():
    result = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_weather_official()],
        fallback_payloads=[_weather_fallback(measured_value=83.0)],  # agrees
        event_context=_CTX,
    )
    assert result.provisional_status is False


# ─────────────────────────────────────────────────────────────────────
# AQI threshold edge cases
# ─────────────────────────────────────────────────────────────────────

def _aqi_official(measured: float, threshold: float = 300.0) -> dict:
    return {
        "source_type": "official",
        "source_name": "CPCB SAMEER station",
        "record_id": "aqi-001",
        "measured_value": measured,
        "measured_unit": "AQI",
        "threshold_value": threshold,
        "event_time": _NOW - timedelta(minutes=5),
        "fetch_time": _NOW - timedelta(seconds=10),
        "geocode_precision": "station",
        "priority": 0,
    }


@pytest.mark.parametrize(
    ("measured", "threshold", "expected_crossed"),
    [
        (300.0, 300.0, True),   # exactly on boundary → met
        (299.9, 300.0, False),  # just below
        (300.1, 300.0, True),   # just above
        (401.0, 400.0, True),   # hazardous tier 2 above
        (400.0, 400.0, True),   # hazardous tier 2 at boundary
        (399.9, 400.0, False),  # just below tier 2
    ],
)
def test_resolver_aqi_threshold_boundary_table(measured, threshold, expected_crossed):
    result = resolve_authoritative_source(
        "Hazardous AQI",
        official_payloads=[_aqi_official(measured=measured, threshold=threshold)],
        fallback_payloads=[],
        event_context=_CTX,
    )
    assert result.threshold_crossed is expected_crossed
