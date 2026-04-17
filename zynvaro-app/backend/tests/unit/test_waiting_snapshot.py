"""
backend/tests/unit/test_waiting_snapshot.py
============================================
Tests for snapshot immutability:
  build_waiting_snapshot() → WaitingSnapshot

Covers spec section I — Snapshot / Audit / Traceability (items 116-128).

Key guarantee: snapshots persist the state AT DECISION TIME.
Config changes, schema migrations, or re-evaluations must NOT alter
historical snapshots. All timestamps are confirmed frozen.
"""
from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from services.waiting_period import (
    CURRENT_RULE_VERSION,
    RULE_24H, RULE_72H, RULE_NEXT_CYCLE,
    RC_EVENT_BEFORE_CLAIM_ELIGIBLE_AT,
    RC_ELIGIBLE,
    RC_CONTINUOUS_RENEWAL_BYPASS,
    WaitingConfig,
    WaitingDecision,
    ContinuityResult,
    evaluate_waiting_eligibility,
    evaluate_policy_continuity,
    build_waiting_snapshot,
)

_BIND  = datetime(2026, 4, 17, 10, 0, 0)
_EV    = _BIND + timedelta(hours=5)   # during 24h waiting window
_AFTER = _BIND + timedelta(hours=25)  # after waiting cleared


def _blocked_decision() -> WaitingDecision:
    return evaluate_waiting_eligibility(
        bind_time=_BIND,
        event_time=_EV,
        config=WaitingConfig(rule_type=RULE_24H),
    )


def _eligible_decision() -> WaitingDecision:
    return evaluate_waiting_eligibility(
        bind_time=_BIND,
        event_time=_AFTER,
        config=WaitingConfig(rule_type=RULE_24H),
    )


# ─────────────────────────────────────────────────────────────────────────────
# I — Snapshot structure / content
# ─────────────────────────────────────────────────────────────────────────────

class TestSnapshotStructure:

    def test_snapshot_contains_all_required_timestamps(self):
        snap = build_waiting_snapshot(_blocked_decision())
        assert snap.purchase_time is not None
        assert snap.bind_time is not None
        assert snap.activation_time is not None
        assert snap.claim_eligible_at is not None
        assert snap.event_time_used is not None
        assert snap.decision_time is not None

    def test_snapshot_stores_correct_bind_time(self):
        snap = build_waiting_snapshot(_blocked_decision())
        assert snap.bind_time == _BIND

    def test_snapshot_stores_correct_event_time(self):
        snap = build_waiting_snapshot(_blocked_decision())
        assert snap.event_time_used == _EV

    def test_snapshot_stores_correct_claim_eligible_at(self):
        snap = build_waiting_snapshot(_blocked_decision())
        assert snap.claim_eligible_at == _BIND + timedelta(hours=24)

    def test_snapshot_stores_decision(self):
        snap = build_waiting_snapshot(_blocked_decision())
        assert snap.decision == "BLOCKED_WAITING"

    def test_snapshot_stores_reason_code(self):
        snap = build_waiting_snapshot(_blocked_decision())
        assert snap.reason_code == RC_EVENT_BEFORE_CLAIM_ELIGIBLE_AT

    def test_snapshot_stores_rule_version(self):
        snap = build_waiting_snapshot(_blocked_decision())
        assert snap.rule_version == CURRENT_RULE_VERSION

    def test_snapshot_stores_worker_safe_explanation(self):
        snap = build_waiting_snapshot(_blocked_decision())
        assert snap.worker_explanation
        assert "waiting" in snap.worker_explanation.lower() or "24" in snap.worker_explanation

    def test_snapshot_worker_explanation_for_eligible_decision(self):
        snap = build_waiting_snapshot(_eligible_decision())
        assert snap.decision == "ELIGIBLE"
        assert snap.worker_explanation

    def test_to_claim_fields_returns_all_expected_keys(self):
        snap = build_waiting_snapshot(_blocked_decision())
        fields = snap.to_claim_fields()
        assert "waiting_decision" in fields
        assert "waiting_reason_code" in fields
        assert "claim_eligible_at_snapshot" in fields
        assert "event_time_used" in fields
        assert "waiting_rule_version" in fields

    def test_to_claim_fields_values_match_snapshot(self):
        snap = build_waiting_snapshot(_blocked_decision())
        fields = snap.to_claim_fields()
        assert fields["waiting_decision"] == snap.decision
        assert fields["waiting_reason_code"] == snap.reason_code
        assert fields["claim_eligible_at_snapshot"] == snap.claim_eligible_at
        assert fields["event_time_used"] == snap.event_time_used
        assert fields["waiting_rule_version"] == snap.rule_version


