from __future__ import annotations

from copy import deepcopy
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta, timezone
from math import isnan
from typing import Any, Optional


REASON_OFFICIAL_VALID_SELECTED = "OFFICIAL_VALID_SELECTED"
REASON_OFFICIAL_STALE_FALLBACK_USED = "OFFICIAL_STALE_FALLBACK_USED"
REASON_OFFICIAL_TIMEOUT_FALLBACK_USED = "OFFICIAL_TIMEOUT_FALLBACK_USED"
REASON_OFFICIAL_MALFORMED_FALLBACK_USED = "OFFICIAL_MALFORMED_FALLBACK_USED"
REASON_BOTH_SOURCES_INVALID = "BOTH_SOURCES_INVALID"
REASON_SOURCE_CONFLICT_UNDER_REVIEW = "SOURCE_CONFLICT_UNDER_REVIEW"
REASON_OFFICIAL_REQUIRED_FOR_TRIGGER = "OFFICIAL_REQUIRED_FOR_TRIGGER"
REASON_CORROBORATOR_MISSING = "CORROBORATOR_MISSING"
REASON_LOW_CONFIDENCE_NO_AUTO_SETTLEMENT = "LOW_CONFIDENCE_NO_AUTO_SETTLEMENT"
REASON_FALLBACK_PROVISIONAL_SETTLEMENT = "FALLBACK_PROVISIONAL_SETTLEMENT"
REASON_AUTHORITATIVE_TELEMETRY_CONFIRMED = "AUTHORITATIVE_TELEMETRY_CONFIRMED"
REASON_SYNTHETIC_ONLY_NOT_SUFFICIENT = "SYNTHETIC_ONLY_NOT_SUFFICIENT"
REASON_SECONDARY_CONTINUITY_SELECTED = "SECONDARY_CONTINUITY_SELECTED"
REASON_SOURCE_CONFIG_UNKNOWN = "SOURCE_CONFIG_UNKNOWN"


DEFAULT_SOURCE_HIERARCHY_CONFIG: dict[str, dict[str, Any]] = {
    "weather": {
        "freshness_sla_seconds": 15 * 60,
        "allowed_units": {"mm/24hr", "C", "°C"},
        "agreement_tolerance": 0.15,
        "future_skew_seconds": 5 * 60,
        "source_config_version": "weather-v1",
        "domain": "weather",
    },
    "Heavy Rainfall": {
        "freshness_sla_seconds": 15 * 60,
        "allowed_units": {"mm/24hr"},
        "agreement_tolerance": 0.12,
        "future_skew_seconds": 5 * 60,
        "source_config_version": "rainfall-v1",
        "domain": "weather",
    },
    "Extreme Rain / Flooding": {
        "freshness_sla_seconds": 15 * 60,
        "allowed_units": {"mm/24hr"},
        "agreement_tolerance": 0.12,
        "future_skew_seconds": 5 * 60,
        "source_config_version": "flood-v1",
        "domain": "weather",
    },
    "Severe Heatwave": {
        "freshness_sla_seconds": 15 * 60,
        "allowed_units": {"C", "°C"},
        "agreement_tolerance": 0.05,
        "future_skew_seconds": 5 * 60,
        "source_config_version": "heat-v1",
        "domain": "weather",
    },
    "aqi": {
        "freshness_sla_seconds": 15 * 60,
        "allowed_units": {"AQI"},
        "agreement_tolerance": 0.10,
        "future_skew_seconds": 5 * 60,
        "source_config_version": "aqi-v1",
        "domain": "aqi",
    },
    "Hazardous AQI": {
        "freshness_sla_seconds": 15 * 60,
        "allowed_units": {"AQI"},
        "agreement_tolerance": 0.10,
        "future_skew_seconds": 5 * 60,
        "source_config_version": "aqi-v1",
        "domain": "aqi",
    },
    "platform": {
        "freshness_sla_seconds": 60,
        "allowed_units": {"minutes down", "seconds", "ms"},
        "agreement_tolerance": 0.10,
        "future_skew_seconds": 30,
        "source_config_version": "outage-v1",
        "domain": "platform",
    },
    "Platform Outage": {
        "freshness_sla_seconds": 60,
        "allowed_units": {"minutes down", "seconds", "ms"},
        "agreement_tolerance": 0.10,
        "future_skew_seconds": 30,
        "source_config_version": "outage-v1",
        "domain": "platform",
    },
    "civil": {
        "freshness_sla_seconds": 30 * 60,
        "allowed_units": {"hours restricted"},
        "agreement_tolerance": 0.10,
        "future_skew_seconds": 5 * 60,
        "source_config_version": "civil-v1",
        "domain": "civil",
    },
    "Civil Disruption": {
        "freshness_sla_seconds": 30 * 60,
        "allowed_units": {"hours restricted"},
        "agreement_tolerance": 0.10,
        "future_skew_seconds": 5 * 60,
        "source_config_version": "civil-v1",
        "domain": "civil",
    },
}


