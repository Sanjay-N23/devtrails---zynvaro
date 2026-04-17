"""
services/waiting_period.py
===========================
Pure waiting-period / cooling-off evaluation service.

Spec reference (Zynvaro_Final_Combined.md):
  § 5 — "24–72 hour waiting period for new enrollments" (forecast arbitrage prevention)
  § 6 — "Renewals: can continue immediately if uninterrupted"
  § 6 — "Sponsor-backed cohorts: waiting period can be relaxed"

Key design principles
---------------------
1. Uses **event_time** (when the trigger happened), NOT processing_time or now().
   Backfilled/delayed processing must not create unfair payouts.
2. claim_eligible_at is persisted at bind time and NEVER recomputed for old policies.
3. Continuity logic is separate from waiting logic:
   first evaluate_policy_continuity(), then evaluate_waiting_eligibility().
4. All timestamps are naive UTC datetimes — no tz-aware objects.
5. This module has no DB dependency — pure datetime arithmetic.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Optional

# ─────────────────────────────────────────────────────────────────
# CURRENT RULE VERSION — bump when waiting rules change
# Old policies retain their frozen version at bind time.
# ─────────────────────────────────────────────────────────────────
CURRENT_RULE_VERSION = "v1"

# ─────────────────────────────────────────────────────────────────
# WAITING RULE TYPES
# ─────────────────────────────────────────────────────────────────
RULE_24H           = "24h"
RULE_72H           = "72h"
RULE_NEXT_CYCLE    = "next_cycle"   # claim_eligible_at = start of next weekly cycle
RULE_ZERO          = "zero"         # no wait (renewals, sponsor, partner)

# ─────────────────────────────────────────────────────────────────
# REASON CODES  (machine-readable, never mutate these values)
# ─────────────────────────────────────────────────────────────────
RC_WAITING_NOT_APPLICABLE        = "WAITING_NOT_APPLICABLE"
RC_WAITING_PERIOD_ACTIVE         = "WAITING_PERIOD_ACTIVE"
RC_NEXT_CYCLE_NOT_STARTED        = "NEXT_CYCLE_NOT_STARTED"
RC_CONTINUOUS_RENEWAL_BYPASS     = "CONTINUOUS_RENEWAL_BYPASS"
RC_GRACE_RENEWAL_BYPASS          = "GRACE_RENEWAL_BYPASS"
RC_LAPSE_RESETS_WAITING          = "LAPSE_RESETS_WAITING"
RC_CANCEL_REBUY_RESETS_WAITING   = "CANCEL_REBUY_RESETS_WAITING"
RC_SPONSOR_BYPASS_ALLOWED        = "SPONSOR_BYPASS_ALLOWED"
RC_CONFIG_INVALID_WAITING_BLOCKED = "CONFIG_INVALID_WAITING_BLOCKED"
RC_EVENT_BEFORE_CLAIM_ELIGIBLE_AT = "EVENT_BEFORE_CLAIM_ELIGIBLE_AT"
RC_ELIGIBLE                      = "WAITING_ELIGIBLE"

# ─────────────────────────────────────────────────────────────────
# WAITING CONFIG DEFAULTS
# ─────────────────────────────────────────────────────────────────
DEFAULT_GRACE_PERIOD_HOURS = 0     # renewals: no grace by default
MAX_GRACE_PERIOD_HOURS     = 72    # absolute ceiling for any grace window
WEEKLY_CYCLE_DAYS          = 7


# ─────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────

@dataclass
class WaitingConfig:
    """Frozen rule set for a specific enrollment. Persisted at bind time."""
    rule_type: str = RULE_24H                  # RULE_* constant
    rule_version: str = CURRENT_RULE_VERSION
    grace_period_hours: int = DEFAULT_GRACE_PERIOD_HOURS
    is_sponsor: bool = False
    is_renewal: bool = False
    channel: str = "retail"                    # 'retail' | 'partner' | 'admin'


@dataclass
class ContinuityResult:
    """Result of evaluating whether a renewal preserves coverage continuity."""
    is_continuous: bool
    continuity_reason: str
    previous_policy_end: Optional[datetime]
    new_policy_start: Optional[datetime]
    gap_hours: Optional[float]
    within_grace: bool
    identity_match: bool
    channel_continuity: bool
    reason_code: str


@dataclass
class WaitingDecision:
    """
    Final waiting-period decision for a single policy/trigger combination.
    Persisted to the Claim row at evaluation time and NEVER recomputed.
    """
    waiting_applies: bool
    decision: str                              # ELIGIBLE | BLOCKED_WAITING | REVIEW_REQUIRED
    reason_code: str
    purchase_time: datetime
    bind_time: datetime
    activation_time: datetime
    claim_eligible_at: datetime
    event_time_used: datetime
    continuity_status: Optional[str]           # RC_CONTINUOUS_RENEWAL_BYPASS etc.
    grace_period_used: bool
    waiting_rule_type: str
    waiting_rule_duration_hours: Optional[float]
    rule_version: str
    reason: str                                # human-readable for worker


@dataclass
class WaitingSnapshot:
    """
    Immutable audit record persisted alongside the claim.
    Contains every value used at evaluation time so history cannot drift.
    """
    # Timestamps (all UTC naive)
    purchase_time: datetime
    bind_time: datetime
    activation_time: datetime
    claim_eligible_at: datetime
    event_time_used: datetime
    decision_time: datetime

    # Decision output
    decision: str
    reason_code: str
    waiting_applies: bool
    rule_type: str
    rule_version: str
    grace_period_used: bool
    continuity_status: Optional[str]

    # Worker-safe explanation (no internal debug data)
    worker_explanation: str

    def to_claim_fields(self) -> dict:
        """Return a dict suitable for setting on a Claim ORM object."""
        return {
            "waiting_decision": self.decision,
            "waiting_reason_code": self.reason_code,
            "claim_eligible_at_snapshot": self.claim_eligible_at,
            "event_time_used": self.event_time_used,
            "waiting_rule_version": self.rule_version,
        }


# ─────────────────────────────────────────────────────────────────
# HELPERS
# ─────────────────────────────────────────────────────────────────

def _compute_claim_eligible_at(
    bind_time: datetime,
    config: WaitingConfig,
) -> datetime:
    """Compute the exact datetime when a policy's claims become eligible."""
    if config.rule_type == RULE_ZERO or config.is_sponsor or config.is_renewal:
        return bind_time   # immediate

    if config.rule_type == RULE_24H:
        return bind_time + timedelta(hours=24)

    if config.rule_type == RULE_72H:
        return bind_time + timedelta(hours=72)

    if config.rule_type == RULE_NEXT_CYCLE:
        # Next Monday 00:00 UTC from bind_time
        days_until_monday = (7 - bind_time.weekday()) % 7
        if days_until_monday == 0:
            days_until_monday = 7
        return (bind_time + timedelta(days=days_until_monday)).replace(
            hour=0, minute=0, second=0, microsecond=0
        )

    # Unknown rule — fail safe: treat as 24h
    return bind_time + timedelta(hours=24)