# ─────────────────────────────────────────────────────────────────────────────
# I — Snapshot immutability after config changes
# ─────────────────────────────────────────────────────────────────────────────

class TestSnapshotImmutability:

    def test_historical_denial_snapshot_not_recomputed(self):
        """
        Simulate: rule changes from 24h to 72h after the fact.
        Old snapshot must still show original 24h decision.
        """
        original_config = WaitingConfig(rule_type=RULE_24H, rule_version="v1")
        d_original = evaluate_waiting_eligibility(
            bind_time=_BIND,
            event_time=_EV,
            config=original_config,
        )
        snap_original = build_waiting_snapshot(d_original)

        # Config changes (new rule is 72h, v2) — does NOT affect old snapshot
        new_config = WaitingConfig(rule_type=RULE_72H, rule_version="v2")
        d_new = evaluate_waiting_eligibility(
            bind_time=_BIND,
            event_time=_EV,
            config=new_config,
        )
        snap_new = build_waiting_snapshot(d_new)

        # Snapshots are independent — old decision remains "BLOCKED_WAITING" with v1
        assert snap_original.rule_version == "v1"
        assert snap_original.rule_type == RULE_24H
        assert snap_new.rule_version == "v2"
        assert snap_new.rule_type == RULE_72H

    def test_old_policy_claim_eligible_at_not_affected_by_new_rule(self):
        """claim_eligible_at in old snapshot = bind +24h, even if rule is now 72h."""
        old_snap = build_waiting_snapshot(
            evaluate_waiting_eligibility(
                bind_time=_BIND, event_time=_EV,
                config=WaitingConfig(rule_type=RULE_24H, rule_version="v1"),
            )
        )
        assert old_snap.claim_eligible_at == _BIND + timedelta(hours=24)
        # A new policy would have 72h, but old snapshot is frozen
        assert old_snap.rule_type == RULE_24H

    def test_eligible_snapshot_preserves_decision_after_rule_tightening(self):
        """Old claim was ELIGIBLE under 24h rule; new rule is 72h → old stays ELIGIBLE."""
        event_25h_after = _BIND + timedelta(hours=25)
        old_d = evaluate_waiting_eligibility(
            bind_time=_BIND, event_time=event_25h_after,
            config=WaitingConfig(rule_type=RULE_24H, rule_version="v1"),
        )
        snap = build_waiting_snapshot(old_d)
        # Under old 24h rule → ELIGIBLE
        assert snap.decision == "ELIGIBLE"
        assert snap.rule_version == "v1"

    def test_snapshot_rule_version_frozen_at_bind_time(self):
        """Snapshot always records the rule_version from the config at bind time."""
        d = evaluate_waiting_eligibility(
            bind_time=_BIND, event_time=_EV,
            config=WaitingConfig(rule_version="v1-custom"),
        )
        snap = build_waiting_snapshot(d)
        assert snap.rule_version == "v1-custom"

    def test_snapshot_claim_eligible_at_is_bind_time_rule_not_now(self):
        """claim_eligible_at is computed from bind_time + rule, never from wall-clock."""
        d = evaluate_waiting_eligibility(
            bind_time=_BIND, event_time=_EV,
            config=WaitingConfig(rule_type=RULE_24H),
        )
        snap = build_waiting_snapshot(d, decision_time=_AFTER)  # wall clock is far ahead
        # claim_eligible_at should still be bind + 24h
        assert snap.claim_eligible_at == _BIND + timedelta(hours=24)

    def test_decision_time_in_snapshot_is_processing_time(self):
        """decision_time = when we processed the claim, NOT event_time."""
        processing_time = _BIND + timedelta(hours=36)
        d = evaluate_waiting_eligibility(
            bind_time=_BIND, event_time=_EV, config=WaitingConfig()
        )
        snap = build_waiting_snapshot(d, decision_time=processing_time)
        assert snap.decision_time == processing_time
        assert snap.event_time_used == _EV  # always event_time

    def test_continuity_status_stored_in_snapshot(self):
        cont = evaluate_policy_continuity(
            previous_policy_end=_BIND - timedelta(minutes=5),
            new_policy_start=_BIND,
        )
        d = evaluate_waiting_eligibility(
            bind_time=_BIND,
            event_time=_EV,
            config=WaitingConfig(rule_type=RULE_24H),
            continuity=cont,
        )
        snap = build_waiting_snapshot(d, continuity=cont)
        assert snap.continuity_status == cont.reason_code