DEFAULT_TRIGGER_POLICIES: dict[str, dict[str, Any]] = {
    "weather": {
        "auto_settle_threshold": 80.0,
        "review_threshold": 55.0,
        "fallback_only_behavior": "review",
        "secondary_only_behavior": "review",
        "requires_official": False,
        "requires_corroborator": False,
        "require_authoritative_telemetry": False,
        "policy_version": "weather-v1",
    },
    "Heavy Rainfall": {
        "auto_settle_threshold": 80.0,
        "review_threshold": 55.0,
        "fallback_only_behavior": "review",
        "secondary_only_behavior": "review",
        "requires_official": False,
        "requires_corroborator": False,
        "require_authoritative_telemetry": False,
        "policy_version": "rainfall-v1",
    },
    "Extreme Rain / Flooding": {
        "auto_settle_threshold": 82.0,
        "review_threshold": 55.0,
        "fallback_only_behavior": "review",
        "secondary_only_behavior": "review",
        "requires_official": False,
        "requires_corroborator": False,
        "require_authoritative_telemetry": False,
        "policy_version": "flood-v1",
    },
    "Severe Heatwave": {
        "auto_settle_threshold": 80.0,
        "review_threshold": 55.0,
        "fallback_only_behavior": "review",
        "secondary_only_behavior": "review",
        "requires_official": False,
        "requires_corroborator": False,
        "require_authoritative_telemetry": False,
        "policy_version": "heat-v1",
    },
    "aqi": {
        "auto_settle_threshold": 80.0,
        "review_threshold": 55.0,
        "fallback_only_behavior": "review",
        "secondary_only_behavior": "review",
        "requires_official": False,
        "requires_corroborator": False,
        "require_authoritative_telemetry": False,
        "policy_version": "aqi-v1",
    },
    "Hazardous AQI": {
        "auto_settle_threshold": 80.0,
        "review_threshold": 55.0,
        "fallback_only_behavior": "review",
        "secondary_only_behavior": "review",
        "requires_official": False,
        "requires_corroborator": False,
        "require_authoritative_telemetry": False,
        "policy_version": "aqi-v1",
    },
    "civil": {
        "auto_settle_threshold": 85.0,
        "review_threshold": 65.0,
        "fallback_only_behavior": "blocked",
        "secondary_only_behavior": "blocked",
        "requires_official": True,
        "requires_corroborator": True,
        "require_authoritative_telemetry": False,
        "policy_version": "civil-v1",
    },
    "Civil Disruption": {
        "auto_settle_threshold": 85.0,
        "review_threshold": 65.0,
        "fallback_only_behavior": "blocked",
        "secondary_only_behavior": "blocked",
        "requires_official": True,
        "requires_corroborator": True,
        "require_authoritative_telemetry": False,
        "policy_version": "civil-v1",
    },
    "platform": {
        "auto_settle_threshold": 85.0,
        "review_threshold": 65.0,
        "fallback_only_behavior": "blocked",
        "secondary_only_behavior": "manual_review",
        "requires_official": False,
        "requires_corroborator": False,
        "require_authoritative_telemetry": True,
        "policy_version": "outage-v1",
    },
    "Platform Outage": {
        "auto_settle_threshold": 85.0,
        "review_threshold": 65.0,
        "fallback_only_behavior": "blocked",
        "secondary_only_behavior": "manual_review",
        "requires_official": False,
        "requires_corroborator": False,
        "require_authoritative_telemetry": True,
        "policy_version": "outage-v1",
    },
}