def _hours_between(a: datetime, b: datetime) -> float:
    """Positive hours from a to b."""
    return round((b - a).total_seconds() / 3600, 4)


# ─────────────────────────────────────────────────────────────────
# PUBLIC API — CONTINUITY EVALUATOR
# ─────────────────────────────────────────────────────────────────

def evaluate_policy_continuity(
    previous_policy_end: Optional[datetime],
    new_policy_start: datetime,
    *,
    identity_match: bool = True,
    channel_continuity: bool = True,
    grace_period_hours: int = DEFAULT_GRACE_PERIOD_HOURS,
) -> ContinuityResult:
    """
    Determine whether the new policy continues uninterrupted from the previous.

    Rules:
    - No previous policy → not continuous (first-ever or after long lapse).
    - Gap ≤ grace_period_hours → continuous (grace renewal bypass).
    - Gap > grace_period_hours → lapse, waiting resets.
    - identity_match=False → breaks continuity (e.g. phone/KYC mismatch).
    - channel_continuity=False → channel-level break (handled by caller).
    """
    if previous_policy_end is None:
        return ContinuityResult(
            is_continuous=False,
            continuity_reason="No previous policy found — first enrollment or long lapse.",
            previous_policy_end=None,
            new_policy_start=new_policy_start,
            gap_hours=None,
            within_grace=False,
            identity_match=identity_match,
            channel_continuity=channel_continuity,
            reason_code=RC_LAPSE_RESETS_WAITING,
        )

    if not identity_match:
        return ContinuityResult(
            is_continuous=False,
            continuity_reason="Identity mismatch between previous and new policy.",
            previous_policy_end=previous_policy_end,
            new_policy_start=new_policy_start,
            gap_hours=_hours_between(previous_policy_end, new_policy_start),
            within_grace=False,
            identity_match=False,
            channel_continuity=channel_continuity,
            reason_code=RC_CANCEL_REBUY_RESETS_WAITING,
        )

    clamped_grace = min(grace_period_hours, MAX_GRACE_PERIOD_HOURS)
    gap_hours = _hours_between(previous_policy_end, new_policy_start)

    if gap_hours <= 0:
        # Overlap or exact continuation — uninterrupted
        return ContinuityResult(
            is_continuous=True,
            continuity_reason=f"Uninterrupted renewal (gap: {gap_hours:.2f}h).",
            previous_policy_end=previous_policy_end,
            new_policy_start=new_policy_start,
            gap_hours=gap_hours,
            within_grace=True,
            identity_match=True,
            channel_continuity=channel_continuity,
            reason_code=RC_CONTINUOUS_RENEWAL_BYPASS,
        )

    if gap_hours <= clamped_grace:
        return ContinuityResult(
            is_continuous=True,
            continuity_reason=f"Renewal within grace period ({gap_hours:.1f}h gap, {clamped_grace}h grace).",
            previous_policy_end=previous_policy_end,
            new_policy_start=new_policy_start,
            gap_hours=gap_hours,
            within_grace=True,
            identity_match=True,
            channel_continuity=channel_continuity,
            reason_code=RC_GRACE_RENEWAL_BYPASS,
        )

    # Gap exceeds grace — waiting resets
    return ContinuityResult(
        is_continuous=False,
        continuity_reason=(
            f"Gap of {gap_hours:.1f}h exceeds grace period ({clamped_grace}h). "
            "Waiting period resets."
        ),
        previous_policy_end=previous_policy_end,
        new_policy_start=new_policy_start,
        gap_hours=gap_hours,
        within_grace=False,
        identity_match=True,
        channel_continuity=channel_continuity,
        reason_code=RC_LAPSE_RESETS_WAITING,
    )


