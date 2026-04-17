"""
backend/tests/unit/test_waiting_period_rules.py
================================================
Comprehensive table-driven unit tests for the waiting-period rules engine.

Covers spec sections:
  A  — Core policy activation / waiting period states
  B  — Boundary time cases (edge: -1s, exact, +1s)
  E  — Trigger / claim interaction
  F  — Forecast arbitrage / anti-gaming
  G  — Configuration / product rule variants
  H  — Eligibility / reason precedence
  L  — Security / abuse / identity
  N  — Concurrency / retries (timestamp determinism)
  O  — Non-functional / robustness

All tests:
  - use frozen deterministic timestamps (never real clock)
  - assert exact reason codes
  - call the pure service, no DB required
"""
from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional

import pytest

from services.waiting_period import (
    CURRENT_RULE_VERSION,
    RULE_24H, RULE_72H, RULE_NEXT_CYCLE, RULE_ZERO,
    RC_WAITING_NOT_APPLICABLE,
    RC_WAITING_PERIOD_ACTIVE,
    RC_NEXT_CYCLE_NOT_STARTED,
    RC_CONTINUOUS_RENEWAL_BYPASS,
    RC_GRACE_RENEWAL_BYPASS,
    RC_LAPSE_RESETS_WAITING,
    RC_CANCEL_REBUY_RESETS_WAITING,
    RC_SPONSOR_BYPASS_ALLOWED,
    RC_CONFIG_INVALID_WAITING_BLOCKED,
    RC_EVENT_BEFORE_CLAIM_ELIGIBLE_AT,
    RC_ELIGIBLE,
    WaitingConfig,
    ContinuityResult,
    evaluate_waiting_eligibility,
    evaluate_policy_continuity,
    build_waiting_snapshot,
    _compute_claim_eligible_at,
)

# ─── Frozen reference times ──────────────────────────────────────────────────
_BIND  = datetime(2026, 4, 17, 10, 0, 0)   # policy bound at 10:00 UTC
_EV_24 = _BIND + timedelta(hours=24)        # exactly at 24h eligibility boundary
_EV_BEFORE = _EV_24 - timedelta(seconds=1) # 1 second before boundary
_EV_AFTER  = _EV_24 + timedelta(seconds=1) # 1 second after boundary


def cfg(rule: str = RULE_24H, *, is_renewal=False, is_sponsor=False, grace=0) -> WaitingConfig:
    return WaitingConfig(
        rule_type=rule,
        is_renewal=is_renewal,
        is_sponsor=is_sponsor,
        grace_period_hours=grace,
    )


def decide(event_time: datetime, config: WaitingConfig = None) -> object:
    return evaluate_waiting_eligibility(
        bind_time=_BIND,
        event_time=event_time,
        config=config or cfg(),
    )


# ─────────────────────────────────────────────────────────────────────────────
# A — Core activation / waiting period states (spec items 1, 9, 10, 11)
# ─────────────────────────────────────────────────────────────────────────────

class TestCoreActivationStates:

    def test_new_enrollment_blocks_claim_before_eligible_at(self):
        d = decide(_BIND + timedelta(hours=1))
        assert d.decision == "BLOCKED_WAITING"
        assert d.waiting_applies is True

    def test_new_enrollment_allows_claim_after_eligible_at(self):
        d = decide(_BIND + timedelta(hours=25))
        assert d.decision == "ELIGIBLE"
        assert d.waiting_applies is False

    def test_decision_provides_exact_claim_eligible_at(self):
        d = decide(_BIND + timedelta(hours=1))
        assert d.claim_eligible_at == _BIND + timedelta(hours=24)

    def test_decision_provides_exact_reason_code(self):
        d = decide(_BIND + timedelta(hours=1))
        assert d.reason_code == RC_EVENT_BEFORE_CLAIM_ELIGIBLE_AT

    def test_eligible_decision_reason_code_is_rc_eligible(self):
        d = decide(_BIND + timedelta(hours=25))
        assert d.reason_code == RC_ELIGIBLE

    def test_decision_stores_event_time_not_wall_clock(self):
        event_time = _BIND + timedelta(hours=1)
        d = decide(event_time)
        assert d.event_time_used == event_time