@dataclass
class SourceResolutionResult:
    trigger_type: str
    authoritative_source_type: Optional[str] = None
    authoritative_source_name: Optional[str] = None
    authoritative_source_record_id: Optional[str] = None
    fallback_used: bool = False
    fallback_source_name: Optional[str] = None
    fallback_reason: Optional[str] = None
    source_event_time: Optional[datetime] = None
    source_fetch_time: Optional[datetime] = None
    freshness_seconds: Optional[int] = None
    freshness_status: str = "unknown"
    data_quality_status: str = "unknown"
    agreement_status: str = "unknown"
    disagreement_magnitude: Optional[float] = None
    geocode_precision: str = "unknown"
    source_scope: Optional[str] = None
    measured_value: Optional[float] = None
    measured_unit: Optional[str] = None
    threshold_value: Optional[float] = None
    threshold_unit: Optional[str] = None
    threshold_crossed: Optional[bool] = None
    provisional_status: bool = False
    resolution_reason_code: str = REASON_BOTH_SOURCES_INVALID
    source_config_version: str = "unknown"
    corroborator_present: bool = False
    source_chain: list[dict[str, Any]] = field(default_factory=list)
    invalid_sources: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class ConfidenceResult:
    confidence_score: float
    confidence_band: str
    authority_score: float
    freshness_score: float
    agreement_score: float
    precision_score: float
    completeness_score: float
    gating_reason: str


@dataclass
class SettlementDecision:
    decision: str
    decision_reason_code: str
    trigger_policy_version: str
    requires_manual_corroboration: bool
    reviewer_hint: Optional[str] = None


@dataclass
class PersistedSnapshot:
    trigger_type: str
    source_resolution: dict[str, Any]
    confidence: dict[str, Any]
    settlement: dict[str, Any]
    snapshot_at: datetime = field(default_factory=datetime.utcnow)


def _coerce_float(value: Any) -> Optional[float]:
    if value in (None, ""):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if isnan(parsed):
        return None
    return parsed


def _coerce_datetime(value: Any) -> Optional[datetime]:
    if value is None:
        return None
    if isinstance(value, datetime):
        if value.tzinfo is not None:
            return value.astimezone(timezone.utc).replace(tzinfo=None)
        return value
    if isinstance(value, str):
        text = value.strip()
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        try:
            parsed = datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is not None:
            return parsed.astimezone(timezone.utc).replace(tzinfo=None)
        return parsed
    return None


def _precision_rank(value: Optional[str]) -> tuple[int, str]:
    normalized = (value or "unknown").lower()
    rank_map = {
        "station": 5,
        "grid": 4,
        "zone": 4,
        "district": 3,
        "city": 2,
        "region": 1,
        "unknown": 0,
    }
    return rank_map.get(normalized, 0), normalized


def _quality_status(candidate: dict[str, Any], cfg: dict[str, Any], now: datetime) -> tuple[bool, str, Optional[int], Optional[datetime], Optional[datetime]]:
    if not candidate:
        return False, "missing", None, None, None
    if candidate.get("timeout"):
        return False, "timeout", None, None, None
    status_code = candidate.get("status_code")
    if isinstance(status_code, int) and status_code >= 500:
        return False, "upstream_5xx", None, None, None
    if candidate.get("malformed"):
        return False, "malformed", None, None, None
    if candidate.get("signature_valid") is False:
        return False, "bad_signature", None, None, None

    measured_value = _coerce_float(candidate.get("measured_value"))
    threshold_value = _coerce_float(candidate.get("threshold_value"))
    measured_unit = candidate.get("measured_unit")
    required_fields = candidate.get("required_fields") or ["measured_value", "measured_unit", "event_time"]
    missing_fields = []
    for field_name in required_fields:
        if field_name == "measured_value" and measured_value is None:
            missing_fields.append(field_name)
        elif field_name == "measured_unit" and not measured_unit:
            missing_fields.append(field_name)
        elif field_name == "threshold_value" and threshold_value is None:
            missing_fields.append(field_name)
        elif field_name == "event_time" and not candidate.get("event_time"):
            missing_fields.append(field_name)
        elif field_name == "location" and not candidate.get("location"):
            missing_fields.append(field_name)
    if missing_fields:
        return False, "missing_field", None, None, None

    if measured_unit and cfg.get("allowed_units") and measured_unit not in cfg["allowed_units"]:
        return False, "unsupported_unit", None, None, None

    valid_range = candidate.get("valid_range")
    if valid_range:
        minimum, maximum = valid_range
        if measured_value is None or measured_value < minimum or measured_value > maximum:
            return False, "impossible_value", None, None, None
    if candidate.get("impossible_value"):
        return False, "impossible_value", None, None, None

    event_time = _coerce_datetime(candidate.get("event_time"))
    fetch_time = _coerce_datetime(candidate.get("fetch_time"))
    if event_time is None and not candidate.get("allow_missing_event_time", False):
        return False, "missing_timestamp", None, None, None

    baseline_time = event_time or fetch_time
    if baseline_time is None:
        return False, "missing_timestamp", None, None, None

    freshness_seconds = int(max(0, (now - baseline_time).total_seconds()))
    if baseline_time > now + timedelta(seconds=cfg.get("future_skew_seconds", 0)):
        return False, "future_skew", freshness_seconds, event_time, fetch_time
    if freshness_seconds > int(cfg.get("freshness_sla_seconds", 0)):
        return False, "stale", freshness_seconds, event_time, fetch_time

    return True, "valid", freshness_seconds, event_time, fetch_time