# ─────────────────────────────────────────────────────────────────
# PUBLIC API — WAITING ELIGIBILITY EVALUATOR
# ─────────────────────────────────────────────────────────────────

def evaluate_waiting_eligibility(
    *,
    bind_time: datetime,
    event_time: datetime,
    config: WaitingConfig,
    continuity: Optional[ContinuityResult] = None,
    claim_eligible_at: Optional[datetime] = None,
) -> WaitingDecision:
    """
    Evaluate whether a claim is eligible given the waiting-period rules.

    Uses **event_time** (when the trigger occurred), NOT processing/wall-clock time.
    This means backfilled or delayed processing cannot create unfair payouts.

    Parameters
    ----------
    bind_time         : When the policy was bound/activated (server-side only).
    event_time        : When the trigger event actually occurred.
    config            : Frozen waiting config from the policy's bind time.
    continuity        : Result from evaluate_policy_continuity() if applicable.
    claim_eligible_at : Pre-computed claim_eligible_at from the policy row (preferred).
                        If None, recomputes from config (for new policies).
    """
    purchase_time = bind_time   # In Zynvaro, purchase_time == bind_time (server sets it)
    activation_time = bind_time

    # ── Continuity bypass ──────────────────────────────────────────
    if continuity and continuity.is_continuous:
        elig_at = claim_eligible_at or bind_time
        return WaitingDecision(
            waiting_applies=False,
            decision="ELIGIBLE",
            reason_code=continuity.reason_code,
            purchase_time=purchase_time,
            bind_time=bind_time,
            activation_time=activation_time,
            claim_eligible_at=elig_at,
            event_time_used=event_time,
            continuity_status=continuity.reason_code,
            grace_period_used=continuity.within_grace,
            waiting_rule_type=config.rule_type,
            waiting_rule_duration_hours=None,
            rule_version=config.rule_version,
            reason="Continuous renewal — no waiting period re-applied.",
        )

    # ── Rule-zero / sponsor / is_renewal bypass ────────────────────
    if config.rule_type == RULE_ZERO or config.is_sponsor:
        return WaitingDecision(
            waiting_applies=False,
            decision="ELIGIBLE",
            reason_code=RC_SPONSOR_BYPASS_ALLOWED if config.is_sponsor else RC_WAITING_NOT_APPLICABLE,
            purchase_time=purchase_time,
            bind_time=bind_time,
            activation_time=activation_time,
            claim_eligible_at=bind_time,
            event_time_used=event_time,
            continuity_status=None,
            grace_period_used=False,
            waiting_rule_type=config.rule_type,
            waiting_rule_duration_hours=0,
            rule_version=config.rule_version,
            reason="No waiting period for sponsor-backed or zero-wait channel.",
        )

    # ── Validate config ────────────────────────────────────────────
    valid_rules = {RULE_24H, RULE_72H, RULE_NEXT_CYCLE, RULE_ZERO}
    if config.rule_type not in valid_rules:
        # Fail safe — block claims until manually resolved
        return WaitingDecision(
            waiting_applies=True,
            decision="REVIEW_REQUIRED",
            reason_code=RC_CONFIG_INVALID_WAITING_BLOCKED,
            purchase_time=purchase_time,
            bind_time=bind_time,
            activation_time=activation_time,
            claim_eligible_at=bind_time + timedelta(hours=24),  # safe default
            event_time_used=event_time,
            continuity_status=None,
            grace_period_used=False,
            waiting_rule_type=config.rule_type,
            waiting_rule_duration_hours=None,
            rule_version=config.rule_version,
            reason="Invalid waiting config — claim requires manual review.",
        )

    # ── Compute claim_eligible_at ──────────────────────────────────
    elig_at = claim_eligible_at or _compute_claim_eligible_at(bind_time, config)

    # Duration for metadata
    duration_hours = _hours_between(bind_time, elig_at)

    # ── Decision: use event_time NOT wall-clock time ───────────────
    if event_time < elig_at:
        return WaitingDecision(
            waiting_applies=True,
            decision="BLOCKED_WAITING",
            reason_code=RC_EVENT_BEFORE_CLAIM_ELIGIBLE_AT
            if config.rule_type != RULE_NEXT_CYCLE
            else RC_NEXT_CYCLE_NOT_STARTED,
            purchase_time=purchase_time,
            bind_time=bind_time,
            activation_time=activation_time,
            claim_eligible_at=elig_at,
            event_time_used=event_time,
            continuity_status=continuity.reason_code if continuity else None,
            grace_period_used=False,
            waiting_rule_type=config.rule_type,
            waiting_rule_duration_hours=duration_hours,
            rule_version=config.rule_version,
            reason=(
                f"Event occurred at {event_time.strftime('%Y-%m-%d %H:%M UTC')} — "
                f"before claim eligibility at {elig_at.strftime('%Y-%m-%d %H:%M UTC')}. "
                f"Waiting period: {config.rule_type}."
            ),
        )

    return WaitingDecision(
        waiting_applies=False,
        decision="ELIGIBLE",
        reason_code=RC_ELIGIBLE,
        purchase_time=purchase_time,
        bind_time=bind_time,
        activation_time=activation_time,
        claim_eligible_at=elig_at,
        event_time_used=event_time,
        continuity_status=continuity.reason_code if continuity else None,
        grace_period_used=False,
        waiting_rule_type=config.rule_type,
        waiting_rule_duration_hours=duration_hours,
        rule_version=config.rule_version,
        reason=(
            f"Event at {event_time.strftime('%Y-%m-%d %H:%M UTC')} is "
            f"at or after claim eligibility ({elig_at.strftime('%Y-%m-%d %H:%M UTC')})."
        ),
    )


