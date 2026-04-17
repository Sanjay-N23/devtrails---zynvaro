"""
services/cooling_off.py
========================
Pure service for waiting-period / cooling-off logic.

Spec reference  (Zynvaro_Final_Combined.md):
  § 5 — "24–72 hour waiting period for new enrollments"
  § 6 — "Renewals can continue immediately if uninterrupted"
  § 6 — "Sponsor-backed cohorts: waiting period can be relaxed"

No DB dependency — works on plain datetime arithmetic so it can be
unit-tested without spinning up a session.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Optional


# ─────────────────────────────────────────────────────────────────
# CONFIGURABLE CONSTANTS (Section 15 of the activity checklist)
# ─────────────────────────────────────────────────────────────────
COOLING_OFF_HOURS_NEW      = 24   # retail new enrollments
COOLING_OFF_HOURS_RENEWAL  = 0    # uninterrupted renewals — no wait
COOLING_OFF_HOURS_SPONSOR  = 0    # sponsor-backed cohorts — relaxed

# Reason codes (machine-readable)
RC_COOLING_OFF_ACTIVE      = "COOLING_OFF_ACTIVE"
RC_COOLING_OFF_CLEARED     = "COOLING_OFF_CLEARED"
RC_COOLING_OFF_BYPASSED    = "COOLING_OFF_BYPASSED"


# ─────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────

def get_cooling_off_hours(
    is_renewal: bool = False,
    is_sponsor: bool = False,
) -> int:
    """Return the waiting-period duration (hours) for the enrollment type."""
    if is_renewal:
        return COOLING_OFF_HOURS_RENEWAL
    if is_sponsor:
        return COOLING_OFF_HOURS_SPONSOR
    return COOLING_OFF_HOURS_NEW


def evaluate_cooling_off(
    policy_start_date: datetime,
    *,
    is_simulated: bool = False,
    bypass_gate: bool = False,
    is_renewal: bool = False,
    is_sponsor: bool = False,
    now: Optional[datetime] = None,
) -> dict:
    """
    Evaluate whether a policy has cleared its cooling-off period.

    Returns
    -------
    dict with keys:
        eligible       : bool   — True if the policy can receive claims
        reason_code    : str    — machine-readable RC_* constant
        hours_elapsed  : float  — how old the policy is (hours)
        hours_remaining: float  — time left in cooling-off (0 if cleared)
        cooling_off_hours: int  — total waiting period for this enrollment type
        eligible_at    : datetime — when the policy becomes eligible
        reason         : str    — human-readable explanation
    """
    now = now or datetime.utcnow()
    cooling_hours = get_cooling_off_hours(is_renewal, is_sponsor)
    hours_elapsed = round(
        max(0.0, (now - policy_start_date).total_seconds() / 3600), 2
    )
    eligible_at = policy_start_date + timedelta(hours=cooling_hours)

    # Bypass paths (demo / simulation)
    if bypass_gate or is_simulated:
        return {
            "eligible": True,
            "reason_code": RC_COOLING_OFF_BYPASSED,
            "hours_elapsed": hours_elapsed,
            "hours_remaining": 0.0,
            "cooling_off_hours": cooling_hours,
            "eligible_at": eligible_at,
            "reason": "Cooling-off gate bypassed (demo/simulation).",
        }

    # Renewal / sponsor — 0h wait
    if cooling_hours == 0:
        return {
            "eligible": True,
            "reason_code": RC_COOLING_OFF_CLEARED,
            "hours_elapsed": hours_elapsed,
            "hours_remaining": 0.0,
            "cooling_off_hours": 0,
            "eligible_at": policy_start_date,
            "reason": "No waiting period for renewals / sponsor-backed policies.",
        }

    # Check if policy has cleared the waiting period
    if hours_elapsed >= cooling_hours:
        return {
            "eligible": True,
            "reason_code": RC_COOLING_OFF_CLEARED,
            "hours_elapsed": hours_elapsed,
            "hours_remaining": 0.0,
            "cooling_off_hours": cooling_hours,
            "eligible_at": eligible_at,
            "reason": f"Policy has cleared the {cooling_hours}-hour cooling-off period.",
        }

    # Still inside cooling-off
    hours_remaining = round(cooling_hours - hours_elapsed, 1)
    return {
        "eligible": False,
        "reason_code": RC_COOLING_OFF_ACTIVE,
        "hours_elapsed": hours_elapsed,
        "hours_remaining": hours_remaining,
        "cooling_off_hours": cooling_hours,
        "eligible_at": eligible_at,
        "reason": (
            f"Policy is {hours_elapsed:.1f}h old — "
            f"{hours_remaining}h remaining in the {cooling_hours}-hour waiting period."
        ),
    }


def policy_cooling_off_status(
    policy_start_date: datetime,
    *,
    is_renewal: bool = False,
    is_sponsor: bool = False,
    now: Optional[datetime] = None,
) -> dict:
    """
    Lightweight status check for API responses. Returns info a worker
    can understand and the frontend can render as a countdown.

    Returns
    -------
    dict with keys:
        in_cooling_off    : bool
        cooling_off_hours : int
        eligible_at       : datetime
        hours_remaining   : float | None
    """
    now = now or datetime.utcnow()
    cooling_hours = get_cooling_off_hours(is_renewal, is_sponsor)
    eligible_at = policy_start_date + timedelta(hours=cooling_hours)

    if cooling_hours == 0 or now >= eligible_at:
        return {
            "in_cooling_off": False,
            "cooling_off_hours": cooling_hours,
            "eligible_at": eligible_at,
            "hours_remaining": None,
        }

    hours_remaining = round(
        max(0.0, (eligible_at - now).total_seconds() / 3600), 1
    )
    return {
        "in_cooling_off": True,
        "cooling_off_hours": cooling_hours,
        "eligible_at": eligible_at,
        "hours_remaining": hours_remaining,
    }