def _candidate_priority(candidate: dict[str, Any], freshness_seconds: Optional[int]) -> tuple[int, int, str]:
    source_type = (candidate.get("source_type") or "unknown").lower()
    if source_type == "official":
        source_rank = 0
    elif source_type == "secondary":
        source_rank = 1
    elif source_type == "fallback":
        source_rank = 2
    else:
        source_rank = 3
    priority = int(candidate.get("priority", 100))
    freshness_rank = freshness_seconds if freshness_seconds is not None else 999999
    record_id = str(candidate.get("record_id") or candidate.get("source_name") or "")
    return source_rank, priority + freshness_rank, record_id


def _normalize_candidate(candidate: dict[str, Any], cfg: dict[str, Any], now: datetime) -> dict[str, Any]:
    valid, quality_status, freshness_seconds, event_time, fetch_time = _quality_status(candidate, cfg, now)
    measured_value = _coerce_float(candidate.get("measured_value"))
    threshold_value = _coerce_float(candidate.get("threshold_value"))
    threshold_crossed = candidate.get("threshold_crossed")
    if threshold_crossed is None and measured_value is not None and threshold_value is not None:
        threshold_crossed = measured_value >= threshold_value
    precision_rank, geocode_precision = _precision_rank(candidate.get("geocode_precision"))
    source_type = (candidate.get("source_type") or "unknown").lower()
    completeness_status = "complete"
    if candidate.get("missing_optional_fields"):
        completeness_status = "partial"
    if not valid:
        completeness_status = "invalid"
    return {
        **candidate,
        "valid": valid,
        "quality_status": quality_status,
        "freshness_seconds": freshness_seconds,
        "event_time": event_time,
        "fetch_time": fetch_time,
        "measured_value": measured_value,
        "threshold_value": threshold_value,
        "threshold_crossed": threshold_crossed,
        "geocode_precision": geocode_precision,
        "precision_rank": precision_rank,
        "source_type": source_type,
        "completeness_status": completeness_status,
    }


def _agreement_status(
    authoritative: Optional[dict[str, Any]],
    corroborators: list[dict[str, Any]],
    tolerance: float,
) -> tuple[str, Optional[float], bool]:
    if authoritative is None:
        return "no_authoritative_source", None, False
    valid_corroborators = [candidate for candidate in corroborators if candidate.get("valid")]
    if not valid_corroborators:
        return "no_corroborator", None, False
    peer = sorted(valid_corroborators, key=lambda item: _candidate_priority(item, item.get("freshness_seconds")))[0]
    if authoritative.get("threshold_crossed") is not None and peer.get("threshold_crossed") is not None:
        if authoritative["threshold_crossed"] != peer["threshold_crossed"]:
            return "threshold_conflict", 1.0, True
    auth_value = authoritative.get("measured_value")
    peer_value = peer.get("measured_value")
    if auth_value is None or peer_value is None:
        return "no_corroborator", None, True
    difference = abs(float(auth_value) - float(peer_value))
    baseline = max(abs(float(authoritative.get("threshold_value") or 0.0)), abs(float(auth_value)), 1.0)
    magnitude = round(difference / baseline, 4)
    if magnitude > tolerance:
        return "conflict", magnitude, True
    return "agree", magnitude, True


