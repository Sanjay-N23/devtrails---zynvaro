"""
Zynvaro — Analytics Router
Exposes the weekly stats, time-series, and city heat-map functions
from analytics.py as authenticated HTTP endpoints.

All endpoints are admin-facing (require a valid JWT).
"""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from typing import List, Optional

from database import get_db
from models import Worker
from routers.auth import get_current_worker
from routers.claims import get_current_admin
from analytics import get_weekly_stats, get_weekly_time_series, get_city_stats_for_week

router = APIRouter(prefix="/analytics", tags=["Analytics"])


@router.get("/weekly")
def weekly_stats(
    week: Optional[int] = Query(None, description="ISO week number (1–53). Defaults to current week."),
    year: Optional[int] = Query(None, description="4-digit year. Defaults to current year."),
    city: Optional[str] = Query(None, description="Filter by city (e.g. 'Mumbai')."),
    db: Session = Depends(get_db),
    current_worker: Worker = Depends(get_current_admin),
):
    """
    [Admin] Return all dashboard KPIs for a given ISO week.

    Includes: premiums collected, claims by status, payout totals, loss ratio,
    auto-approval rate, fraud risk signals, and per-trigger breakdown.
    Defaults to the current ISO week if week/year are omitted.
    """
    stats = get_weekly_stats(db, week=week, year=year, city=city)
    return stats.to_dict()


@router.get("/time-series")
def time_series(
    weeks: int = Query(8, ge=1, le=52, description="Number of past ISO weeks to return (1–52)."),
    city: Optional[str] = Query(None, description="Filter by city."),
    db: Session = Depends(get_db),
    current_worker: Worker = Depends(get_current_admin),
):
    """
    [Admin] Return WeeklyStats for the last N ISO weeks, ordered oldest-first.
    Used to populate line/bar charts on the admin dashboard.
    """
    return get_weekly_time_series(db, weeks=weeks, city=city)


@router.get("/cities")
def city_breakdown(
    week: Optional[int] = Query(None, description="ISO week number. Defaults to current week."),
    year: Optional[int] = Query(None, description="4-digit year. Defaults to current year."),
    db: Session = Depends(get_db),
    current_worker: Worker = Depends(get_current_admin),
):
    """
    [Admin] Return per-city aggregations for one week, sorted by loss ratio descending.
    Used for the geographic heat-map on the admin dashboard.

    Returns: city, policies_issued, claims_total, total_premiums_collected,
             total_payouts_settled, loss_ratio, avg_authenticity_score
    """
    return get_city_stats_for_week(db, week=week, year=year)
