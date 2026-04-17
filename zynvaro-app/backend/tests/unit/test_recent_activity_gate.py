"""
backend/tests/unit/test_recent_activity_gate.py
================================================
Direct unit tests for:
  1. get_recent_activity_snapshot() — all branches
  2. _worker_trigger_eligibility()  — all 6 return paths

All tests are pure-Python; NO database, NO HTTP client needed.
Worker objects are simple namespaces so tests run in ~0ms each.
"""
from __future__ import annotations

from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest

from services.fraud_engine import (
    RECENT_ACTIVITY_WINDOW_HOURS,
    get_recent_activity_snapshot,
)

# ── Minimal worker stub ───────────────────────────────────────────────

def _worker(
    *,
    last_location_at: datetime | None = None,
    last_activity_source: str | None = "session_ping",
    city: str = "Bangalore",
    platform: str = "Blinkit",
    last_known_lat: float | None = None,
    last_known_lng: float | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        last_location_at=last_location_at,
        last_activity_source=last_activity_source,
        city=city,
        platform=platform,
        last_known_lat=last_known_lat,
        last_known_lng=last_known_lng,
        home_lat=None,
        home_lng=None,
    )


_NOW = datetime(2026, 4, 17, 7, 0, 0)


# ─────────────────────────────────────────────────────────────────────
# A — get_recent_activity_snapshot(): no location_at
# ─────────────────────────────────────────────────────────────────────

def test_recent_activity_no_location_at_is_ineligible():
    w = _worker(last_location_at=None)
    result = get_recent_activity_snapshot(w, as_of=_NOW)
    assert result["eligible"] is False
    assert result["activity_at"] is None
    assert result["activity_age_hours"] is None
    assert "No recent app activity" in result["reason"]


def test_recent_activity_none_sets_activity_source_in_result():
    w = _worker(last_location_at=None, last_activity_source="signup_seed")
    result = get_recent_activity_snapshot(w, as_of=_NOW)
    assert result["activity_source"] == "signup_seed"


# ─────────────────────────────────────────────────────────────────────
# B — window exceeded (too old)
# ─────────────────────────────────────────────────────────────────────

def test_recent_activity_beyond_window_is_ineligible():
    too_old = _NOW - timedelta(hours=RECENT_ACTIVITY_WINDOW_HOURS + 1)
    w = _worker(last_location_at=too_old, last_activity_source="gps_ping")
    result = get_recent_activity_snapshot(w, as_of=_NOW)
    assert result["eligible"] is False
    assert result["activity_age_hours"] == pytest.approx(
        RECENT_ACTIVITY_WINDOW_HOURS + 1, abs=0.1
    )
    assert str(RECENT_ACTIVITY_WINDOW_HOURS) in result["reason"]


def test_recent_activity_boundary_exactly_48h_is_eligible():
    """Boundary: exactly at the 48-hour mark — still within window."""
    exact = _NOW - timedelta(hours=RECENT_ACTIVITY_WINDOW_HOURS)
    w = _worker(last_location_at=exact, last_activity_source="gps_ping")
    result = get_recent_activity_snapshot(w, as_of=_NOW)
    # `last_activity_at < reference_time - timedelta(hours=48)` → strictly less-than
    # so exactly 48h ago means NOT less-than → eligible
    assert result["eligible"] is True


def test_recent_activity_boundary_48h_plus_1_second_is_ineligible():
    one_over = _NOW - timedelta(hours=RECENT_ACTIVITY_WINDOW_HOURS, seconds=1)
    w = _worker(last_location_at=one_over, last_activity_source="gps_ping")
    result = get_recent_activity_snapshot(w, as_of=_NOW)
    assert result["eligible"] is False


# ─────────────────────────────────────────────────────────────────────
# C — activity_source rules
# ─────────────────────────────────────────────────────────────────────

def test_recent_activity_signup_seed_is_ineligible():
    """signup_seed is deliberately excluded — routes to REVIEW_REQUIRED."""
    fresh = _NOW - timedelta(hours=2)
    w = _worker(last_location_at=fresh, last_activity_source="signup_seed")
    result = get_recent_activity_snapshot(w, as_of=_NOW)
    assert result["eligible"] is False
    assert result["eligibility_state"] == "REVIEW_REQUIRED"
    assert result["reason_code"] == "SIGNALS_TOO_WEAK"


def test_recent_activity_gps_ping_fresh_is_eligible():
    fresh = _NOW - timedelta(hours=3)
    w = _worker(last_location_at=fresh, last_activity_source="gps_ping")
    result = get_recent_activity_snapshot(w, as_of=_NOW)
    assert result["eligible"] is True
    assert result["activity_source"] == "gps_ping"


def test_recent_activity_session_ping_fresh_is_eligible():
    fresh = _NOW - timedelta(hours=6)
    w = _worker(last_location_at=fresh, last_activity_source="session_ping")
    result = get_recent_activity_snapshot(w, as_of=_NOW)
    assert result["eligible"] is True
    assert result["activity_source"] == "session_ping"