def resolve_authoritative_source(
    trigger_type: str,
    official_payloads: list[dict[str, Any]] | None,
    fallback_payloads: list[dict[str, Any]] | None,
    config: Optional[dict[str, Any]] = None,
    event_context: Optional[dict[str, Any]] = None,
) -> SourceResolutionResult:
    cfg = deepcopy(config or DEFAULT_SOURCE_HIERARCHY_CONFIG.get(trigger_type) or DEFAULT_SOURCE_HIERARCHY_CONFIG.get(
        DEFAULT_SOURCE_HIERARCHY_CONFIG.get(trigger_type, {}).get("domain", "")
    ) or {})
    if not cfg:
        return SourceResolutionResult(
            trigger_type=trigger_type,
            resolution_reason_code=REASON_SOURCE_CONFIG_UNKNOWN,
            source_config_version="unknown",
        )

    now = _coerce_datetime((event_context or {}).get("as_of")) or datetime.utcnow()
    official_candidates = [_normalize_candidate(candidate, cfg, now) for candidate in (official_payloads or [])]
    lower_priority_candidates = [_normalize_candidate(candidate, cfg, now) for candidate in (fallback_payloads or [])]
    all_candidates = official_candidates + lower_priority_candidates
    valid_officials = [candidate for candidate in official_candidates if candidate["valid"]]
    valid_lowers = [candidate for candidate in lower_priority_candidates if candidate["valid"]]

    authoritative = None
    if valid_officials:
        authoritative = sorted(valid_officials, key=lambda item: _candidate_priority(item, item["freshness_seconds"]))[0]
    elif valid_lowers:
        authoritative = sorted(valid_lowers, key=lambda item: _candidate_priority(item, item["freshness_seconds"]))[0]

    invalid_sources = [
        {
            "source_name": candidate.get("source_name"),
            "source_type": candidate.get("source_type"),
            "quality_status": candidate.get("quality_status"),
        }
        for candidate in all_candidates
        if not candidate.get("valid")
    ]

    if authoritative is None:
        fallback_reason = invalid_sources[0]["quality_status"] if invalid_sources else "missing"
        return SourceResolutionResult(
            trigger_type=trigger_type,
            fallback_used=False,
            fallback_reason=fallback_reason,
            freshness_status="invalid",
            data_quality_status="invalid",
            agreement_status="no_authoritative_source",
            geocode_precision="unknown",
            resolution_reason_code=REASON_BOTH_SOURCES_INVALID,
            source_config_version=str(cfg.get("source_config_version", "unknown")),
            invalid_sources=invalid_sources,
            source_chain=[
                {
                    "source_name": candidate.get("source_name"),
                    "source_type": candidate.get("source_type"),
                    "quality_status": candidate.get("quality_status"),
                }
                for candidate in all_candidates
            ],
        )

    corroborators = [candidate for candidate in all_candidates if candidate is not authoritative]
    agreement_status, disagreement_magnitude, corroborator_present = _agreement_status(
        authoritative, corroborators, float(cfg.get("agreement_tolerance", 0.1))
    )

    fallback_used = authoritative.get("source_type") != "official"
    fallback_reason = None
    if fallback_used:
        official_failures = [candidate for candidate in official_candidates if not candidate.get("valid")]
        if official_failures:
            fallback_reason = official_failures[0].get("quality_status")
        elif authoritative.get("source_type") == "secondary":
            fallback_reason = "official_unavailable"
        else:
            fallback_reason = authoritative.get("quality_status")

    if authoritative.get("source_type") == "official":
        resolution_reason_code = REASON_OFFICIAL_VALID_SELECTED
    elif authoritative.get("source_type") == "secondary":
        if fallback_reason == "stale":
            resolution_reason_code = REASON_OFFICIAL_STALE_FALLBACK_USED
        elif fallback_reason == "timeout":
            resolution_reason_code = REASON_OFFICIAL_TIMEOUT_FALLBACK_USED
        elif fallback_reason in {"malformed", "missing_field", "unsupported_unit", "impossible_value", "bad_signature"}:
            resolution_reason_code = REASON_OFFICIAL_MALFORMED_FALLBACK_USED
        else:
            resolution_reason_code = REASON_SECONDARY_CONTINUITY_SELECTED
    else:
        if fallback_reason == "stale":
            resolution_reason_code = REASON_OFFICIAL_STALE_FALLBACK_USED
        elif fallback_reason == "timeout":
            resolution_reason_code = REASON_OFFICIAL_TIMEOUT_FALLBACK_USED
        elif fallback_reason in {"malformed", "missing_field", "unsupported_unit", "impossible_value", "bad_signature"}:
            resolution_reason_code = REASON_OFFICIAL_MALFORMED_FALLBACK_USED
        else:
            resolution_reason_code = REASON_FALLBACK_PROVISIONAL_SETTLEMENT

    provisional_status = authoritative.get("source_type") != "official" or agreement_status in {"conflict", "threshold_conflict"}
    if agreement_status in {"conflict", "threshold_conflict"}:
        resolution_reason_code = REASON_SOURCE_CONFLICT_UNDER_REVIEW

    source_chain = [
        {
            "source_name": candidate.get("source_name"),
            "source_type": candidate.get("source_type"),
            "quality_status": candidate.get("quality_status"),
            "selected": candidate is authoritative,
        }
        for candidate in all_candidates
    ]

    return SourceResolutionResult(
        trigger_type=trigger_type,
        authoritative_source_type=authoritative.get("source_type"),
        authoritative_source_name=authoritative.get("source_name"),
        authoritative_source_record_id=str(authoritative.get("record_id")) if authoritative.get("record_id") is not None else None,
        fallback_used=fallback_used,
        fallback_source_name=authoritative.get("source_name") if fallback_used else None,
        fallback_reason=fallback_reason,
        source_event_time=authoritative.get("event_time"),
        source_fetch_time=authoritative.get("fetch_time"),
        freshness_seconds=authoritative.get("freshness_seconds"),
        freshness_status="fresh" if authoritative.get("quality_status") == "valid" else authoritative.get("quality_status", "unknown"),
        data_quality_status=authoritative.get("quality_status", "unknown"),
        agreement_status=agreement_status,
        disagreement_magnitude=disagreement_magnitude,
        geocode_precision=authoritative.get("geocode_precision", "unknown"),
        source_scope=authoritative.get("source_scope"),
        measured_value=authoritative.get("measured_value"),
        measured_unit=authoritative.get("measured_unit"),
        threshold_value=authoritative.get("threshold_value"),
        threshold_unit=authoritative.get("threshold_unit"),
        threshold_crossed=authoritative.get("threshold_crossed"),
        provisional_status=provisional_status,
        resolution_reason_code=resolution_reason_code,
        source_config_version=str(cfg.get("source_config_version", "unknown")),
        corroborator_present=corroborator_present,
        source_chain=source_chain,
        invalid_sources=invalid_sources,
    )


