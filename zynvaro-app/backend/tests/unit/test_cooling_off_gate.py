"""
backend/tests/unit/test_cooling_off_gate.py
============================================
Tests for Feature 5: Waiting-period / Cooling-off logic.

Proves that:
  1. Policies younger than 24h are blocked from receiving auto-generated claims.
  2. Policies older than 24h pass the gate correctly.
  3. Simulated events always bypass the gate (demo privilege).
  4. bypass_gate=True explicitly skips the gate.
  5. Boundary: policy exactly 24h old passes.
  6. Boundary: policy 23h59m old is blocked.
"""
from __future__ import annotations

import sys
import os
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

# ── path bootstrap identical to the main conftest ───────────────────
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)


# ── Pure helper that mirrors the gate logic inside _auto_generate_claims ─
# We extract it so it can be tested without spinning up FastAPI or the DB.

COOLING_OFF_HOURS = 24

def cooling_off_eligible(
    policy_start_date: datetime,
    is_simulated: bool = False,
    bypass_gate: bool = False,
    now: datetime | None = None,
) -> tuple[bool, str]:
    """
    Returns (is_eligible, reason).
    Mirrors the exact gate logic in _auto_generate_claims.
    """
    if bypass_gate or is_simulated:
        return True, "Gate bypassed."

    now = now or datetime.utcnow()
    policy_age_hours = (now - policy_start_date).total_seconds() / 3600

    if policy_age_hours < COOLING_OFF_HOURS:
        hours_remaining = round(COOLING_OFF_HOURS - policy_age_hours, 1)
        return False, (
            f"Policy is only {policy_age_hours:.1f}h old. "
            f"{hours_remaining}h remaining in cooling-off period."
        )
    return True, "Policy has cleared the 24-hour cooling-off period."


# ─────────────────────────────────────────────────────────────────────
# Core gate tests
# ─────────────────────────────────────────────────────────────────────

class TestCoolingOffGate:
    _NOW = datetime(2026, 4, 17, 12, 0, 0)  # fixed reference point

    def _start(self, hours_ago: float) -> datetime:
        return self._NOW - timedelta(hours=hours_ago)

    def test_policy_less_than_24h_old_is_blocked(self):
        eligible, reason = cooling_off_eligible(
            self._start(2.0), now=self._NOW
        )
        assert eligible is False
        assert "cooling-off" in reason.lower()

    def test_policy_more_than_24h_old_passes_gate(self):
        eligible, reason = cooling_off_eligible(
            self._start(25.0), now=self._NOW
        )
        assert eligible is True

    def test_policy_exactly_24h_old_passes_gate(self):
        """Boundary: policy started exactly 24 hours ago → eligible."""
        eligible, _ = cooling_off_eligible(
            self._start(24.0), now=self._NOW
        )
        assert eligible is True

    def test_policy_23h59m_old_is_still_blocked(self):
        """1 minute short of 24h → still blocked."""
        eligible, reason = cooling_off_eligible(
            self._start(23 + 59 / 60), now=self._NOW
        )
        assert eligible is False
        assert "0.0" in reason or "cooling-off" in reason.lower()

    def test_simulated_event_bypasses_gate_regardless_of_policy_age(self):
        """is_simulated=True must always bypass, even for brand-new policies."""
        eligible, _ = cooling_off_eligible(
            self._start(0.1),    # 6 minutes old
            is_simulated=True,
            now=self._NOW,
        )
        assert eligible is True

    def test_bypass_gate_flag_bypasses_gate(self):
        """bypass_gate=True overrides the gate."""
        eligible, _ = cooling_off_eligible(
            self._start(0.5),
            bypass_gate=True,
            now=self._NOW,
        )
        assert eligible is True

    def test_bypass_gate_false_and_not_simulated_enforces_gate(self):
        eligible, _ = cooling_off_eligible(
            self._start(1.0),
            is_simulated=False,
            bypass_gate=False,
            now=self._NOW,
        )
        assert eligible is False

    def test_hours_remaining_in_reason_message(self):
        eligible, reason = cooling_off_eligible(
            self._start(6.0), now=self._NOW
        )
        assert eligible is False
        # 24 - 6 = 18 hours remaining
        assert "18.0" in reason

    def test_new_policy_zero_age_is_blocked(self):
        eligible, _ = cooling_off_eligible(
            self._NOW,   # started right now
            now=self._NOW,
        )
        assert eligible is False

    def test_week_old_policy_always_passes(self):
        eligible, _ = cooling_off_eligible(
            self._start(7 * 24), now=self._NOW
        )
        assert eligible is True