def test_recent_activity_none_source_is_ineligible():
    """source=None is not in {gps_ping, session_ping} → blocked."""
    fresh = _NOW - timedelta(hours=1)
    w = _worker(last_location_at=fresh, last_activity_source=None)
    result = get_recent_activity_snapshot(w, as_of=_NOW)
    assert result["eligible"] is False


def test_recent_activity_unknown_source_is_ineligible():
    fresh = _NOW - timedelta(hours=1)
    w = _worker(last_location_at=fresh, last_activity_source="unknown_source")
    result = get_recent_activity_snapshot(w, as_of=_NOW)
    assert result["eligible"] is False


# ─────────────────────────────────────────────────────────────────────
# D — as_of parameter (time-travel)
# ─────────────────────────────────────────────────────────────────────

def test_recent_activity_as_of_controls_reference_time():
    """Activity that is 50h old is ineligible at NOW but eligible at NOW-10h."""
    activity_time = _NOW - timedelta(hours=50)   # 50h old — beyond 48h window
    w = _worker(last_location_at=activity_time, last_activity_source="gps_ping")

    # From NOW: 50h old → beyond 48h window → ineligible
    result_far = get_recent_activity_snapshot(w, as_of=_NOW)
    assert result_far["eligible"] is False

    # From NOW - 10h: only 40h old → within window → eligible
    earlier_ref = _NOW - timedelta(hours=10)
    result_earlier = get_recent_activity_snapshot(w, as_of=earlier_ref)
    assert result_earlier["eligible"] is True


def test_recent_activity_custom_window_hours_respected():
    """Pass a custom window of 12h; activity that is 15h old is blocked."""
    fifteen_hours_ago = _NOW - timedelta(hours=15)
    w = _worker(last_location_at=fifteen_hours_ago, last_activity_source="gps_ping")
    result = get_recent_activity_snapshot(w, as_of=_NOW, window_hours=12)
    assert result["eligible"] is False


def test_recent_activity_custom_window_allows_fresh_activity():
    """Same worker, window=24h → 15h old is fine."""
    fifteen_hours_ago = _NOW - timedelta(hours=15)
    w = _worker(last_location_at=fifteen_hours_ago, last_activity_source="gps_ping")
    result = get_recent_activity_snapshot(w, as_of=_NOW, window_hours=24)
    assert result["eligible"] is True


# ─────────────────────────────────────────────────────────────────────
# E — returned field completeness
# ─────────────────────────────────────────────────────────────────────

def test_recent_activity_snapshot_returns_activity_age_hours_on_eligible():
    two_hours_ago = _NOW - timedelta(hours=2)
    w = _worker(last_location_at=two_hours_ago, last_activity_source="session_ping")
    result = get_recent_activity_snapshot(w, as_of=_NOW)
    assert result["eligible"] is True
    assert result["activity_age_hours"] == pytest.approx(2.0, abs=0.05)
    assert result["activity_at"] == two_hours_ago


def test_recent_activity_reason_message_contains_age_hours():
    five_hours_ago = _NOW - timedelta(hours=5)
    w = _worker(last_location_at=five_hours_ago, last_activity_source="gps_ping")
    result = get_recent_activity_snapshot(w, as_of=_NOW)
    # Eligible path: reason says "X hours before payout review"
    assert "5.0" in result["reason"]


def test_recent_activity_all_required_keys_present_on_eligible_result():
    fresh = _NOW - timedelta(hours=1)
    w = _worker(last_location_at=fresh, last_activity_source="gps_ping")
    result = get_recent_activity_snapshot(w, as_of=_NOW)
    for key in ("eligible", "activity_at", "activity_age_hours", "activity_source", "reason"):
        assert key in result, f"Missing key: {key}"


def test_recent_activity_all_required_keys_present_on_ineligible_result():
    w = _worker(last_location_at=None)
    result = get_recent_activity_snapshot(w, as_of=_NOW)
    for key in ("eligible", "activity_at", "activity_age_hours", "reason"):
        assert key in result


# ─────────────────────────────────────────────────────────────────────
# F — _worker_trigger_eligibility() paths
# Uses the real function via conftest fixtures
# ─────────────────────────────────────────────────────────────────────