# ─────────────────────────────────────────────────────────────────
# PUBLIC API — SNAPSHOT BUILDER
# ─────────────────────────────────────────────────────────────────

def build_waiting_snapshot(
    decision: WaitingDecision,
    continuity: Optional[ContinuityResult] = None,
    decision_time: Optional[datetime] = None,
) -> WaitingSnapshot:
    """
    Build an immutable WaitingSnapshot from a WaitingDecision.
    This is persisted to the Claim row and must never be recomputed.

    Worker-facing explanation is computed here and frozen — it always
    reflects the rules at the time of the original decision.
    """
    now = decision_time or datetime.utcnow()

    worker_texts = {
        "ELIGIBLE": "Your policy was active and past the waiting period when this event occurred.",
        "BLOCKED_WAITING": (
            f"This event happened during your policy's {decision.waiting_rule_type} waiting period. "
            f"Your coverage became fully active at {decision.claim_eligible_at.strftime('%d %b %Y %H:%M UTC')}."
        ),
        "REVIEW_REQUIRED": (
            "Your claim is under manual review due to a configuration issue with your policy's waiting period."
        ),
    }

    return WaitingSnapshot(
        purchase_time=decision.purchase_time,
        bind_time=decision.bind_time,
        activation_time=decision.activation_time,
        claim_eligible_at=decision.claim_eligible_at,
        event_time_used=decision.event_time_used,
        decision_time=now,
        decision=decision.decision,
        reason_code=decision.reason_code,
        waiting_applies=decision.waiting_applies,
        rule_type=decision.waiting_rule_type,
        rule_version=decision.rule_version,
        grace_period_used=decision.grace_period_used,
        continuity_status=continuity.reason_code if continuity else decision.continuity_status,
        worker_explanation=worker_texts.get(decision.decision, decision.reason),
    )