def compute_source_confidence(
    resolution_result: SourceResolutionResult,
    config: Optional[dict[str, Any]] = None,
    event_context: Optional[dict[str, Any]] = None,
) -> ConfidenceResult:
    del event_context
    cfg = config or DEFAULT_SOURCE_HIERARCHY_CONFIG.get(resolution_result.trigger_type) or {}

    authority_score = {
        "official": 40.0,
        "secondary": 26.0,
        "fallback": 12.0,
    }.get(resolution_result.authoritative_source_type or "", 0.0)
    freshness_score = 25.0 if resolution_result.freshness_status == "fresh" else 10.0 if resolution_result.freshness_status == "stale" else 0.0
    agreement_score = {
        "agree": 20.0,
        "no_corroborator": 12.0,
        "threshold_conflict": 4.0,
        "conflict": 4.0,
        "no_authoritative_source": 0.0,
    }.get(resolution_result.agreement_status, 6.0)
    precision_score = {
        "station": 10.0,
        "grid": 9.0,
        "zone": 9.0,
        "district": 7.0,
        "city": 5.0,
        "region": 3.0,
        "unknown": 1.0,
    }.get(resolution_result.geocode_precision, 1.0)
    completeness_score = 5.0
    if resolution_result.measured_value is None or not resolution_result.measured_unit:
        completeness_score = 0.0
    elif resolution_result.threshold_value is None:
        completeness_score = 2.0

    score = authority_score + freshness_score + agreement_score + precision_score + completeness_score
    if resolution_result.provisional_status:
        score -= 8.0
    if resolution_result.fallback_used:
        score -= 6.0
    if resolution_result.disagreement_magnitude and resolution_result.disagreement_magnitude > float(cfg.get("agreement_tolerance", 0.1)):
        score -= 10.0
    if resolution_result.freshness_status not in {"fresh", "stale"}:
        score = 0.0

    score = max(0.0, min(100.0, round(score, 1)))
    if score >= 80:
        band = "high"
    elif score >= 60:
        band = "medium"
    elif score >= 40:
        band = "low"
    else:
        band = "weak"

    gating_reason = resolution_result.resolution_reason_code
    if resolution_result.authoritative_source_type is None:
        gating_reason = REASON_BOTH_SOURCES_INVALID
    elif band in {"low", "weak"}:
        gating_reason = REASON_LOW_CONFIDENCE_NO_AUTO_SETTLEMENT

    return ConfidenceResult(
        confidence_score=score,
        confidence_band=band,
        authority_score=round(authority_score, 1),
        freshness_score=round(freshness_score, 1),
        agreement_score=round(agreement_score, 1),
        precision_score=round(precision_score, 1),
        completeness_score=round(completeness_score, 1),
        gating_reason=gating_reason,
    )


