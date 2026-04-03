"""
Zynvaro — Weekly Analytics Engine
Provides WeeklyStats computation for the admin dashboard.

All queries are parameterised by ISO week number and optional year/city filters
so the frontend can render time-series charts and drill-down tables without
hitting raw joins across three tables on every page load.

Usage (FastAPI route example):
    from analytics import get_weekly_stats, get_weekly_time_series
    stats = get_weekly_stats(db, week=14, year=2026)
    series = get_weekly_time_series(db, weeks=8, city="Mumbai")
"""

from __future__ import annotations

from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from typing import List, Optional

from sqlalchemy import func, case, extract
from sqlalchemy.orm import Session

from models import (
    Claim, ClaimStatus, Policy, PayoutTransaction,
    PayoutTransactionStatus, TriggerEvent, Worker,
)


# ─────────────────────────────────────────────────────────────────
# DATA CLASSES
# ─────────────────────────────────────────────────────────────────

@dataclass
class TriggerBreakdown:
    """Claim counts and payouts sliced by trigger type."""
    trigger_type: str
    claim_count: int
    total_payout: float
    avg_payout: float


@dataclass
class WeeklyStats:
    """
    All metrics needed by the admin analytics dashboard for one ISO week.

    loss_ratio = total_payouts_settled / total_premiums_collected
    A loss ratio > 1.0 means the insurer paid out more than it collected.
    """
    # Identity
    week_number: int
    year: int
    week_start: str          # ISO date string e.g. "2026-03-30"
    week_end: str            # ISO date string e.g. "2026-04-05"
    city_filter: Optional[str]

    # Premium side
    policies_issued: int
    total_premiums_collected: float  # Sum of weekly_premium for active policies in this week
    avg_premium: float

    # Claims side
    claims_total: int
    claims_auto_approved: int
    claims_manual_review: int
    claims_paid: int
    claims_rejected: int

    # Payout side (from Claim.payout_amount / paid_at fields)
    total_payouts_settled: float     # Confirmed settled transactions only
    total_payouts_pending: float     # Initiated + pending transactions
    avg_payout_per_claim: float

    # Ratios
    loss_ratio: float                # total_payouts_settled / total_premiums_collected
    claim_rate: float                # claims_total / policies_issued (0.0–1.0+)
    auto_approval_rate: float        # claims_auto_approved / claims_total

    # Fraud signals
    avg_authenticity_score: float    # Mean across all claims this week
    high_fraud_risk_claims: int      # Claims with authenticity_score < 50

    # Trigger breakdown
    by_trigger: List[TriggerBreakdown] = field(default_factory=list)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["by_trigger"] = [asdict(t) for t in self.by_trigger]
        return d


# ─────────────────────────────────────────────────────────────────
# WEEK BOUNDARY HELPERS
# ─────────────────────────────────────────────────────────────────

def _week_boundaries(week: int, year: int):
    """
    Returns (week_start, week_end) as datetime objects for an ISO week.
    ISO weeks start on Monday.
    """
    # Jan 4th is always in ISO week 1
    jan4 = datetime(year, 1, 4)
    # Monday of week 1
    week1_monday = jan4 - timedelta(days=jan4.weekday())
    week_start = week1_monday + timedelta(weeks=week - 1)
    week_end = week_start + timedelta(days=6, hours=23, minutes=59, seconds=59)
    return week_start, week_end


def _current_iso_week() -> tuple[int, int]:
    """Returns (week_number, year) for today."""
    today = datetime.utcnow()
    iso = today.isocalendar()
    return iso[1], iso[0]


# ─────────────────────────────────────────────────────────────────
# CORE STATS FUNCTION
# ─────────────────────────────────────────────────────────────────

