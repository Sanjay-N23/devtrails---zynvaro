"""
backend/tests/unit/test_source_settlement_gating.py
=====================================================
Unit tests for evaluate_settlement_from_sources().
Covers:
  - Auto-settle, pending-review, manual-review, no-payout paths
  - Trigger-specific restrictions (civil, outage)
  - Eligibility-failure gating
  - Fallback / secondary behaviour rules
  - Confidence band thresholds
  - Snapshot immutability (config changes don't mutate decided outcome)
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from services.source_hierarchy import (
    REASON_AUTHORITATIVE_TELEMETRY_CONFIRMED,
    REASON_BOTH_SOURCES_INVALID,
    REASON_CORROBORATOR_MISSING,
    REASON_FALLBACK_PROVISIONAL_SETTLEMENT,
    REASON_LOW_CONFIDENCE_NO_AUTO_SETTLEMENT,
    REASON_OFFICIAL_REQUIRED_FOR_TRIGGER,
    REASON_OFFICIAL_VALID_SELECTED,
    REASON_SECONDARY_CONTINUITY_SELECTED,
    REASON_SOURCE_CONFIG_UNKNOWN,
    REASON_SYNTHETIC_ONLY_NOT_SUFFICIENT,
    ConfidenceResult,
    SettlementDecision,
    SourceResolutionResult,
    build_source_hierarchy_snapshot,
    compute_source_confidence,
    evaluate_settlement_from_sources,
    resolve_authoritative_source,
)

_NOW = datetime(2026, 4, 17, 7, 0, 0)
_CTX = {"as_of": _NOW}


# ── Minimal helpers ───────────────────────────────────────────────────

def _make_resolution(
    *,
    trigger_type: str = "Heavy Rainfall",
    authoritative_source_type: str | None = "official",
    fallback_used: bool = False,
    provisional_status: bool = False,
    agreement_status: str = "agree",
    disagreement_magnitude: float | None = None,
    freshness_status: str = "fresh",
    geocode_precision: str = "district",
    measured_value: float | None = 82.0,
    measured_unit: str | None = "mm/24hr",
    threshold_value: float | None = 64.5,
    corroborator_present: bool = False,
    resolution_reason_code: str = REASON_OFFICIAL_VALID_SELECTED,
) -> SourceResolutionResult:
    return SourceResolutionResult(
        trigger_type=trigger_type,
        authoritative_source_type=authoritative_source_type,
        fallback_used=fallback_used,
        provisional_status=provisional_status,
        agreement_status=agreement_status,
        disagreement_magnitude=disagreement_magnitude,
        freshness_status=freshness_status,
        geocode_precision=geocode_precision,
        measured_value=measured_value,
        measured_unit=measured_unit,
        threshold_value=threshold_value,
        corroborator_present=corroborator_present,
        resolution_reason_code=resolution_reason_code,
    )


def _high_conf(trigger: str = "Heavy Rainfall") -> tuple[SourceResolutionResult, ConfidenceResult]:
    res = _make_resolution(trigger_type=trigger, geocode_precision="station")
    conf = compute_source_confidence(res)
    return res, conf


def _low_conf(trigger: str = "Heavy Rainfall") -> tuple[SourceResolutionResult, ConfidenceResult]:
    res = _make_resolution(
        trigger_type=trigger,
        authoritative_source_type="fallback",
        fallback_used=True,
        freshness_status="stale",
        agreement_status="conflict",
        disagreement_magnitude=0.5,
        geocode_precision="unknown",
        provisional_status=True,
        measured_value=None,
    )
    conf = compute_source_confidence(res)
    return res, conf


# ─────────────────────────────────────────────────────────────────────
# Core decision paths
# ─────────────────────────────────────────────────────────────────────

def test_settlement_auto_settles_when_confidence_exceeds_threshold():
    res, conf = _high_conf()
    assert conf.confidence_score >= 80.0
    decision = evaluate_settlement_from_sources(res, conf)
    assert decision.decision == "AUTO_SETTLE"
    assert decision.decision_reason_code == REASON_OFFICIAL_VALID_SELECTED
    assert decision.requires_manual_corroboration is False


def test_settlement_routes_to_pending_review_when_confidence_is_mid_band():
    """Score in 55–79 → pending review."""
    res = _make_resolution(
        authoritative_source_type="official",
        freshness_status="stale",       # 10 freshness
        agreement_status="no_corroborator",  # 12 agreement
        geocode_precision="city",       # 5 precision
        provisional_status=True,        # -8 penalty
    )
    conf = compute_source_confidence(res)
    assert 55.0 <= conf.confidence_score < 80.0, f"Expected mid band, got {conf.confidence_score}"
    decision = evaluate_settlement_from_sources(res, conf)
    assert decision.decision == "PENDING_REVIEW"


def test_settlement_manual_review_when_confidence_below_review_threshold():
    res, conf = _low_conf()
    decision = evaluate_settlement_from_sources(res, conf)
    assert decision.decision == "MANUAL_REVIEW"
    assert decision.decision_reason_code == REASON_LOW_CONFIDENCE_NO_AUTO_SETTLEMENT
    assert decision.requires_manual_corroboration is True


def test_settlement_no_payout_when_no_authoritative_source():
    res = _make_resolution(authoritative_source_type=None, freshness_status="invalid",
                           resolution_reason_code=REASON_BOTH_SOURCES_INVALID)
    conf = compute_source_confidence(res)
    decision = evaluate_settlement_from_sources(res, conf)
    assert decision.decision == "NO_PAYOUT"
    assert decision.decision_reason_code == REASON_BOTH_SOURCES_INVALID


def test_settlement_no_payout_when_eligibility_fails():
    res, conf = _high_conf()
    decision = evaluate_settlement_from_sources(
        res, conf,
        eligibility_ctx={"eligible": False, "reason": "Worker in different zone"},
    )
    assert decision.decision == "NO_PAYOUT"
    assert decision.decision_reason_code == "ELIGIBILITY_FAILED"
    assert "zone" in (decision.reviewer_hint or "").lower()


def test_settlement_no_payout_when_missing_trigger_policy_config():
    res = _make_resolution(trigger_type="UNKNOWN_TRIGGER_99")
    conf = compute_source_confidence(res)
    decision = evaluate_settlement_from_sources(res, conf, trigger_policy={})
    assert decision.decision == "NO_PAYOUT"
    assert decision.decision_reason_code == REASON_SOURCE_CONFIG_UNKNOWN


# ─────────────────────────────────────────────────────────────────────
# Civil disruption — requires official + corroborator
# ─────────────────────────────────────────────────────────────────────

def test_settlement_blocks_civil_disruption_without_official_source():
    res = _make_resolution(
        trigger_type="Civil Disruption",
        authoritative_source_type="fallback",
        fallback_used=True,
        provisional_status=True,
        agreement_status="no_corroborator",
        geocode_precision="city",
        freshness_status="fresh",
        resolution_reason_code=REASON_FALLBACK_PROVISIONAL_SETTLEMENT,
    )
    conf = compute_source_confidence(res)
    decision = evaluate_settlement_from_sources(res, conf)
    # Civil Disruption: requires_official=True + fallback_only_behavior=blocked
    assert decision.decision == "NO_PAYOUT"
    assert decision.decision_reason_code in {
        REASON_OFFICIAL_REQUIRED_FOR_TRIGGER,
        REASON_CORROBORATOR_MISSING,
    }


def test_settlement_blocks_civil_disruption_without_corroborator():
    res = _make_resolution(
        trigger_type="Civil Disruption",
        authoritative_source_type="secondary",
        fallback_used=False,
        provisional_status=False,
        agreement_status="no_corroborator",
        geocode_precision="district",
        freshness_status="fresh",
        resolution_reason_code=REASON_SECONDARY_CONTINUITY_SELECTED,
    )
    conf = compute_source_confidence(res)
    decision = evaluate_settlement_from_sources(res, conf)
    # secondary_only_behavior=blocked for civil
    assert decision.decision == "NO_PAYOUT"


def test_settlement_allows_civil_disruption_with_official_and_high_confidence():
    res = _make_resolution(
        trigger_type="Civil Disruption",
        authoritative_source_type="official",
        agreement_status="agree",
        freshness_status="fresh",
        geocode_precision="station",
        corroborator_present=True,
    )
    conf = compute_source_confidence(res)
    # Civil threshold = 85 → need score ≥ 85; max possible with station = 100
    assert conf.confidence_score >= 85.0
    decision = evaluate_settlement_from_sources(res, conf)
    assert decision.decision == "AUTO_SETTLE"


# ─────────────────────────────────────────────────────────────────────
# Platform Outage — requires authoritative telemetry
# ─────────────────────────────────────────────────────────────────────

def test_settlement_blocks_outage_without_authoritative_telemetry():
    """Synthetic/fallback probe only → SYNTHETIC_ONLY_NOT_SUFFICIENT."""
    res = _make_resolution(
        trigger_type="Platform Outage",
        authoritative_source_type="fallback",
        fallback_used=True,
        provisional_status=True,
        freshness_status="fresh",
        geocode_precision="city",
        resolution_reason_code=REASON_FALLBACK_PROVISIONAL_SETTLEMENT,
    )
    conf = compute_source_confidence(res)
    decision = evaluate_settlement_from_sources(res, conf)
    assert decision.decision == "NO_PAYOUT"
    assert decision.decision_reason_code == REASON_SYNTHETIC_ONLY_NOT_SUFFICIENT


def test_settlement_auto_settles_outage_with_partner_telemetry_high_confidence():
    res = _make_resolution(
        trigger_type="Platform Outage",
        authoritative_source_type="official",
        agreement_status="agree",
        freshness_status="fresh",
        geocode_precision="zone",
    )
    conf = compute_source_confidence(res)
    assert conf.confidence_score >= 85.0, f"score was {conf.confidence_score}"
    decision = evaluate_settlement_from_sources(res, conf)
    assert decision.decision == "AUTO_SETTLE"
    assert decision.decision_reason_code == REASON_AUTHORITATIVE_TELEMETRY_CONFIRMED


# ─────────────────────────────────────────────────────────────────────
# Fallback / secondary behaviour tables
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("trigger_type", "source_type", "fallback_used", "expected_decision"),
    [
        # Weather: fallback_only_behavior=review → PENDING_REVIEW
        ("Heavy Rainfall", "fallback", True, "PENDING_REVIEW"),
        # Civil disruption: fallback_only_behavior=blocked → NO_PAYOUT (requires_official=True blocks first)
        ("Civil Disruption", "fallback", True, "NO_PAYOUT"),
        # Platform: require_authoritative_telemetry=True blocks fallback → NO_PAYOUT
        ("Platform Outage", "fallback", True, "NO_PAYOUT"),
    ],
)
def test_settlement_fallback_only_behavior_by_trigger(
    trigger_type, source_type, fallback_used, expected_decision
):
    res = _make_resolution(
        trigger_type=trigger_type,
        authoritative_source_type=source_type,
        fallback_used=fallback_used,
        provisional_status=True,
        freshness_status="fresh",
        agreement_status="no_corroborator",
        geocode_precision="city",
        resolution_reason_code=REASON_FALLBACK_PROVISIONAL_SETTLEMENT,
    )
    conf = compute_source_confidence(res)
    decision = evaluate_settlement_from_sources(res, conf)
    assert decision.decision == expected_decision


@pytest.mark.parametrize(
    ("trigger_type", "expected_decision"),
    [
        # Weather: secondary_only_behavior=review → PENDING_REVIEW
        ("Heavy Rainfall", "PENDING_REVIEW"),
        # Civil: secondary_only_behavior=blocked → NO_PAYOUT (requires_official=True overrides)
        ("Civil Disruption", "NO_PAYOUT"),
        # Platform: secondary_only_behavior=manual_review → MANUAL_REVIEW
        ("Platform Outage", "MANUAL_REVIEW"),
    ],
)
def test_settlement_secondary_only_behavior_by_trigger(trigger_type, expected_decision):
    res = _make_resolution(
        trigger_type=trigger_type,
        authoritative_source_type="secondary",
        fallback_used=False,
        provisional_status=False,
        freshness_status="fresh",
        agreement_status="no_corroborator",
        geocode_precision="district",
        resolution_reason_code=REASON_SECONDARY_CONTINUITY_SELECTED,
    )
    conf = compute_source_confidence(res)
    decision = evaluate_settlement_from_sources(res, conf)
    assert decision.decision == expected_decision


# ─────────────────────────────────────────────────────────────────────
# Threshold boundary: auto_settle vs review threshold
# ─────────────────────────────────────────────────────────────────────

def test_settlement_no_auto_settle_when_score_is_exactly_on_review_threshold():
    """At exactly review_threshold(55) → PENDING_REVIEW, not MANUAL_REVIEW."""
    # Direct injection of known score
    res = _make_resolution(
        authoritative_source_type="official",
        freshness_status="stale",
        agreement_status="no_corroborator",
        geocode_precision="region",   # 3 pts
        provisional_status=True,
    )
    conf = compute_source_confidence(res)
    # Manually override to exactly 55.0 for this boundary test
    conf = ConfidenceResult(
        confidence_score=55.0,
        confidence_band="medium",
        authority_score=40.0,
        freshness_score=10.0,
        agreement_score=12.0,
        precision_score=3.0,
        completeness_score=5.0,
        gating_reason=REASON_OFFICIAL_VALID_SELECTED,
    )
    decision = evaluate_settlement_from_sources(res, conf)
    assert decision.decision == "PENDING_REVIEW"


def test_settlement_manual_review_when_confidence_below_review_threshold():
    """Injected score of 54.9 is below review_threshold(55) → MANUAL_REVIEW."""
    res = _make_resolution(
        authoritative_source_type="official",
        freshness_status="stale",
    )
    # score=54.9 is below 55.0 review_threshold → MANUAL_REVIEW
    conf = ConfidenceResult(
        confidence_score=54.9,
        confidence_band="low",
        authority_score=40.0,
        freshness_score=10.0,
        agreement_score=0.0,
        precision_score=3.0,
        completeness_score=0.0,
        gating_reason=REASON_LOW_CONFIDENCE_NO_AUTO_SETTLEMENT,
    )
    decision = evaluate_settlement_from_sources(res, conf)
    assert decision.decision == "MANUAL_REVIEW"


# ─────────────────────────────────────────────────────────────────────
# Snapshot immutability
# ─────────────────────────────────────────────────────────────────────

def test_snapshot_preserves_original_confidence_band_after_algorithm_update():
    """Simulates: confidence was HIGH at claim time; later run same resolver differently.
    The snapshot stores the point-in-time result, not a live pointer."""
    res, conf = _high_conf()
    decision = evaluate_settlement_from_sources(res, conf)
    snapshot = build_source_hierarchy_snapshot(res, conf, decision)

    # Snapshot confidence must match original result — not be recomputed from live state
    snap_score = snapshot.confidence["confidence_score"]
    assert snap_score == pytest.approx(conf.confidence_score)
    assert snapshot.confidence["confidence_band"] == conf.confidence_band


def test_snapshot_uses_claim_time_source_priority_config():
    """Snapshot must freeze the authoritative_source_type used, not re-resolve later."""
    res, conf = _high_conf()
    decision = evaluate_settlement_from_sources(res, conf)
    snapshot = build_source_hierarchy_snapshot(res, conf, decision)

    assert snapshot.source_resolution["authoritative_source_type"] == "official"
    assert snapshot.source_resolution["resolution_reason_code"] == REASON_OFFICIAL_VALID_SELECTED
    # source_chain is captured at snapshot time
    assert "source_chain" in snapshot.source_resolution


def test_snapshot_preserves_original_official_measurement_after_provider_revision():
    """If official source later revises value from 82 to 60, the snapshot keeps 82."""
    res = _make_resolution(measured_value=82.0)
    conf = compute_source_confidence(res)
    decision = evaluate_settlement_from_sources(res, conf)
    snapshot = build_source_hierarchy_snapshot(res, conf, decision)

    # Simulate a later "revision" — we create a new resolution with different value
    res_revised = _make_resolution(measured_value=60.0)
    conf_revised = compute_source_confidence(res_revised)
    decision_revised = evaluate_settlement_from_sources(res_revised, conf_revised)
    snapshot_revised = build_source_hierarchy_snapshot(res_revised, conf_revised, decision_revised)

    assert snapshot.source_resolution["measured_value"] == pytest.approx(82.0)
    assert snapshot_revised.source_resolution["measured_value"] == pytest.approx(60.0)
    # Original snapshot unchanged
    assert snapshot.source_resolution["measured_value"] != snapshot_revised.source_resolution["measured_value"]


def test_snapshot_has_snapshot_at_timestamp():
    res, conf = _high_conf()
    decision = evaluate_settlement_from_sources(res, conf)
    snapshot = build_source_hierarchy_snapshot(res, conf, decision)
    assert isinstance(snapshot.snapshot_at, datetime)


def test_snapshot_settlement_fields_are_frozen():
    res, conf = _high_conf()
    decision = evaluate_settlement_from_sources(res, conf)
    snapshot = build_source_hierarchy_snapshot(res, conf, decision)

    assert snapshot.settlement["decision"] == "AUTO_SETTLE"
    assert snapshot.settlement["requires_manual_corroboration"] is False
    assert snapshot.settlement["trigger_policy_version"] is not None


# ─────────────────────────────────────────────────────────────────────
# End-to-end: resolve → confidence → settle
# ─────────────────────────────────────────────────────────────────────

def _make_weather_official(measured: float = 82.0, stale: bool = False) -> dict:
    offset = -(16 * 60) if stale else -(5 * 60)
    return {
        "source_type": "official",
        "source_name": "IMD district",
        "record_id": "001",
        "measured_value": measured,
        "measured_unit": "mm/24hr",
        "threshold_value": 64.5,
        "event_time": _NOW + timedelta(seconds=offset),
        "fetch_time": _NOW - timedelta(seconds=10),
        "geocode_precision": "station",
        "priority": 0,
    }


def _make_weather_fallback(measured: float = 80.0) -> dict:
    return {
        "source_type": "fallback",
        "source_name": "OWM city",
        "record_id": "002",
        "measured_value": measured,
        "measured_unit": "mm/24hr",
        "threshold_value": 64.5,
        "event_time": _NOW - timedelta(minutes=3),
        "fetch_time": _NOW - timedelta(seconds=5),
        "geocode_precision": "city",
        "priority": 10,
    }


def test_full_pipeline_official_valid_auto_settles():
    res = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_make_weather_official()],
        fallback_payloads=[_make_weather_fallback()],
        event_context=_CTX,
    )
    conf = compute_source_confidence(res)
    decision = evaluate_settlement_from_sources(res, conf)
    assert decision.decision == "AUTO_SETTLE"


def test_full_pipeline_official_stale_fallback_creates_pending_review():
    res = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_make_weather_official(stale=True)],
        fallback_payloads=[_make_weather_fallback()],
        event_context=_CTX,
    )
    conf = compute_source_confidence(res)
    decision = evaluate_settlement_from_sources(res, conf)
    assert decision.decision == "PENDING_REVIEW"
    assert decision.decision_reason_code == REASON_FALLBACK_PROVISIONAL_SETTLEMENT


def test_full_pipeline_both_invalid_returns_no_payout():
    res = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[{"source_type": "official", "source_name": "IMD", "timeout": True}],
        fallback_payloads=[{"source_type": "fallback", "source_name": "OWM", "timeout": True}],
        event_context=_CTX,
    )
    conf = compute_source_confidence(res)
    decision = evaluate_settlement_from_sources(res, conf)
    assert decision.decision == "NO_PAYOUT"
    assert decision.decision_reason_code == REASON_BOTH_SOURCES_INVALID


def test_full_pipeline_eligibility_failure_overrides_good_source():
    res = resolve_authoritative_source(
        "Heavy Rainfall",
        official_payloads=[_make_weather_official()],
        fallback_payloads=[],
        event_context=_CTX,
    )
    conf = compute_source_confidence(res)
    assert conf.confidence_band == "high"
    decision = evaluate_settlement_from_sources(
        res, conf,
        eligibility_ctx={"eligible": False, "reason": "Cooling-off period active"},
    )
    assert decision.decision == "NO_PAYOUT"
    assert decision.decision_reason_code == "ELIGIBILITY_FAILED"