def evaluate_settlement_from_sources(
    resolution_result: SourceResolutionResult,
    confidence_result: ConfidenceResult,
    trigger_policy: Optional[dict[str, Any]] = None,
    eligibility_ctx: Optional[dict[str, Any]] = None,
) -> SettlementDecision:
    policy = deepcopy(trigger_policy or DEFAULT_TRIGGER_POLICIES.get(resolution_result.trigger_type) or {})
    if not policy:
        return SettlementDecision(
            decision="NO_PAYOUT",
            decision_reason_code=REASON_SOURCE_CONFIG_UNKNOWN,
            trigger_policy_version="unknown",
            requires_manual_corroboration=False,
            reviewer_hint="Settlement config missing.",
        )

    if not (eligibility_ctx or {}).get("eligible", True):
        return SettlementDecision(
            decision="NO_PAYOUT",
            decision_reason_code="ELIGIBILITY_FAILED",
            trigger_policy_version=str(policy.get("policy_version", "unknown")),
            requires_manual_corroboration=False,
            reviewer_hint=(eligibility_ctx or {}).get("reason"),
        )

    if resolution_result.authoritative_source_type is None:
        return SettlementDecision(
            decision="NO_PAYOUT",
            decision_reason_code=REASON_BOTH_SOURCES_INVALID,
            trigger_policy_version=str(policy.get("policy_version", "unknown")),
            requires_manual_corroboration=False,
            reviewer_hint="No trustworthy source passed validation.",
        )

    if policy.get("requires_official") and resolution_result.authoritative_source_type != "official":
        code = REASON_CORROBORATOR_MISSING if policy.get("requires_corroborator") else REASON_OFFICIAL_REQUIRED_FOR_TRIGGER
        return SettlementDecision(
            decision="NO_PAYOUT",
            decision_reason_code=code,
            trigger_policy_version=str(policy.get("policy_version", "unknown")),
            requires_manual_corroboration=bool(policy.get("requires_corroborator")),
            reviewer_hint="Official corroboration is required for this trigger.",
        )

    if policy.get("require_authoritative_telemetry") and resolution_result.authoritative_source_type == "fallback":
        return SettlementDecision(
            decision="NO_PAYOUT",
            decision_reason_code=REASON_SYNTHETIC_ONLY_NOT_SUFFICIENT,
            trigger_policy_version=str(policy.get("policy_version", "unknown")),
            requires_manual_corroboration=True,
            reviewer_hint="Synthetic signals alone cannot confirm this outage.",
        )

    if resolution_result.fallback_used and resolution_result.authoritative_source_type == "fallback":
        fallback_behavior = policy.get("fallback_only_behavior", "review")
        if fallback_behavior == "blocked":
            return SettlementDecision(
                decision="NO_PAYOUT",
                decision_reason_code=REASON_OFFICIAL_REQUIRED_FOR_TRIGGER,
                trigger_policy_version=str(policy.get("policy_version", "unknown")),
                requires_manual_corroboration=True,
                reviewer_hint="Fallback-only signal is insufficient for settlement.",
            )
        if fallback_behavior == "review":
            return SettlementDecision(
                decision="PENDING_REVIEW",
                decision_reason_code=REASON_FALLBACK_PROVISIONAL_SETTLEMENT,
                trigger_policy_version=str(policy.get("policy_version", "unknown")),
                requires_manual_corroboration=True,
                reviewer_hint="Fallback source used; confirm with stronger evidence.",
            )

    if resolution_result.authoritative_source_type == "secondary":
        secondary_behavior = policy.get("secondary_only_behavior", "review")
        if secondary_behavior == "blocked":
            return SettlementDecision(
                decision="NO_PAYOUT",
                decision_reason_code=REASON_OFFICIAL_REQUIRED_FOR_TRIGGER,
                trigger_policy_version=str(policy.get("policy_version", "unknown")),
                requires_manual_corroboration=True,
                reviewer_hint="Continuity source is not sufficient for this trigger.",
            )
        if secondary_behavior == "manual_review":
            return SettlementDecision(
                decision="MANUAL_REVIEW",
                decision_reason_code=REASON_SECONDARY_CONTINUITY_SELECTED,
                trigger_policy_version=str(policy.get("policy_version", "unknown")),
                requires_manual_corroboration=True,
                reviewer_hint="Continuity source detected the event; manual corroboration required.",
            )

    if confidence_result.confidence_score >= float(policy.get("auto_settle_threshold", 80.0)) and not resolution_result.provisional_status:
        return SettlementDecision(
            decision="AUTO_SETTLE",
            decision_reason_code=REASON_AUTHORITATIVE_TELEMETRY_CONFIRMED if resolution_result.trigger_type in {"Platform Outage", "platform"} else REASON_OFFICIAL_VALID_SELECTED,
            trigger_policy_version=str(policy.get("policy_version", "unknown")),
            requires_manual_corroboration=False,
            reviewer_hint=None,
        )

    if confidence_result.confidence_score >= float(policy.get("review_threshold", 55.0)):
        return SettlementDecision(
            decision="PENDING_REVIEW",
            decision_reason_code=REASON_SOURCE_CONFLICT_UNDER_REVIEW if resolution_result.agreement_status in {"conflict", "threshold_conflict"} else confidence_result.gating_reason,
            trigger_policy_version=str(policy.get("policy_version", "unknown")),
            requires_manual_corroboration=resolution_result.provisional_status,
            reviewer_hint="Source quality is usable but not settlement-grade.",
        )

    return SettlementDecision(
        decision="MANUAL_REVIEW",
        decision_reason_code=REASON_LOW_CONFIDENCE_NO_AUTO_SETTLEMENT,
        trigger_policy_version=str(policy.get("policy_version", "unknown")),
        requires_manual_corroboration=True,
        reviewer_hint="Low source confidence requires manual verification.",
    )