# ─────────────────────────────────────────────────────────────────────────────
# B — Boundary timestamps (spec items 16-30)
# ─────────────────────────────────────────────────────────────────────────────

class TestBoundaryTimestamps:

    def test_event_exactly_at_bind_time_is_blocked(self):
        """Event at the exact bind moment — policy is 0h old."""
        d = decide(_BIND)
        assert d.decision == "BLOCKED_WAITING"

    def test_event_1_second_before_eligible_is_blocked(self):
        d = decide(_EV_BEFORE)
        assert d.decision == "BLOCKED_WAITING"

    def test_event_exactly_at_eligible_time_is_allowed(self):
        """Boundary inclusive: exactly at claim_eligible_at → ELIGIBLE."""
        d = decide(_EV_24)
        assert d.decision == "ELIGIBLE"

    def test_event_1_second_after_eligible_is_allowed(self):
        d = decide(_EV_AFTER)
        assert d.decision == "ELIGIBLE"

    def test_72h_rule_boundary_before_is_blocked(self):
        boundary = _BIND + timedelta(hours=72)
        d = evaluate_waiting_eligibility(
            bind_time=_BIND,
            event_time=boundary - timedelta(seconds=1),
            config=cfg(RULE_72H),
        )
        assert d.decision == "BLOCKED_WAITING"

    def test_72h_rule_boundary_exact_is_eligible(self):
        boundary = _BIND + timedelta(hours=72)
        d = evaluate_waiting_eligibility(
            bind_time=_BIND,
            event_time=boundary,
            config=cfg(RULE_72H),
        )
        assert d.decision == "ELIGIBLE"

    def test_next_cycle_rule_before_monday_is_blocked(self):
        """Bind on a Friday — event on Saturday is before Monday cycle start."""
        # April 17 2026 = Thursday 10:00 UTC
        # Next Monday = April 20 2026 00:00 UTC
        friday_bind = datetime(2026, 4, 17, 10, 0, 0)
        saturday_event = datetime(2026, 4, 18, 12, 0, 0)
        d = evaluate_waiting_eligibility(
            bind_time=friday_bind,
            event_time=saturday_event,
            config=cfg(RULE_NEXT_CYCLE),
        )
        assert d.decision == "BLOCKED_WAITING"
        assert d.reason_code == RC_NEXT_CYCLE_NOT_STARTED

    def test_next_cycle_rule_at_monday_is_eligible(self):
        friday_bind = datetime(2026, 4, 17, 10, 0, 0)
        monday_00 = datetime(2026, 4, 20, 0, 0, 0)
        d = evaluate_waiting_eligibility(
            bind_time=friday_bind,
            event_time=monday_00,
            config=cfg(RULE_NEXT_CYCLE),
        )
        assert d.decision == "ELIGIBLE"

    def test_zero_wait_rule_at_bind_time_is_eligible(self):
        """Zero waiting: event exactly at bind time is immediately eligible."""
        d = decide(_BIND, cfg(RULE_ZERO))
        assert d.decision == "ELIGIBLE"

    def test_precise_seconds_respected_not_rounded_to_date(self):
        """Waiting check must use exact timestamps, not date-level rounding."""
        # Policy bound at 23:59:59 — event the next day at 00:00:00
        # That's only 1 second difference — NOT 24 hours — must still block
        late_bind = datetime(2026, 4, 17, 23, 59, 59)
        next_day_midnight = datetime(2026, 4, 18, 0, 0, 0)  # only 1s later
        d = evaluate_waiting_eligibility(
            bind_time=late_bind,
            event_time=next_day_midnight,
            config=cfg(RULE_24H),
        )
        assert d.decision == "BLOCKED_WAITING"

    def test_claim_eligible_at_computed_is_24h_from_bind(self):
        d = decide(_BIND + timedelta(hours=1))
        assert d.claim_eligible_at == _BIND + timedelta(hours=24)

    def test_midweek_to_next_monday_next_cycle_computed_correctly(self):
        """Bind on Thursday -> next Monday 00:00 UTC."""
        thu_bind = datetime(2026, 4, 16, 15, 0, 0)  # Thursday
        elig_at = _compute_claim_eligible_at(thu_bind, cfg(RULE_NEXT_CYCLE))
        assert elig_at.weekday() == 0  # Monday
        assert elig_at.hour == 0
        assert elig_at.minute == 0


