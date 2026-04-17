"""
backend/tests/unit/test_cooling_off_service.py
================================================
Tests for services/cooling_off.py:
  - get_cooling_off_hours()
  - evaluate_cooling_off()
  - policy_cooling_off_status()

All tests are pure-Python: no DB, no HTTP.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from services.cooling_off import (
    COOLING_OFF_HOURS_NEW,
    COOLING_OFF_HOURS_RENEWAL,
    COOLING_OFF_HOURS_SPONSOR,
    RC_COOLING_OFF_ACTIVE,
    RC_COOLING_OFF_CLEARED,
    RC_COOLING_OFF_BYPASSED,
    evaluate_cooling_off,
    get_cooling_off_hours,
    policy_cooling_off_status,
)

_NOW = datetime(2026, 4, 17, 12, 0, 0)


def _start(hours_ago: float) -> datetime:
    return _NOW - timedelta(hours=hours_ago)


# ─────────────────────────────────────────────────────────────────
# A — get_cooling_off_hours()
# ─────────────────────────────────────────────────────────────────

class TestGetCoolingOffHours:
    def test_new_enrollment_returns_24(self):
        assert get_cooling_off_hours() == 24

    def test_renewal_returns_0(self):
        assert get_cooling_off_hours(is_renewal=True) == 0

    def test_sponsor_returns_0(self):
        assert get_cooling_off_hours(is_sponsor=True) == 0

    def test_is_renewal_takes_precedence_over_sponsor(self):
        # both set to True → still 0
        assert get_cooling_off_hours(is_renewal=True, is_sponsor=True) == 0

    def test_neither_flag_returns_new_enrollment_hours(self):
        assert get_cooling_off_hours(is_renewal=False, is_sponsor=False) == COOLING_OFF_HOURS_NEW


# ─────────────────────────────────────────────────────────────────
# B — evaluate_cooling_off() — blocked cases
# ─────────────────────────────────────────────────────────────────

class TestEvaluateCoolingOffBlocked:
    def test_brand_new_policy_is_blocked(self):
        result = evaluate_cooling_off(_start(0.0), now=_NOW)
        assert result["eligible"] is False
        assert result["reason_code"] == RC_COOLING_OFF_ACTIVE

    def test_1h_old_policy_is_blocked(self):
        result = evaluate_cooling_off(_start(1.0), now=_NOW)
        assert result["eligible"] is False

    def test_12h_old_policy_is_blocked(self):
        result = evaluate_cooling_off(_start(12.0), now=_NOW)
        assert result["eligible"] is False

    def test_23h59m_policy_is_blocked(self):
        result = evaluate_cooling_off(_start(23 + 59 / 60), now=_NOW)
        assert result["eligible"] is False

    def test_reason_contains_hours_remaining(self):
        result = evaluate_cooling_off(_start(6.0), now=_NOW)
        # 24 - 6 = 18h remaining
        assert "18.0" in result["reason"]

    def test_hours_remaining_is_correct(self):
        result = evaluate_cooling_off(_start(6.0), now=_NOW)
        assert result["hours_remaining"] == pytest.approx(18.0, abs=0.05)

    def test_hours_elapsed_is_correct(self):
        result = evaluate_cooling_off(_start(10.0), now=_NOW)
        assert result["hours_elapsed"] == pytest.approx(10.0, abs=0.05)

    def test_eligible_at_is_24h_after_start(self):
        start = _start(5.0)
        result = evaluate_cooling_off(start, now=_NOW)
        assert result["eligible_at"] == start + timedelta(hours=24)


# ─────────────────────────────────────────────────────────────────
# C — evaluate_cooling_off() — cleared cases
# ─────────────────────────────────────────────────────────────────

class TestEvaluateCoolingOffCleared:
    def test_exactly_24h_old_is_eligible(self):
        result = evaluate_cooling_off(_start(24.0), now=_NOW)
        assert result["eligible"] is True
        assert result["reason_code"] == RC_COOLING_OFF_CLEARED

    def test_25h_old_is_eligible(self):
        result = evaluate_cooling_off(_start(25.0), now=_NOW)
        assert result["eligible"] is True

    def test_week_old_is_eligible(self):
        result = evaluate_cooling_off(_start(7 * 24), now=_NOW)
        assert result["eligible"] is True

    def test_hours_remaining_is_zero_when_cleared(self):
        result = evaluate_cooling_off(_start(25.0), now=_NOW)
        assert result["hours_remaining"] == 0.0


# ─────────────────────────────────────────────────────────────────
# D — evaluate_cooling_off() — bypass paths
# ─────────────────────────────────────────────────────────────────

class TestEvaluateCoolingOffBypass:
    def test_bypass_gate_overrides_for_new_policy(self):
        result = evaluate_cooling_off(_start(0.1), bypass_gate=True, now=_NOW)
        assert result["eligible"] is True
        assert result["reason_code"] == RC_COOLING_OFF_BYPASSED

    def test_is_simulated_overrides_for_new_policy(self):
        result = evaluate_cooling_off(_start(0.5), is_simulated=True, now=_NOW)
        assert result["eligible"] is True
        assert result["reason_code"] == RC_COOLING_OFF_BYPASSED

    def test_bypass_and_simulated_both_give_eligible(self):
        result = evaluate_cooling_off(
            _start(1.0), bypass_gate=True, is_simulated=True, now=_NOW
        )
        assert result["eligible"] is True


# ─────────────────────────────────────────────────────────────────
# E — evaluate_cooling_off() — renewal / sponsor
# ─────────────────────────────────────────────────────────────────

class TestEvaluateCoolingOffRenewal:
    def test_renewal_is_immediately_eligible(self):
        result = evaluate_cooling_off(_start(0.0), is_renewal=True, now=_NOW)
        assert result["eligible"] is True
        assert result["cooling_off_hours"] == 0
        assert result["hours_remaining"] == 0.0

    def test_sponsor_is_immediately_eligible(self):
        result = evaluate_cooling_off(_start(0.0), is_sponsor=True, now=_NOW)
        assert result["eligible"] is True
        assert result["cooling_off_hours"] == 0

    def test_renewal_eligible_at_equals_start_date(self):
        start = _start(0.0)
        result = evaluate_cooling_off(start, is_renewal=True, now=_NOW)
        assert result["eligible_at"] == start


# ─────────────────────────────────────────────────────────────────
# F — policy_cooling_off_status()
# ─────────────────────────────────────────────────────────────────

class TestPolicyCoolingOffStatus:
    def test_new_policy_is_in_cooling_off(self):
        status = policy_cooling_off_status(_start(2.0), now=_NOW)
        assert status["in_cooling_off"] is True
        assert status["hours_remaining"] == pytest.approx(22.0, abs=0.1)

    def test_cleared_policy_not_in_cooling_off(self):
        status = policy_cooling_off_status(_start(25.0), now=_NOW)
        assert status["in_cooling_off"] is False
        assert status["hours_remaining"] is None

    def test_renewal_never_in_cooling_off(self):
        status = policy_cooling_off_status(_start(0.0), is_renewal=True, now=_NOW)
        assert status["in_cooling_off"] is False
        assert status["hours_remaining"] is None

    def test_eligible_at_is_start_plus_cooling_off_hours(self):
        start = _start(5.0)
        status = policy_cooling_off_status(start, now=_NOW)
        assert status["eligible_at"] == start + timedelta(hours=24)

    def test_hours_remaining_decreases_with_age(self):
        s1 = policy_cooling_off_status(_start(6.0), now=_NOW)
        s2 = policy_cooling_off_status(_start(12.0), now=_NOW)
        assert s1["hours_remaining"] > s2["hours_remaining"]


# ─────────────────────────────────────────────────────────────────
# G — integration: evaluate_cooling_off wired correctly into triggers
# ─────────────────────────────────────────────────────────────────

def test_cooling_off_integration_new_policy_blocked(
    make_worker, make_policy, make_trigger, test_db
):
    """New policy 1h old + non-simulated trigger = 0 claims."""
    from models import Claim, PolicyStatus
    from routers.triggers import _auto_generate_claims

    worker = make_worker(city="Bangalore")
    make_policy(
        worker=worker,
        status=PolicyStatus.ACTIVE,
        start_date=_NOW - timedelta(hours=1),
    )
    trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")

    _auto_generate_claims(
        event_id=trigger.id,
        city="Bangalore",
        trigger_type="Heavy Rainfall",
        db=test_db,
        is_simulated=False,
        bypass_gate=False,
    )

    claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
    assert len(claims) == 0


def test_cooling_off_integration_renewal_policy_not_blocked(
    make_worker, make_policy, make_trigger, test_db
):
    """Renewal policy (is_renewal=True, 0h old) is immediately eligible."""
    from models import Claim, PolicyStatus
    from routers.triggers import _auto_generate_claims

    worker = make_worker(city="Bangalore")
    policy = make_policy(
        worker=worker,
        status=PolicyStatus.ACTIVE,
        start_date=_NOW - timedelta(minutes=5),
    )
    policy.is_renewal = True
    test_db.commit()

    trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")

    _auto_generate_claims(
        event_id=trigger.id,
        city="Bangalore",
        trigger_type="Heavy Rainfall",
        db=test_db,
        is_simulated=False,
        bypass_gate=False,
    )

    claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
    assert len(claims) >= 1, "Renewal policy should not be blocked by cooling-off gate"


def test_cooling_off_integration_claim_stores_audit_fields(
    make_worker, make_policy, make_trigger, test_db
):
    """Claims created after cooling-off sets cooling_off_cleared=True + stores age."""
    from datetime import datetime as _dt
    from models import Claim, PolicyStatus
    from routers.triggers import _auto_generate_claims

    worker = make_worker(city="Bangalore")
    make_policy(
        worker=worker,
        status=PolicyStatus.ACTIVE,
        start_date=_dt.utcnow() - timedelta(hours=25),
    )
    trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")

    _auto_generate_claims(
        event_id=trigger.id,
        city="Bangalore",
        trigger_type="Heavy Rainfall",
        db=test_db,
        is_simulated=False,
        bypass_gate=False,
    )

    claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
    assert len(claims) >= 1
    claim = claims[0]
    assert claim.cooling_off_cleared is True
    assert claim.cooling_off_hours_at_claim is not None
    assert claim.cooling_off_hours_at_claim >= 25.0