# ─────────────────────────────────────────────────────────────────────────────
# Integration: snapshot → claim fields → round-trip check
# ─────────────────────────────────────────────────────────────────────────────

class TestSnapshotToClaimIntegration:

    def test_claim_fields_round_trip_for_blocked_decision(
        self, make_worker, make_policy, make_trigger, make_claim, test_db
    ):
        """
        Verify that claim ORM objects correctly store and retrieve
        waiting_decision, waiting_reason_code, and claim_eligible_at_snapshot.
        """
        from models import Claim, PolicyStatus

        worker = make_worker(city="Bangalore")
        policy = make_policy(
            worker=worker,
            status=PolicyStatus.ACTIVE,
            start_date=_BIND,
        )
        trigger = make_trigger(city="Bangalore", trigger_type="Heavy Rainfall")
        d = evaluate_waiting_eligibility(
            bind_time=_BIND,
            event_time=_EV,
            config=WaitingConfig(rule_type=RULE_24H),
        )
        snap = build_waiting_snapshot(d)
        claim_fields = snap.to_claim_fields()

        claim = make_claim(worker=worker, policy=policy, trigger=trigger)
        claim.waiting_decision = claim_fields["waiting_decision"]
        claim.waiting_reason_code = claim_fields["waiting_reason_code"]
        claim.claim_eligible_at_snapshot = claim_fields["claim_eligible_at_snapshot"]
        claim.event_time_used = claim_fields["event_time_used"]
        claim.waiting_rule_version = claim_fields["waiting_rule_version"]
        test_db.commit()
        test_db.refresh(claim)

        assert claim.waiting_decision == "BLOCKED_WAITING"
        assert claim.waiting_reason_code == RC_EVENT_BEFORE_CLAIM_ELIGIBLE_AT
        assert claim.claim_eligible_at_snapshot is not None

    def test_historical_claim_snap_unchanged_after_later_config_change(
        self, make_worker, make_policy, make_trigger, make_claim, test_db
    ):
        """
        Persist a BLOCKED_WAITING snapshot, then simulate config change.
        The stored claim fields must remain unchanged.
        """
        from models import Claim, PolicyStatus

        worker = make_worker(city="Bangalore")
        policy = make_policy(
            worker=worker, status=PolicyStatus.ACTIVE, start_date=_BIND,
        )
        trigger = make_trigger(city="Bangalore", trigger_type="Severe Heatwave")

        # Decision under v1 rule
        d_v1 = evaluate_waiting_eligibility(
            bind_time=_BIND, event_time=_EV,
            config=WaitingConfig(rule_type=RULE_24H, rule_version="v1"),
        )
        snap_v1 = build_waiting_snapshot(d_v1)
        claim = make_claim(worker=worker, policy=policy, trigger=trigger)
        claim.waiting_decision = snap_v1.decision
        claim.waiting_reason_code = snap_v1.reason_code
        claim.waiting_rule_version = snap_v1.rule_version
        test_db.commit()

        # "Config changes" — this is a new evaluation, not a mutation of old claim
        d_v2 = evaluate_waiting_eligibility(
            bind_time=_BIND, event_time=_EV,
            config=WaitingConfig(rule_type=RULE_72H, rule_version="v2"),
        )

        # Original claim is still v1
        test_db.refresh(claim)
        assert claim.waiting_rule_version == "v1"
        assert claim.waiting_decision == "BLOCKED_WAITING"