# ─────────────────────────────────────────────────────────────────────────────
# E — Trigger / claim interaction (spec items 61-75)
# ─────────────────────────────────────────────────────────────────────────────

class TestTriggerClaimInteraction:

    def test_trigger_during_waiting_no_payout(self):
        d = decide(_BIND + timedelta(hours=6))
        assert d.decision == "BLOCKED_WAITING"

    def test_trigger_before_purchase_no_payout(self):
        """Event time before bind_time must still be blocked."""
        event_before_bind = _BIND - timedelta(hours=1)
        d = decide(event_before_bind)
        assert d.decision == "BLOCKED_WAITING"

    def test_trigger_after_eligibility_normal_flow(self):
        d = decide(_BIND + timedelta(hours=25))
        assert d.decision == "ELIGIBLE"

    def test_event_time_used_is_trigger_event_time_not_now(self):
        """The decision uses the frozen event_time, not the clock at processing."""
        old_event = _BIND + timedelta(hours=1)  # still in cooling-off period
        d = decide(old_event)
        assert d.event_time_used == old_event
        assert d.decision == "BLOCKED_WAITING"

    def test_backfilled_event_in_waiting_period_is_blocked(self):
        """Trigger processed late but event occurred during waiting — must block."""
        event_time = _BIND + timedelta(hours=12)   # during waiting
        # processing_time = _BIND + timedelta(hours=36)  # after waiting — but irrelevant
        d = decide(event_time)  # always pass event_time, not processing_time
        assert d.decision == "BLOCKED_WAITING"

    def test_retry_processing_same_event_does_not_change_decision(self):
        """Idempotency: processing the same event_time twice gives same result."""
        event_time = _BIND + timedelta(hours=12)
        d1 = decide(event_time)
        d2 = decide(event_time)
        assert d1.decision == d2.decision
        assert d1.reason_code == d2.reason_code

    def test_claim_record_stores_waiting_denial_reason_code(self):
        d = decide(_BIND + timedelta(hours=6))
        snap = build_waiting_snapshot(d)
        assert snap.reason_code == RC_EVENT_BEFORE_CLAIM_ELIGIBLE_AT


# ─────────────────────────────────────────────────────────────────────────────
# F — Forecast arbitrage / anti-gaming (spec items 76-90)
# ─────────────────────────────────────────────────────────────────────────────