def get_weekly_stats(
    db: Session,
    week: Optional[int] = None,
    year: Optional[int] = None,
    city: Optional[str] = None,
) -> WeeklyStats:
    """
    Compute all dashboard KPIs for a given ISO week.

    Parameters
    ----------
    db   : SQLAlchemy session
    week : ISO week number (1–53). Defaults to current week.
    year : 4-digit year. Defaults to current year.
    city : Optional city filter (e.g. "Mumbai"). Filters workers by city.
    """
    if week is None or year is None:
        _w, _y = _current_iso_week()
        week = week or _w
        year = year or _y

    week_start, week_end = _week_boundaries(week, year)

    # ── Base worker queryset (city filter applied once here) ──────
    worker_ids_q = db.query(Worker.id)
    if city:
        worker_ids_q = worker_ids_q.filter(Worker.city == city)
    worker_ids = [r[0] for r in worker_ids_q.all()]

    # ── Policies issued this week ─────────────────────────────────
    policy_q = (
        db.query(Policy)
        .filter(
            Policy.start_date >= week_start,
            Policy.start_date <= week_end,
        )
    )
    if city:
        policy_q = policy_q.filter(Policy.worker_id.in_(worker_ids))

    policies = policy_q.all()
    policies_issued = len(policies)
    total_premiums = round(sum(p.weekly_premium for p in policies), 2)
    avg_premium = round(total_premiums / policies_issued, 2) if policies_issued else 0.0

    # ── Claims created this week ──────────────────────────────────
    claim_q = (
        db.query(Claim)
        .filter(
            Claim.created_at >= week_start,
            Claim.created_at <= week_end,
        )
    )
    if city:
        claim_q = claim_q.filter(Claim.worker_id.in_(worker_ids))

    claims = claim_q.all()
    claims_total = len(claims)

    # Count both AUTO_APPROVED (pending payment) and PAID (payment confirmed) —
    # both represent claims that passed the ML fraud scorer automatically.
    claims_auto_approved = sum(
        1 for c in claims if c.status in (ClaimStatus.AUTO_APPROVED, ClaimStatus.PAID)
    )
    claims_manual_review = sum(
        1 for c in claims
        if c.status in (ClaimStatus.MANUAL_REVIEW, ClaimStatus.PENDING_REVIEW)
    )
    claims_paid = sum(1 for c in claims if c.status == ClaimStatus.PAID)
    claims_rejected = sum(1 for c in claims if c.status == ClaimStatus.REJECTED)

    # Fraud signals
    auth_scores = [c.authenticity_score for c in claims if c.authenticity_score is not None]
    avg_authenticity = round(sum(auth_scores) / len(auth_scores), 2) if auth_scores else 0.0
    high_fraud_risk = sum(1 for s in auth_scores if s < 50)

    # ── Payout amounts from Claim model ────────────────────────
    # NOTE: PayoutTransaction is unused in the current flow (kept for Phase 3
    # Razorpay integration).  Claim.payout_amount + Claim.paid_at are the
    # source of truth for settled/pending amounts.
    claim_ids = [c.id for c in claims]

    # Use Claim.payout_amount for settled amounts
    settled_total = round(sum(
        (c.payout_amount or 0) for c in claims
        if c.status in (ClaimStatus.PAID,) and c.paid_at is not None
    ), 2)
    pending_total = round(sum(
        (c.payout_amount or 0) for c in claims
        if c.status in (ClaimStatus.AUTO_APPROVED, ClaimStatus.PENDING_REVIEW)
    ), 2)
    avg_payout = round(settled_total / claims_total, 2) if claims_total > 0 else 0.0

    # ── Derived ratios ────────────────────────────────────────────
    loss_ratio = round(settled_total / total_premiums, 4) if total_premiums > 0 else 0.0
    claim_rate = round(claims_total / policies_issued, 4) if policies_issued > 0 else 0.0
    auto_approval_rate = (
        round(claims_auto_approved / claims_total, 4) if claims_total > 0 else 0.0
    )

    # ── Trigger-type breakdown ────────────────────────────────────
    trigger_breakdown: List[TriggerBreakdown] = []
    if claim_ids:
        # Use Claim.payout_amount (PAID + paid_at) instead of PayoutTransaction
        trigger_rows = (
            db.query(
                TriggerEvent.trigger_type,
                func.count(Claim.id).label("claim_count"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                (Claim.status == ClaimStatus.PAID) & (Claim.paid_at.isnot(None)),
                                Claim.payout_amount,
                            ),
                            else_=0,
                        )
                    ),
                    0.0,
                ).label("total_payout"),
            )
            .join(Claim, Claim.trigger_event_id == TriggerEvent.id)
            .filter(Claim.id.in_(claim_ids))
            .group_by(TriggerEvent.trigger_type)
            .all()
        )
        for row in trigger_rows:
            trigger_breakdown.append(
                TriggerBreakdown(
                    trigger_type=row.trigger_type,
                    claim_count=row.claim_count,
                    total_payout=round(float(row.total_payout), 2),
                    avg_payout=round(
                        float(row.total_payout) / row.claim_count, 2
                    ) if row.claim_count else 0.0,
                )
            )

    return WeeklyStats(
        week_number=week,
        year=year,
        week_start=week_start.date().isoformat(),
        week_end=week_end.date().isoformat(),
        city_filter=city,
        policies_issued=policies_issued,
        total_premiums_collected=total_premiums,
        avg_premium=avg_premium,
        claims_total=claims_total,
        claims_auto_approved=claims_auto_approved,
        claims_manual_review=claims_manual_review,
        claims_paid=claims_paid,
        claims_rejected=claims_rejected,
        total_payouts_settled=settled_total,
        total_payouts_pending=pending_total,
        avg_payout_per_claim=avg_payout,
        loss_ratio=loss_ratio,
        claim_rate=claim_rate,
        auto_approval_rate=auto_approval_rate,
        avg_authenticity_score=avg_authenticity,
        high_fraud_risk_claims=high_fraud_risk,
        by_trigger=trigger_breakdown,
    )