def build_source_hierarchy_snapshot(
    resolution_result: SourceResolutionResult,
    confidence_result: ConfidenceResult,
    settlement_decision: SettlementDecision,
) -> PersistedSnapshot:
    return PersistedSnapshot(
        trigger_type=resolution_result.trigger_type,
        source_resolution=deepcopy(asdict(resolution_result)),
        confidence=deepcopy(asdict(confidence_result)),
        settlement=deepcopy(asdict(settlement_decision)),
    )


def snapshot_to_meta(snapshot: PersistedSnapshot) -> dict[str, Any]:
    source_resolution = snapshot.source_resolution
    confidence = snapshot.confidence
    settlement = snapshot.settlement
    source_type = source_resolution.get("authoritative_source_type") or "none"
    if source_type == "official":
        status = "Official source active - settlement-grade signal available"
    elif source_type == "secondary":
        status = "Secondary continuity source active - trigger may be detected, payout review required"
    elif source_type == "fallback":
        status = "Fallback monitoring only - no claim automation"
    else:
        status = "No trustworthy source available - monitoring only"

    chain_lines = []
    for item in source_resolution.get("source_chain", []):
        label = item.get("source_name") or "Unknown source"
        role = (item.get("source_type") or "unknown").upper()
        quality = (item.get("quality_status") or "unknown").upper()
        selected = " [SELECTED]" if item.get("selected") else ""
        chain_lines.append(f"{role}: {label} [{quality}]{selected}")
    if not chain_lines:
        chain_lines.append("No source chain captured.")
    chain_lines.append(f"Resolution: {source_resolution.get('resolution_reason_code')}")
    chain_lines.append(f"Settlement: {settlement.get('decision')} ({settlement.get('decision_reason_code')})")

    return {
        "source_primary": source_resolution.get("authoritative_source_name") or "Unavailable",
        "source_secondary": source_resolution.get("fallback_source_name"),
        "source_used": source_resolution.get("authoritative_source_name") or "Unavailable",
        "source_tier": source_type,
        "confidence_score": confidence.get("confidence_score", 0.0),
        "is_validated": source_type == "official" and not source_resolution.get("provisional_status", False),
        "claim_allowed": settlement.get("decision") != "NO_PAYOUT",
        "requires_manual_review": settlement.get("decision") in {"PENDING_REVIEW", "MANUAL_REVIEW"},
        "status": status,
        "source_log_lines": chain_lines,
        "resolution_reason_code": source_resolution.get("resolution_reason_code"),
        "fallback_reason": source_resolution.get("fallback_reason"),
        "freshness_status": source_resolution.get("freshness_status"),
        "agreement_status": source_resolution.get("agreement_status"),
        "provisional_status": source_resolution.get("provisional_status"),
        "source_config_version": source_resolution.get("source_config_version"),
        "reviewer_hint": settlement.get("reviewer_hint"),
    }