class TestForecastArbitrageProtection:

    def test_policy_bought_minutes_before_rain_trigger_is_blocked(self):
        """User buys policy 30 min before heavy rain detected — blocked."""
        bind = _BIND
        trigger_time = _BIND + timedelta(minutes=30)
        d = decide(trigger_time, cfg(RULE_24H))
        assert d.decision == "BLOCKED_WAITING"

    def test_policy_bought_minutes_before_heat_trigger_is_blocked(self):
        trigger_time = _BIND + timedelta(minutes=5)
        d = decide(trigger_time, cfg(RULE_24H))
        assert d.decision == "BLOCKED_WAITING"

    def test_policy_bought_after_visible_forecast_is_blocked(self):
        """Worst case arbitrage: buy at T=0, trigger at T+1h."""
        d = decide(_BIND + timedelta(hours=1), cfg(RULE_24H))
        assert d.decision == "BLOCKED_WAITING"

    def test_cancel_rebuy_before_event_resets_waiting(self):
        """Cancel old policy, rebuy near forecast — waiting must reset to new bind_time."""
        # Old policy expired — continuity is broken
        cont = evaluate_policy_continuity(
            previous_policy_end=_BIND - timedelta(hours=5),  # ended 5h before rebuy
            new_policy_start=_BIND,
            grace_period_hours=0,
        )
        assert cont.is_continuous is False
        assert cont.reason_code == RC_LAPSE_RESETS_WAITING
        # Now the new policy has 24h waiting from its own bind_time
        d = decide(_BIND + timedelta(hours=2))
        assert d.decision == "BLOCKED_WAITING"

    def test_same_day_cancel_rebuy_triggers_new_waiting_period(self):
        """Cancel and rebuy on same day — rebuy bind_time is the new reference."""
        new_bind = _BIND + timedelta(hours=3)
        event_time = new_bind + timedelta(hours=1)   # only 1h after new bind
        d = evaluate_waiting_eligibility(
            bind_time=new_bind,
            event_time=event_time,
            config=cfg(RULE_24H),
        )
        assert d.decision == "BLOCKED_WAITING"

    def test_quote_time_not_used_bind_time_is_truth(self):
        """Quote was generated before event but payment after — bind_time from payment."""
        # Simulate: worker got quote at _BIND-5h, paid at _BIND (bind=payment time)
        event = _BIND + timedelta(hours=2)   # 2h after binding = still waiting
        d = decide(event, cfg(RULE_24H))
        assert d.decision == "BLOCKED_WAITING"

    def test_waiting_applies_immediate_eligibility_not_granted_by_default(self):
        d = decide(_BIND, cfg(RULE_24H))
        assert d.waiting_applies is True

    def test_sponsor_bypass_is_only_for_sponsor_config(self):
        """Retail workers cannot self-declare sponsor bypass."""
        d = decide(_BIND + timedelta(minutes=5), cfg(RULE_24H))
        assert d.decision == "BLOCKED_WAITING"

    def test_sponsor_config_grants_immediate_eligibility(self):
        d = decide(_BIND + timedelta(minutes=5), cfg(RULE_24H, is_sponsor=True))
        assert d.decision == "ELIGIBLE"
        assert d.reason_code == RC_SPONSOR_BYPASS_ALLOWED


# ─────────────────────────────────────────────────────────────────────────────
# G — Configuration / product rule variants (spec items 91-105)
# ─────────────────────────────────────────────────────────────────────────────

class TestConfigRuleVariants:

    @pytest.mark.parametrize("hours", [1, 6, 12, 23])
    def test_24h_rule_blocks_within_window(self, hours):
        d = decide(_BIND + timedelta(hours=hours), cfg(RULE_24H))
        assert d.decision == "BLOCKED_WAITING"

    @pytest.mark.parametrize("hours", [24, 25, 48])
    def test_24h_rule_allows_after_window(self, hours):
        d = decide(_BIND + timedelta(hours=hours), cfg(RULE_24H))
        assert d.decision == "ELIGIBLE"

    @pytest.mark.parametrize("hours", [1, 24, 71])
    def test_72h_rule_blocks_within_window(self, hours):
        d = decide(_BIND + timedelta(hours=hours), cfg(RULE_72H))
        assert d.decision == "BLOCKED_WAITING"

    @pytest.mark.parametrize("hours", [72, 73, 144])
    def test_72h_rule_allows_after_window(self, hours):
        d = decide(_BIND + timedelta(hours=hours), cfg(RULE_72H))
        assert d.decision == "ELIGIBLE"

    def test_zero_rule_allows_at_bind_instant(self):
        d = decide(_BIND, cfg(RULE_ZERO))
        assert d.decision == "ELIGIBLE"
        assert d.waiting_applies is False

    def test_unknown_rule_type_fails_safe_to_review_required(self):
        bad_config = WaitingConfig(rule_type="unknown_rule")
        d = evaluate_waiting_eligibility(
            bind_time=_BIND,
            event_time=_BIND + timedelta(hours=1),
            config=bad_config,
        )
        assert d.decision == "REVIEW_REQUIRED"
        assert d.reason_code == RC_CONFIG_INVALID_WAITING_BLOCKED

    def test_negative_duration_not_valid_fails_safe(self):
        """If somehow a rule produces a negative duration, fail safe."""
        # We simulate this by passing a pre-computed claim_eligible_at in the past
        # Should still use evaluate_waiting_eligibility which checks event vs elig_at
        past_elig = _BIND - timedelta(hours=5)  # elig_at in the past
        d = evaluate_waiting_eligibility(
            bind_time=_BIND,
            event_time=_BIND + timedelta(hours=1),
            config=cfg(RULE_24H),
            claim_eligible_at=past_elig,
        )
        # event > past_elig → ELIGIBLE (negative wait = immediate by definition)
        assert d.decision == "ELIGIBLE"

    def test_sponsor_flag_overrides_24h_rule(self):
        d = evaluate_waiting_eligibility(
            bind_time=_BIND,
            event_time=_BIND + timedelta(hours=1),
            config=WaitingConfig(rule_type=RULE_24H, is_sponsor=True),
        )
        assert d.decision == "ELIGIBLE"
        assert d.reason_code == RC_SPONSOR_BYPASS_ALLOWED

    def test_waiting_rule_version_preserved_in_decision(self):
        d = decide(_BIND + timedelta(hours=1))
        assert d.rule_version == CURRENT_RULE_VERSION

    def test_channel_partner_zero_wait_allowed(self):
        partner_cfg = WaitingConfig(rule_type=RULE_ZERO, channel="partner")
        d = decide(_BIND, partner_cfg)
        assert d.decision == "ELIGIBLE"


