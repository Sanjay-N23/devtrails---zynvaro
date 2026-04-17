"""
backend/tests/unit/test_source_confidence.py
=============================================
Table-driven unit tests for compute_source_confidence().
Covers:
  - Authority score by source type
  - Freshness score contribution
  - Agreement / conflict contribution
  - Geocode precision contribution
  - Completeness contribution
  - Confidence band boundaries (high/medium/low/weak)
  - Penalty deductions (provisional, fallback, disagreement)
  - Safe handling of no-source / zero-score scenarios
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from services.source_hierarchy import (
    REASON_BOTH_SOURCES_INVALID,
    REASON_LOW_CONFIDENCE_NO_AUTO_SETTLEMENT,
    REASON_OFFICIAL_VALID_SELECTED,
    ConfidenceResult,
    SourceResolutionResult,
    compute_source_confidence,
    resolve_authoritative_source,
)

_NOW = datetime(2026, 4, 17, 7, 0, 0)
_CTX = {"as_of": _NOW}


# ── Helper to build a minimal SourceResolutionResult directly ─────────
def _make_result(
    *,
    trigger_type: str = "Heavy Rainfall",
    authoritative_source_type: str | None = "official",
    fallback_used: bool = False,
    freshness_status: str = "fresh",
    agreement_status: str = "agree",
    disagreement_magnitude: float | None = None,
    geocode_precision: str = "district",
    measured_value: float | None = 82.0,
    measured_unit: str | None = "mm/24hr",
    threshold_value: float | None = 64.5,
    provisional_status: bool = False,
    resolution_reason_code: str = REASON_OFFICIAL_VALID_SELECTED,
) -> SourceResolutionResult:
    return SourceResolutionResult(
        trigger_type=trigger_type,
        authoritative_source_type=authoritative_source_type,
        fallback_used=fallback_used,
        freshness_status=freshness_status,
        agreement_status=agreement_status,
        disagreement_magnitude=disagreement_magnitude,
        geocode_precision=geocode_precision,
        measured_value=measured_value,
        measured_unit=measured_unit,
        threshold_value=threshold_value,
        provisional_status=provisional_status,
        resolution_reason_code=resolution_reason_code,
    )


# ─────────────────────────────────────────────────────────────────────
# Authority score variations
# ─────────────────────────────────────────────────────────────────────

def test_confidence_official_source_has_highest_authority_score():
    r = _make_result(authoritative_source_type="official")
    result = compute_source_confidence(r)
    # official = 40, fresh = 25, agree = 20, district = 7, complete = 5 → 97 → capped at 100
    assert result.authority_score == pytest.approx(40.0)
    assert result.confidence_band == "high"


def test_confidence_fallback_source_has_lower_authority_score():
    r = _make_result(
        authoritative_source_type="fallback",
        fallback_used=True,
        provisional_status=True,
    )
    result = compute_source_confidence(r)
    assert result.authority_score == pytest.approx(12.0)
    assert result.confidence_band in {"medium", "low", "weak"}


def test_confidence_no_authoritative_source_returns_zero():
    r = _make_result(
        authoritative_source_type=None,
        freshness_status="invalid",
        resolution_reason_code=REASON_BOTH_SOURCES_INVALID,
    )
    result = compute_source_confidence(r)
    assert result.confidence_score == pytest.approx(0.0)
    assert result.confidence_band == "weak"
    assert result.gating_reason == REASON_BOTH_SOURCES_INVALID


# ─────────────────────────────────────────────────────────────────────
# Freshness score
# ─────────────────────────────────────────────────────────────────────

def test_confidence_fresh_source_scores_25_freshness():
    r = _make_result(freshness_status="fresh")
    result = compute_source_confidence(r)
    assert result.freshness_score == pytest.approx(25.0)


def test_confidence_stale_source_scores_10_freshness():
    r = _make_result(freshness_status="stale")
    result = compute_source_confidence(r)
    assert result.freshness_score == pytest.approx(10.0)


def test_confidence_invalid_freshness_collapses_score_to_zero():
    r = _make_result(freshness_status="timeout", authoritative_source_type="official")
    result = compute_source_confidence(r)
    assert result.confidence_score == pytest.approx(0.0)


# ─────────────────────────────────────────────────────────────────────
# Agreement score
# ─────────────────────────────────────────────────────────────────────

def test_confidence_agreement_score_for_concurring_sources():
    r = _make_result(agreement_status="agree")
    result = compute_source_confidence(r)
    assert result.agreement_score == pytest.approx(20.0)


def test_confidence_agreement_score_drops_for_no_corroborator():
    r = _make_result(agreement_status="no_corroborator")
    result = compute_source_confidence(r)
    assert result.agreement_score == pytest.approx(12.0)


def test_confidence_agreement_score_drops_for_threshold_conflict():
    r = _make_result(agreement_status="threshold_conflict", provisional_status=True)
    result = compute_source_confidence(r)
    assert result.agreement_score == pytest.approx(4.0)


def test_confidence_drops_on_material_source_disagreement():
    """Disagreement magnitude beyond tolerance → extra -10 penalty applied, score is medium not high."""
    r = _make_result(
        agreement_status="conflict",
        disagreement_magnitude=0.35,   # > 0.12 tolerance for Heavy Rainfall
        provisional_status=True,
    )
    result = compute_source_confidence(r)
    # official(40) + fresh(25) + conflict(4) + district(7) + complete(5) - provisional(8) - disagree(10) = 63
    # This is medium band, which is correct — lower than the 80+ it would be without conflict
    assert result.confidence_score < 80.0
    assert result.confidence_band in {"medium", "low", "weak"}


# ─────────────────────────────────────────────────────────────────────
# Geocode precision
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("precision", "expected_score"),
    [
        ("station", 10.0),
        ("grid",    9.0),
        ("zone",    9.0),
        ("district", 7.0),
        ("city",    5.0),
        ("region",  3.0),
        ("unknown", 1.0),
    ],
)
def test_confidence_drops_for_coarse_geocode_precision(precision, expected_score):
    r = _make_result(geocode_precision=precision)
    result = compute_source_confidence(r)
    assert result.precision_score == pytest.approx(expected_score)


# ─────────────────────────────────────────────────────────────────────
# Completeness deductions
# ─────────────────────────────────────────────────────────────────────

def test_confidence_completeness_drops_when_measured_value_missing():
    r = _make_result(measured_value=None)
    result = compute_source_confidence(r)
    assert result.completeness_score == pytest.approx(0.0)


def test_confidence_completeness_drops_when_threshold_missing():
    r = _make_result(threshold_value=None)
    result = compute_source_confidence(r)
    assert result.completeness_score == pytest.approx(2.0)


def test_confidence_full_completeness_when_all_fields_present():
    r = _make_result()
    result = compute_source_confidence(r)
    assert result.completeness_score == pytest.approx(5.0)


# ─────────────────────────────────────────────────────────────────────
# Penalty: provisional + fallback
# ─────────────────────────────────────────────────────────────────────

def test_confidence_provisional_flag_applies_minus_8_penalty():
    r_base = _make_result(provisional_status=False, agreement_status="no_corroborator")
    r_prov = _make_result(provisional_status=True, agreement_status="no_corroborator")
    base = compute_source_confidence(r_base).confidence_score
    prov = compute_source_confidence(r_prov).confidence_score
    assert base - prov == pytest.approx(8.0, abs=0.5)


def test_confidence_fallback_used_applies_minus_6_penalty():
    r_base = _make_result(fallback_used=False, authoritative_source_type="fallback", provisional_status=True)
    r_fb = _make_result(fallback_used=True, authoritative_source_type="fallback", provisional_status=True)
    base = compute_source_confidence(r_base).confidence_score
    fb = compute_source_confidence(r_fb).confidence_score
    assert base - fb == pytest.approx(6.0, abs=0.5)


# ─────────────────────────────────────────────────────────────────────
# Band boundary conditions
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("score_factors", "expected_band"),
    [
        # Force high: official, fresh, agree, station → 40+25+20+10+5 = 100 → high
        ({"geocode_precision": "station"}, "high"),
        # Force medium: fallback(12) + fresh(25) + no_corroborator(12) + city(5) + complete(5) = 59
        # minus fallback penalty(6) = 53 → low. Use secondary to avoid fallback deduction:
        # official(40) + stale(10) + no_corroborator(12) + city(5) + complete(5) = 72 - provisional(8) = 64 → medium
        ({"authoritative_source_type": "official",
          "freshness_status": "stale",
          "agreement_status": "no_corroborator",
          "geocode_precision": "city",
          "provisional_status": True}, "medium"),
        # Force low: fallback + stale + no_corroborator + unknown precision
        # fallback(12) + stale(10) + no_corroborator(12) + unknown(1) + complete(5) = 40
        # minus fallback(6), provisional(8) = 26 → weak; stale is valid for freshness calc
        # Actually stale gives freshness_score=10 which passes the freshness gate.
        # 12+10+12+1+5 = 40 - 6 - 8 = 26 → weak
        # To hit "low" (40-59): remove provisional penalty: 40 - 6 = 34 still weak.
        # Use secondary(26)+stale(10)+no_corroborator(12)+unknown(1)+complete(5)=54 - fallback(6) = 48 → low
        ({"authoritative_source_type": "secondary",
          "fallback_used": True,
          "freshness_status": "stale",
          "agreement_status": "no_corroborator",
          "geocode_precision": "unknown",
          "provisional_status": False}, "low"),
    ],
    ids=["high", "medium", "low"],
)
def test_confidence_band_mapping(score_factors, expected_band):
    r = _make_result(**score_factors)
    result = compute_source_confidence(r)
    assert result.confidence_band == expected_band


def test_confidence_score_clamped_to_100():
    """Max possible inputs still produce score <= 100."""
    r = _make_result(
        authoritative_source_type="official",
        freshness_status="fresh",
        agreement_status="agree",
        geocode_precision="station",
    )
    result = compute_source_confidence(r)
    assert result.confidence_score <= 100.0


def test_confidence_score_never_negative():
    r = _make_result(
        authoritative_source_type="fallback",
        fallback_used=True,
        freshness_status="stale",
        agreement_status="conflict",
        disagreement_magnitude=0.99,
        geocode_precision="unknown",
        provisional_status=True,
        measured_value=None,
    )
    result = compute_source_confidence(r)
    assert result.confidence_score >= 0.0


# ─────────────────────────────────────────────────────────────────────
# Gating reason selection
# ─────────────────────────────────────────────────────────────────────

def test_confidence_gating_reason_is_low_confidence_when_band_is_low():
    r = _make_result(
        authoritative_source_type="fallback",
        fallback_used=True,
        freshness_status="stale",
        agreement_status="conflict",
        disagreement_magnitude=0.5,
        geocode_precision="unknown",
        provisional_status=True,
        measured_value=None,
    )
    result = compute_source_confidence(r)
    assert result.gating_reason == REASON_LOW_CONFIDENCE_NO_AUTO_SETTLEMENT


def test_confidence_gating_reason_preserves_official_code_when_high():
    r = _make_result(
        resolution_reason_code=REASON_OFFICIAL_VALID_SELECTED,
    )
    result = compute_source_confidence(r)
    assert result.gating_reason == REASON_OFFICIAL_VALID_SELECTED


# ─────────────────────────────────────────────────────────────────────
# Integration: resolve → confidence
# ─────────────────────────────────────────────────────────────────────

def _official_payload(measured: float = 82.0) -> dict:
    return {
        "source_type": "official",
        "source_name": "IMD",
        "record_id": "001",
        "measured_value": measured,
        "measured_unit": "mm/24hr",
        "threshold_value": 64.5,
        "event_time": _NOW - timedelta(minutes=5),
        "fetch_time": _NOW - timedelta(seconds=10),
        "geocode_precision": "district",
        "priority": 0,
    }


def _fallback_payload(measured: float = 80.0) -> dict:
    return {
        "source_type": "fallback",
        "source_name": "OWM",
        "record_id": "002",
        "measured_value": measured,
        "measured_unit": "mm/24hr",
        "threshold_value": 64.5,
        "event_time": _NOW - timedelta(minutes=3),
        "fetch_time": _NOW - timedelta(seconds=5),
        "geocode_precision": "city",
        "priority": 10,
    }


def test_confidence_high_for_fresh_official_with_cross_source_agreement():
    res = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_official_payload(82.0)],
        fallback_payloads=[_fallback_payload(83.0)],  # close agreement
        event_context=_CTX,
    )
    conf = compute_source_confidence(res)
    assert conf.confidence_band == "high"
    assert conf.confidence_score >= 80.0


def test_confidence_drops_when_fallback_only_is_used():
    res = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[{"source_type": "official", "source_name": "IMD", "timeout": True}],
        fallback_payloads=[_fallback_payload()],
        event_context=_CTX,
    )
    conf = compute_source_confidence(res)
    assert conf.confidence_band in {"medium", "low", "weak"}
    assert conf.confidence_score < 80.0


def test_confidence_drops_on_material_disagreement_integration():
    res = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_official_payload(measured=75.0)],
        fallback_payloads=[_fallback_payload(measured=50.0)],  # threshold_conflict fires first
        event_context=_CTX,
    )
    # 75 crosses 64.5; 50 does not → threshold_conflict; either status causes conflict label
    assert res.agreement_status in {"conflict", "threshold_conflict"}
    conf = compute_source_confidence(res)
    assert conf.confidence_score < 80.0