# ─────────────────────────────────────────────────────────────────────
# Boundary parametrize table
# ─────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("hours_ago", "is_simulated", "bypass", "expect_eligible"),
    [
        # strictly inside cooling-off → blocked
        (0.0,   False, False, False),
        (0.1,   False, False, False),
        (12.0,  False, False, False),
        (23.9,  False, False, False),
        # exactly at boundary → eligible
        (24.0,  False, False, True),
        # well past boundary → eligible
        (25.0,  False, False, True),
        (48.0,  False, False, True),
        (168.0, False, False, True),
        # simulated overrides
        (0.5,   True,  False, True),
        (12.0,  True,  False, True),
        # bypass_gate overrides
        (1.0,   False, True,  True),
        (23.5,  False, True,  True),
    ],
)
def test_cooling_off_parametrize_table(hours_ago, is_simulated, bypass, expect_eligible):
    _NOW = datetime(2026, 4, 17, 12, 0, 0)
    start = _NOW - timedelta(hours=hours_ago)
    eligible, _ = cooling_off_eligible(
        start,
        is_simulated=is_simulated,
        bypass_gate=bypass,
        now=_NOW,
    )
    assert eligible is expect_eligible


# ─────────────────────────────────────────────────────────────────────
# Integration: verify _auto_generate_claims skips young policies
# ─────────────────────────────────────────────────────────────────────

def test_auto_generate_claims_skips_policy_under_cooling_off(
    make_worker, make_policy, make_trigger, test_db, authed_client
):
    """
    End-to-end via DB: policy is 1h old, non-simulated trigger fires.
    _auto_generate_claims should create ZERO claims.
    """
    from models import Claim, PolicyStatus
    from routers.triggers import _auto_generate_claims

    worker = make_worker(city="Bangalore")
    # Start date 1 hour ago → inside 24-hour cooling-off
    policy = make_policy(
        worker=worker,
        status=PolicyStatus.ACTIVE,
        start_date=datetime.utcnow() - timedelta(hours=1),
    )
    trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")

    # Run the claim generator synchronously (bypass=False by default)
    _auto_generate_claims(
        event_id=trigger.id,
        city="Bangalore",
        trigger_type="Heavy Rainfall",
        db=test_db,
        is_simulated=False,    # <- real event
        bypass_gate=False,     # <- gate active
    )

    claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
    assert len(claims) == 0, (
        f"Expected 0 claims during cooling-off period, got {len(claims)}"
    )


def test_auto_generate_claims_allows_policy_over_cooling_off(
    make_worker, make_policy, make_trigger, test_db
):
    """
    Policy is 25 hours old → beyond cooling-off → claims are created.
    """
    from models import Claim, PolicyStatus
    from routers.triggers import _auto_generate_claims

    worker = make_worker(city="Bangalore")
    policy = make_policy(
        worker=worker,
        status=PolicyStatus.ACTIVE,
        start_date=datetime.utcnow() - timedelta(hours=25),
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
    assert len(claims) >= 1, "Policy older than 24h should have received a claim."


def test_auto_generate_claims_bypass_creates_claims_for_young_policy(
    make_worker, make_policy, make_trigger, test_db
):
    """
    bypass_gate=True: even a brand-new policy gets a claim (demo simulation path).
    """
    from models import Claim, PolicyStatus
    from routers.triggers import _auto_generate_claims

    worker = make_worker(city="Bangalore")
    policy = make_policy(
        worker=worker,
        status=PolicyStatus.ACTIVE,
        start_date=datetime.utcnow() - timedelta(minutes=5),  # 5 min old
    )
    trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")

    _auto_generate_claims(
        event_id=trigger.id,
        city="Bangalore",
        trigger_type="Heavy Rainfall",
        db=test_db,
        is_simulated=False,
        bypass_gate=True,      # demo override
    )

    claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
    assert len(claims) >= 1, "bypass_gate=True should create claims even for brand-new policies."