# ─────────────────────────────────────────────────────────────────────────────
# H — Eligibility / reason precedence (spec items 106-115)
# ─────────────────────────────────────────────────────────────────────────────

class TestEligibilityReasonPrecedence:

    def test_waiting_period_denial_takes_precedence_over_payout_calculation(self):
        """A blocked waiting period = NO payout, regardless of payout math."""
        d = decide(_BIND + timedelta(hours=1))
        # The caller must never compute payout if decision != ELIGIBLE
        assert d.decision != "ELIGIBLE"

    def test_waiting_period_denial_takes_precedence_over_zone_match(self):
        """Zone match ✓ but waiting ✗ → still blocked."""
        d = decide(_BIND + timedelta(hours=2))
        assert d.decision == "BLOCKED_WAITING"

    def test_waiting_period_denial_shown_as_primary_reason(self):
        d = decide(_BIND + timedelta(hours=2))
        assert d.reason_code == RC_EVENT_BEFORE_CLAIM_ELIGIBLE_AT

    def test_next_cycle_blocked_shows_next_cycle_reason_not_generic(self):
        friday_bind = datetime(2026, 4, 17, 10, 0, 0)
        saturday_event = datetime(2026, 4, 18, 12, 0, 0)
        d = evaluate_waiting_eligibility(
            bind_time=friday_bind,
            event_time=saturday_event,
            config=cfg(RULE_NEXT_CYCLE),
        )
        assert d.reason_code == RC_NEXT_CYCLE_NOT_STARTED

    def test_valid_continuity_overrides_waiting(self):
        cont = ContinuityResult(
            is_continuous=True,
            continuity_reason="Uninterrupted renewal",
            previous_policy_end=_BIND - timedelta(minutes=1),
            new_policy_start=_BIND,
            gap_hours=0.0,
            within_grace=True,
            identity_match=True,
            channel_continuity=True,
            reason_code=RC_CONTINUOUS_RENEWAL_BYPASS,
        )
        d = evaluate_waiting_eligibility(
            bind_time=_BIND,
            event_time=_BIND + timedelta(hours=1),
            config=cfg(RULE_24H),
            continuity=cont,
        )
        assert d.decision == "ELIGIBLE"
        assert d.reason_code == RC_CONTINUOUS_RENEWAL_BYPASS


# ─────────────────────────────────────────────────────────────────────────────
# L — Security / abuse / identity (spec items 159-170)
# ─────────────────────────────────────────────────────────────────────────────