# ─────────────────────────────────────────────────────────────────
# TIME-SERIES: LAST N WEEKS
# ─────────────────────────────────────────────────────────────────

def get_weekly_time_series(
    db: Session,
    weeks: int = 8,
    city: Optional[str] = None,
) -> List[dict]:
    """
    Returns WeeklyStats for the last `weeks` ISO weeks, ordered oldest-first.
    Used to populate line/bar charts on the admin dashboard.

    Parameters
    ----------
    db    : SQLAlchemy session
    weeks : How many past weeks to return (default 8)
    city  : Optional city filter
    """
    current_week, current_year = _current_iso_week()
    results = []

    for offset in range(weeks - 1, -1, -1):
        target_week = current_week - offset
        target_year = current_year

        # Handle wrap-around into the previous year
        if target_week < 1:
            target_year -= 1
            # ISO weeks in prior year — compute properly
            dec28 = datetime(target_year, 12, 28)
            target_week = dec28.isocalendar()[1] + target_week  # adjusts negative

        stats = get_weekly_stats(db, week=target_week, year=target_year, city=city)
        results.append(stats.to_dict())

    return results


# ─────────────────────────────────────────────────────────────────
# CITY-LEVEL LEADERBOARD (for map/heat-map view)
# ─────────────────────────────────────────────────────────────────

def get_city_stats_for_week(
    db: Session,
    week: Optional[int] = None,
    year: Optional[int] = None,
) -> List[dict]:
    """
    Returns per-city aggregations for one week, used for the geographic
    heat-map on the admin dashboard.

    Returns list of dicts with keys:
        city, policies_issued, claims_total, loss_ratio, total_payouts_settled
    """
    if week is None or year is None:
        _w, _y = _current_iso_week()
        week = week or _w
        year = year or _y

    cities = [r[0] for r in db.query(Worker.city).distinct().all()]
    city_stats = []

    for city in cities:
        stats = get_weekly_stats(db, week=week, year=year, city=city)
        city_stats.append({
            "city": city,
            "policies_issued": stats.policies_issued,
            "claims_total": stats.claims_total,
            "total_premiums_collected": stats.total_premiums_collected,
            "total_payouts_settled": stats.total_payouts_settled,
            "loss_ratio": stats.loss_ratio,
            "avg_authenticity_score": stats.avg_authenticity_score,
        })

    # Sort by loss_ratio descending so the riskiest cities appear first
    return sorted(city_stats, key=lambda x: x["loss_ratio"], reverse=True)