class TestWorkerTriggerEligibility:
    """Integration tests for _worker_trigger_eligibility via conftest factories."""

    def test_eligibility_blocks_when_recent_activity_absent(
        self, make_worker
    ):
        from routers.triggers import _worker_trigger_eligibility

        # conftest factory sentinel always sets last_location_at=utcnow() when None is passed.
        # Use signup_seed source instead — it is always blocked regardless of timestamp.
        w = make_worker(
            city="Bangalore",
            last_activity_source="signup_seed",
        )
        result = _worker_trigger_eligibility(w, "Bangalore", "Heavy Rainfall")
        assert result["eligible"] is False
        assert result["recent_activity_valid"] is False
        assert "recent_activity_reason" in result

    def test_eligibility_blocks_when_activity_is_signup_seed_only(
        self, make_worker
    ):
        from routers.triggers import _worker_trigger_eligibility

        w = make_worker(
            city="Bangalore",
            last_location_at=datetime.utcnow() - timedelta(hours=1),
            last_activity_source="signup_seed",
        )
        result = _worker_trigger_eligibility(w, "Bangalore", "Heavy Rainfall")
        assert result["eligible"] is False
        assert result["recent_activity_valid"] is False

    def test_eligibility_blocks_on_platform_mismatch(self, make_worker):
        from routers.triggers import _worker_trigger_eligibility

        w = make_worker(
            city="Bangalore",
            platform="Swiggy",
            last_location_at=datetime.utcnow(),
            last_activity_source="gps_ping",
        )
        result = _worker_trigger_eligibility(
            w, "Bangalore", "Platform Outage", platform="Blinkit"
        )
        assert result["eligible"] is False
        assert "platform" in result["reason"].lower()

    def test_eligibility_allows_worker_with_fresh_gps_in_matching_city(
        self, make_worker
    ):
        from routers.triggers import _worker_trigger_eligibility

        w = make_worker(
            city="Bangalore",
            platform="Blinkit",
            last_location_at=datetime.utcnow(),
            last_activity_source="gps_ping",
        )
        result = _worker_trigger_eligibility(w, "Bangalore", "Heavy Rainfall")
        assert result["eligible"] is True
        assert result["recent_activity_valid"] is True

    def test_eligibility_propagates_recent_activity_fields_on_success(
        self, make_worker
    ):
        from routers.triggers import _worker_trigger_eligibility

        activity_time = datetime.utcnow() - timedelta(hours=3)
        w = make_worker(
            city="Bangalore",
            last_location_at=activity_time,
            last_activity_source="session_ping",
        )
        result = _worker_trigger_eligibility(w, "Bangalore", "Heavy Rainfall")
        assert result["eligible"] is True
        assert result["recent_activity_valid"] is True
        assert result["recent_activity_at"] == activity_time
        assert result["recent_activity_age_hours"] == pytest.approx(3.0, abs=0.1)

    def test_eligibility_propagates_recent_activity_fields_on_block(
        self, make_worker
    ):
        from routers.triggers import _worker_trigger_eligibility

        # Use signup_seed (valid timestamp but invalid source) → blocked
        w = make_worker(
            city="Bangalore",
            last_location_at=datetime.utcnow() - timedelta(hours=1),
            last_activity_source="signup_seed",
        )
        result = _worker_trigger_eligibility(w, "Bangalore", "Heavy Rainfall")
        assert result["eligible"] is False
        # Even on block, all downstream consumers get activity metadata
        assert "recent_activity_valid" in result
        assert "recent_activity_at" in result
        assert "recent_activity_age_hours" in result
        assert "recent_activity_reason" in result

    def test_eligibility_blocks_worker_in_wrong_city(self, make_worker):
        from routers.triggers import _worker_trigger_eligibility

        w = make_worker(
            city="Chennai",
            last_location_at=datetime.utcnow(),
            last_activity_source="session_ping",
        )
        result = _worker_trigger_eligibility(w, "Mumbai", "Heavy Rainfall")
        assert result["eligible"] is False


# ─────────────────────────────────────────────────────────────────────
# G — parametrize table: source → eligibility
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("source", "hours_old", "expect_eligible"),
    [
        ("gps_ping",      1,   True),
        ("gps_ping",      24,  True),
        ("gps_ping",      47,  True),
        ("gps_ping",      49,  False),    # beyond 48h window
        ("session_ping",  1,   True),
        # session_ping at 48h: staleness_ratio=1.0, base=0.7, penalty=0.3 → confidence=0.4 < 0.55 → REVIEW_REQUIRED
        ("session_ping",  48,  False),
        ("session_ping",  49,  False),
        ("signup_seed",   1,   False),    # always REVIEW_REQUIRED regardless of freshness
        ("signup_seed",   24,  False),
        (None,            1,   False),    # None source always REVIEW_REQUIRED
        ("unknown_src",   1,   False),
    ],
)
def test_recent_activity_source_and_age_parametrize(source, hours_old, expect_eligible):
    activity_time = _NOW - timedelta(hours=hours_old)
    w = _worker(last_location_at=activity_time, last_activity_source=source)
    result = get_recent_activity_snapshot(w, as_of=_NOW)
    assert result["eligible"] is expect_eligible, (
        f"source={source!r}, hours_old={hours_old} → "
        f"expected eligible={expect_eligible}, got {result['eligible']}. "
        f"reason: {result['reason']}"
    )
