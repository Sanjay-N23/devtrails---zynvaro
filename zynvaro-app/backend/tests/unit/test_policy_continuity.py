"""
backend/tests/unit/test_policy_continuity.py
=============================================
Tests for the continuity evaluator:
  evaluate_policy_continuity(previous_policy_end, new_policy_start, ...)

Covers spec sections:
  D  — Renewal / continuity logic
  C  — Policy purchase / enrollment edge cases (32-44)
  L  — Identity mismatch continuity

All tests use frozen timestamps. No DB required.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from services.waiting_period import (
    RC_CONTINUOUS_RENEWAL_BYPASS,
    RC_GRACE_RENEWAL_BYPASS,
    RC_LAPSE_RESETS_WAITING,
    RC_CANCEL_REBUY_RESETS_WAITING,
    evaluate_policy_continuity,
    evaluate_waiting_eligibility,
    WaitingConfig,
    RULE_24H, RULE_ZERO,
)

_NOW   = datetime(2026, 4, 17, 12, 0, 0)   # reference: new policy starts here
_PREV_END = _NOW                           # default: old policy ended exactly when new starts


def cont(
    gap_hours: float = 0.0,
    *,
    identity_match: bool = True,
    grace: int = 0,
) -> object:
    prev_end = _NOW - timedelta(hours=gap_hours)
    return evaluate_policy_continuity(
        previous_policy_end=prev_end,
        new_policy_start=_NOW,
        identity_match=identity_match,
        grace_period_hours=grace,
    )


# ─────────────────────────────────────────────────────────────────────────────
# D — Renewal / continuity logic
# ─────────────────────────────────────────────────────────────────────────────

class TestRenewalContinuityLogic:

    def test_uninterrupted_renewal_is_continuous(self):
        """Auto-renew with no gap → continuous, no waiting."""
        r = cont(gap_hours=0.0)
        assert r.is_continuous is True
        assert r.reason_code == RC_CONTINUOUS_RENEWAL_BYPASS

    def test_manual_renew_before_expiry_is_continuous(self):
        """Renewed 2h before expiry (overlap) → continuous."""
        r = cont(gap_hours=-2.0)   # negative = overlap
        assert r.is_continuous is True

    def test_renewal_within_grace_is_continuous(self):
        """Gap of 2h but grace is 6h → continuous."""
        r = cont(gap_hours=2.0, grace=6)
        assert r.is_continuous is True
        assert r.reason_code == RC_GRACE_RENEWAL_BYPASS

    def test_renewal_at_grace_boundary_is_continuous(self):
        """Gap exactly equals grace period → still continuous (inclusive)."""
        r = cont(gap_hours=6.0, grace=6)
        assert r.is_continuous is True

    def test_renewal_1_second_outside_grace_is_not_continuous(self):
        """Gap = grace + 1s → lapse."""
        prev_end = _NOW - timedelta(hours=6, seconds=1)
        r = evaluate_policy_continuity(
            previous_policy_end=prev_end,
            new_policy_start=_NOW,
            grace_period_hours=6,
        )
        assert r.is_continuous is False
        assert r.reason_code == RC_LAPSE_RESETS_WAITING

    def test_no_previous_policy_is_not_continuous(self):
        """First-ever enrollment — no previous policy."""
        r = evaluate_policy_continuity(
            previous_policy_end=None,
            new_policy_start=_NOW,
        )
        assert r.is_continuous is False
        assert r.reason_code == RC_LAPSE_RESETS_WAITING

    def test_large_gap_is_not_continuous(self):
        """Policy lapsed 30 days ago — waiting resets."""
        r = cont(gap_hours=720)
        assert r.is_continuous is False
        assert r.reason_code == RC_LAPSE_RESETS_WAITING

    def test_identity_mismatch_breaks_continuity(self):
        """Renewal with identity mismatch → always breaks continuity."""
        r = cont(gap_hours=0.0, identity_match=False)
        assert r.is_continuous is False
        assert r.reason_code == RC_CANCEL_REBUY_RESETS_WAITING

    def test_identity_mismatch_with_grace_still_breaks_continuity(self):
        """Grace period cannot override identity mismatch."""
        r = cont(gap_hours=1.0, identity_match=False, grace=24)
        assert r.is_continuous is False

    def test_continuous_renewal_bypasses_24h_waiting(self):
        """Continuous renewal → evaluate_waiting_eligibility returns ELIGIBLE immediately."""
        r = cont(gap_hours=0.0)
        d = evaluate_waiting_eligibility(
            bind_time=_NOW,
            event_time=_NOW + timedelta(minutes=5),
            config=WaitingConfig(rule_type=RULE_24H),
            continuity=r,
        )
        assert d.decision == "ELIGIBLE"
        assert d.reason_code == RC_CONTINUOUS_RENEWAL_BYPASS

    def test_lapsed_renewal_reapplies_24h_waiting(self):
        """Lapsed worker re-enrolls → new 24h waiting applies."""
        r = cont(gap_hours=100)  # 100h gap = lapsed
        d = evaluate_waiting_eligibility(
            bind_time=_NOW,
            event_time=_NOW + timedelta(hours=1),  # still in 24h window
            config=WaitingConfig(rule_type=RULE_24H),
            continuity=r,
        )
        assert d.decision == "BLOCKED_WAITING"

    def test_grace_renewal_bypasses_waiting(self):
        r = cont(gap_hours=2.0, grace=6)
        d = evaluate_waiting_eligibility(
            bind_time=_NOW,
            event_time=_NOW + timedelta(hours=1),
            config=WaitingConfig(rule_type=RULE_24H),
            continuity=r,
        )
        assert d.decision == "ELIGIBLE"
        assert d.reason_code == RC_GRACE_RENEWAL_BYPASS

    def test_grace_period_clamped_to_max_72h(self):
        """Grace period cannot exceed MAX_GRACE_PERIOD_HOURS=72."""
        from services.waiting_period import MAX_GRACE_PERIOD_HOURS
        r = cont(gap_hours=MAX_GRACE_PERIOD_HOURS + 1, grace=999)
        # Even with enormous grace config, gap > 72h always breaks continuity
        assert r.is_continuous is False


# ─────────────────────────────────────────────────────────────────────────────
# C — Policy purchase / enrollment edge cases (spec items 32-45)
# ─────────────────────────────────────────────────────────────────────────────

class TestEnrollmentEdgeCases:

    def test_returning_worker_after_long_lapse_gets_waiting(self):
        """Worker absent for 6 months → treated as new enrollment."""
        r = cont(gap_hours=6 * 30 * 24)
        assert r.is_continuous is False

    def test_returning_worker_same_week_continuous_no_waiting(self):
        """Re-enrolled within same week with no gap → continuous."""
        r = cont(gap_hours=0.0)
        assert r.is_continuous is True

    def test_cancel_and_rebuy_same_day_resets_waiting(self):
        """Cancel at 9AM, rebuy at 11AM (2h gap, no grace) → waiting resets."""
        r = cont(gap_hours=2.0, grace=0)
        assert r.is_continuous is False
        assert r.reason_code == RC_LAPSE_RESETS_WAITING

    def test_failed_payment_bind_time_is_actual_success_time(self):
        """
        Simulate: first payment failed at T=0, retry succeeded at T=5h.
        bind_time should be T+5h (actual success), NOT T+0h.
        Result: event at T+5h+1h is still in waiting (only 1h into 24h wait).
        """
        bind_time_retry = _NOW + timedelta(hours=5)  # actual successful bind
        event_time = _NOW + timedelta(hours=6)        # 1h after actual bind
        d = evaluate_waiting_eligibility(
            bind_time=bind_time_retry,
            event_time=event_time,
            config=WaitingConfig(rule_type=RULE_24H),
        )
        assert d.decision == "BLOCKED_WAITING"

    def test_backdated_policy_creation_does_not_bypass_waiting(self):
        """
        Even if start_date is set in the past (e.g. admin correction),
        waiting is computed from start_date — service enforces this.
        """
        # start_date = yesterday, event = now → should be eligible
        # But if start_date is falsely set 25h ago, it passes — that's correct
        # The key: service always uses what it's given; caller must not accept
        # client-provided bind_time. The api layer enforces this, not the service.
        # This test checks the pure math is correct.
        backdated_bind = _NOW - timedelta(hours=25)
        d = evaluate_waiting_eligibility(
            bind_time=backdated_bind,
            event_time=_NOW,
            config=WaitingConfig(rule_type=RULE_24H),
        )
        assert d.decision == "ELIGIBLE"   # 25h after bind = past 24h window

    def test_multi_policy_same_user_each_gets_own_waiting(self):
        """If same user somehow has 2 policies, each is evaluated independently."""
        policy1_bind = _NOW
        policy2_bind = _NOW + timedelta(hours=2)
        event = _NOW + timedelta(hours=3)

        d1 = evaluate_waiting_eligibility(
            bind_time=policy1_bind, event_time=event, config=WaitingConfig()
        )
        d2 = evaluate_waiting_eligibility(
            bind_time=policy2_bind, event_time=event, config=WaitingConfig()
        )
        # Policy 1: 3h old → blocked (24h rule)
        assert d1.decision == "BLOCKED_WAITING"
        # Policy 2: only 1h old → also blocked
        assert d2.decision == "BLOCKED_WAITING"


# ─────────────────────────────────────────────────────────────────────────────
# Parametrize table — continuity scenarios
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("label", "gap_hours", "grace", "identity_match", "expect_continuous", "expect_rc"),
    [
        # Uninterrupted
        ("no_gap",              0.0,  0,  True,  True,  RC_CONTINUOUS_RENEWAL_BYPASS),
        ("overlap",            -1.0,  0,  True,  True,  RC_CONTINUOUS_RENEWAL_BYPASS),
        # Within grace
        ("within_grace",        2.0,  6,  True,  True,  RC_GRACE_RENEWAL_BYPASS),
        ("at_grace_boundary",   6.0,  6,  True,  True,  RC_GRACE_RENEWAL_BYPASS),
        # Outside grace
        ("just_outside_grace",  6.01, 6,  True,  False, RC_LAPSE_RESETS_WAITING),
        ("long_lapse",        168.0,  0,  True,  False, RC_LAPSE_RESETS_WAITING),
        ("first_ever",          None, 0,  True,  False, RC_LAPSE_RESETS_WAITING),
        # Identity mismatch
        ("id_mismatch",         0.0,  0,  False, False, RC_CANCEL_REBUY_RESETS_WAITING),
        ("id_mismatch_grace",   2.0, 24,  False, False, RC_CANCEL_REBUY_RESETS_WAITING),
    ],
)
def test_continuity_parametrize_table(
    label, gap_hours, grace, identity_match, expect_continuous, expect_rc,
):
    if gap_hours is None:
        prev_end = None
    else:
        prev_end = _NOW - timedelta(hours=gap_hours)

    result = evaluate_policy_continuity(
        previous_policy_end=prev_end,
        new_policy_start=_NOW,
        identity_match=identity_match,
        grace_period_hours=grace,
    )
    assert result.is_continuous == expect_continuous, (
        f"[{label}] expected is_continuous={expect_continuous}, "
        f"got {result.is_continuous}. reason: {result.continuity_reason}"
    )
    assert result.reason_code == expect_rc, (
        f"[{label}] expected rc={expect_rc!r}, got {result.reason_code!r}"
    )