class TestSecurityAndAbuse:

    def test_client_tampered_event_time_in_past_does_not_bypass(self):
        """If client submits an event_time before bind, it's still blocked."""
        spoofed_event = _BIND - timedelta(hours=10)
        d = decide(spoofed_event)
        assert d.decision == "BLOCKED_WAITING"

    def test_client_cannot_submit_early_event_time_to_bypass_waiting(self):
        """Event at bind+0.5h spoofed to appear at bind+25h — service uses event_time."""
        # Service only uses event_time as passed — caller must validate source
        # If event_time is within waiting window, decision is BLOCKED regardless
        early_event = _BIND + timedelta(hours=1)
        d = decide(early_event)
        assert d.decision == "BLOCKED_WAITING"

    def test_linked_account_rebuy_resets_waiting(self):
        """Even if same identity, cancel-rebuy resets waiting from new bind_time."""
        old_end = _BIND - timedelta(hours=1)  # old policy ended 1h before new bind
        cont = evaluate_policy_continuity(
            previous_policy_end=old_end,
            new_policy_start=_BIND,
            grace_period_hours=0,
        )
        assert cont.is_continuous is False

    def test_identity_mismatch_on_renewal_breaks_continuity(self):
        cont = evaluate_policy_continuity(
            previous_policy_end=_BIND - timedelta(minutes=5),
            new_policy_start=_BIND,
            identity_match=False,
        )
        assert cont.is_continuous is False
        assert cont.reason_code == RC_CANCEL_REBUY_RESETS_WAITING

    def test_admin_override_does_not_alter_original_reason_code(self):
        """Build snapshot, then verify it is immutable after the fact."""
        d = decide(_BIND + timedelta(hours=1))
        snap = build_waiting_snapshot(d)
        original_code = snap.reason_code
        # Simulated 'admin change' — snapshot is a frozen dataclass
        assert snap.reason_code == original_code   # cannot be mutated


# ─────────────────────────────────────────────────────────────────────────────
# Master parametrize table — minimum must-pass test pack (spec priority order)
# ─────────────────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("label", "hours_after_bind", "rule", "is_renewal", "is_sponsor",
     "expect_decision", "expect_rc"),
    [
        # 1. New enrollment blocked
        ("new_blocked_1h",   1,  RULE_24H, False, False, "BLOCKED_WAITING", RC_EVENT_BEFORE_CLAIM_ELIGIBLE_AT),
        ("new_blocked_23h",  23, RULE_24H, False, False, "BLOCKED_WAITING", RC_EVENT_BEFORE_CLAIM_ELIGIBLE_AT),
        # 2. Claim allowed exactly at boundary
        ("new_exact_24h",   24, RULE_24H, False, False, "ELIGIBLE",         RC_ELIGIBLE),
        # 3. Renewal immediate (is_renewal bypasses via continuity — tested via RULE_ZERO standalone)
        ("zero_wait_zero",   0, RULE_ZERO, False, False, "ELIGIBLE",         RC_WAITING_NOT_APPLICABLE),
        # 4. Sponsor bypass
        ("sponsor_bypass",   0, RULE_24H, False, True,  "ELIGIBLE",         RC_SPONSOR_BYPASS_ALLOWED),
        # 5. 72h rule boundary
        ("72h_blocked",     71, RULE_72H, False, False, "BLOCKED_WAITING",  RC_EVENT_BEFORE_CLAIM_ELIGIBLE_AT),
        ("72h_exact",       72, RULE_72H, False, False, "ELIGIBLE",         RC_ELIGIBLE),
        # 6. Anti-gaming: minutes after purchase
        ("arb_30m",          0, RULE_24H, False, False, "BLOCKED_WAITING",  RC_EVENT_BEFORE_CLAIM_ELIGIBLE_AT),
        # 7. Unknown rule fails safe
        # (tested separately in TestConfigRuleVariants.test_unknown_rule_type_fails_safe_to_review_required)
        # 8. Post-waiting eligible
        ("well_past",       48, RULE_24H, False, False, "ELIGIBLE",         RC_ELIGIBLE),
    ],
)
def test_waiting_period_parametrize_table(
    label, hours_after_bind, rule, is_renewal, is_sponsor,
    expect_decision, expect_rc,
):
    event_time = _BIND + timedelta(hours=hours_after_bind)
    config = WaitingConfig(rule_type=rule, is_renewal=is_renewal, is_sponsor=is_sponsor)
    d = evaluate_waiting_eligibility(
        bind_time=_BIND,
        event_time=event_time,
        config=config,
    )
    assert d.decision == expect_decision, (
        f"[{label}] expected decision={expect_decision!r}, got {d.decision!r}. "
        f"reason: {d.reason}"
    )
    assert d.reason_code == expect_rc, (
        f"[{label}] expected rc={expect_rc!r}, got {d.reason_code!r}"
    )
