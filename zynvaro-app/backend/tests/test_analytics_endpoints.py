"""
Zynvaro Backend -- Analytics endpoint tests
============================================
Tests the three admin-only endpoints under /analytics/:
  GET /analytics/weekly
  GET /analytics/time-series
  GET /analytics/cities

All endpoints require admin JWT auth (is_admin=True).
"""

from datetime import datetime, timedelta

import pytest

from models import ClaimStatus, PolicyStatus
from tests.conftest import worker_token


# ─────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────

def _current_iso_week():
    today = datetime.utcnow()
    iso = today.isocalendar()
    return iso[1], iso[0]


def _seed_data(make_worker, make_policy, make_trigger, make_claim, *,
               city="Bangalore", num_claims=2, paid_claims=1):
    """
    Create a worker + policy + trigger + N claims in the current ISO week.
    Returns (worker, policy, trigger, claims_list).
    """
    week, year = _current_iso_week()
    # Compute Monday of the current ISO week so all records land inside it
    jan4 = datetime(year, 1, 4)
    week1_monday = jan4 - timedelta(days=jan4.weekday())
    week_start = week1_monday + timedelta(weeks=week - 1)

    worker = make_worker(city=city)
    policy = make_policy(
        worker=worker,
        start_date=week_start + timedelta(hours=1),
    )
    trigger = make_trigger(city=city, detected_at=week_start + timedelta(hours=2))

    claims = []
    for i in range(num_claims):
        if i < paid_claims:
            c = make_claim(
                worker=worker,
                policy=policy,
                trigger=trigger,
                status=ClaimStatus.PAID,
                payout_amount=500.0,
                paid_at=week_start + timedelta(hours=3 + i),
            )
        else:
            c = make_claim(
                worker=worker,
                policy=policy,
                trigger=trigger,
                status=ClaimStatus.AUTO_APPROVED,
                payout_amount=350.0,
            )
        claims.append(c)
    return worker, policy, trigger, claims


# ─────────────────────────────────────────────────────────────────
# 1. GET /analytics/weekly -> returns dict with all WeeklyStats fields
# ─────────────────────────────────────────────────────────────────

WEEKLY_REQUIRED_FIELDS = {
    "week_number", "year", "week_start", "week_end", "city_filter",
    "policies_issued", "total_premiums_collected", "avg_premium",
    "claims_total", "claims_auto_approved", "claims_manual_review",
    "claims_paid", "claims_rejected",
    "total_payouts_settled", "total_payouts_pending", "avg_payout_per_claim",
    "loss_ratio", "claim_rate", "auto_approval_rate",
    "avg_authenticity_score", "high_fraud_risk_claims", "by_trigger",
}


def test_weekly_returns_all_fields(
    authed_client, make_worker, make_policy, make_trigger, make_claim,
):
    _seed_data(make_worker, make_policy, make_trigger, make_claim)
    resp = authed_client.get("/analytics/weekly")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, dict)
    assert WEEKLY_REQUIRED_FIELDS.issubset(data.keys()), (
        f"Missing fields: {WEEKLY_REQUIRED_FIELDS - data.keys()}"
    )


# ─────────────────────────────────────────────────────────────────
# 2. loss_ratio is numeric >= 0
# ─────────────────────────────────────────────────────────────────

def test_weekly_loss_ratio_numeric_nonneg(
    authed_client, make_worker, make_policy, make_trigger, make_claim,
):
    _seed_data(make_worker, make_policy, make_trigger, make_claim)
    resp = authed_client.get("/analytics/weekly")
    data = resp.json()
    assert isinstance(data["loss_ratio"], (int, float))
    assert data["loss_ratio"] >= 0


# ─────────────────────────────────────────────────────────────────
# 3. With paid claims -> total_payouts_settled > 0
#    (uses Claim.payout_amount, not PayoutTransaction)
# ─────────────────────────────────────────────────────────────────

def test_weekly_paid_claims_settled_positive(
    authed_client, make_worker, make_policy, make_trigger, make_claim,
):
    _seed_data(
        make_worker, make_policy, make_trigger, make_claim,
        paid_claims=2, num_claims=2,
    )
    resp = authed_client.get("/analytics/weekly")
    data = resp.json()
    assert data["total_payouts_settled"] > 0, (
        "Settled payouts should be > 0 when PAID claims exist with payout_amount"
    )
    # Two PAID claims at 500 each
    assert data["total_payouts_settled"] == 1000.0


# ─────────────────────────────────────────────────────────────────
# 4. GET /analytics/time-series?weeks=4 -> list of 4 entries
# ─────────────────────────────────────────────────────────────────

def test_time_series_returns_correct_count(authed_client):
    resp = authed_client.get("/analytics/time-series", params={"weeks": 4})
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) == 4


# ─────────────────────────────────────────────────────────────────
# 5. Time-series entries are ordered oldest-first
# ─────────────────────────────────────────────────────────────────

def test_time_series_ordered_oldest_first(authed_client):
    resp = authed_client.get("/analytics/time-series", params={"weeks": 6})
    data = resp.json()
    week_numbers = [(d["year"], d["week_number"]) for d in data]
    assert week_numbers == sorted(week_numbers), (
        "Time-series entries should be ordered oldest-first by (year, week_number)"
    )


# ─────────────────────────────────────────────────────────────────
# 6. GET /analytics/cities -> returns list of city dicts
# ─────────────────────────────────────────────────────────────────

def test_cities_returns_list(
    authed_client, make_worker, make_policy, make_trigger, make_claim,
):
    _seed_data(make_worker, make_policy, make_trigger, make_claim, city="Mumbai")
    resp = authed_client.get("/analytics/cities")
    assert resp.status_code == 200
    data = resp.json()
    assert isinstance(data, list)
    assert len(data) >= 1
    # Each entry should be a dict with a "city" key
    for entry in data:
        assert isinstance(entry, dict)
        assert "city" in entry


# ─────────────────────────────────────────────────────────────────
# 7. Each city dict has loss_ratio field
# ─────────────────────────────────────────────────────────────────

def test_cities_has_loss_ratio(
    authed_client, make_worker, make_policy, make_trigger, make_claim,
):
    _seed_data(make_worker, make_policy, make_trigger, make_claim, city="Delhi")
    resp = authed_client.get("/analytics/cities")
    data = resp.json()
    for entry in data:
        assert "loss_ratio" in entry, f"City entry missing loss_ratio: {entry}"
        assert isinstance(entry["loss_ratio"], (int, float))


# ─────────────────────────────────────────────────────────────────
# 8. Analytics with no claims -> returns zeros without crash
# ─────────────────────────────────────────────────────────────────

def test_weekly_no_claims_returns_zeros(authed_client):
    resp = authed_client.get("/analytics/weekly")
    assert resp.status_code == 200
    data = resp.json()
    assert data["claims_total"] == 0
    assert data["total_payouts_settled"] == 0.0
    assert data["loss_ratio"] == 0.0
    assert data["auto_approval_rate"] == 0.0


def test_time_series_no_claims_no_crash(authed_client):
    resp = authed_client.get("/analytics/time-series", params={"weeks": 2})
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


def test_cities_no_data_no_crash(authed_client):
    # The authed_client worker exists but has no policy/claims in this week.
    # The cities endpoint should still return a list (possibly with the worker's city).
    resp = authed_client.get("/analytics/cities")
    assert resp.status_code == 200
    assert isinstance(resp.json(), list)


# ─────────────────────────────────────────────────────────────────
# 9. claims_auto_approved counts both AUTO_APPROVED + PAID statuses
# ─────────────────────────────────────────────────────────────────

def test_claims_auto_approved_includes_paid(
    authed_client, make_worker, make_policy, make_trigger, make_claim,
):
    """
    The analytics engine counts PAID claims as auto-approved since PAID
    is the next lifecycle state after AUTO_APPROVED. Both should be
    included in claims_auto_approved.
    """
    week, year = _current_iso_week()
    jan4 = datetime(year, 1, 4)
    week1_monday = jan4 - timedelta(days=jan4.weekday())
    week_start = week1_monday + timedelta(weeks=week - 1)

    worker = make_worker()
    policy = make_policy(worker=worker, start_date=week_start + timedelta(hours=1))
    trigger = make_trigger(city=worker.city, detected_at=week_start + timedelta(hours=2))

    # One AUTO_APPROVED claim
    make_claim(
        worker=worker, policy=policy, trigger=trigger,
        status=ClaimStatus.AUTO_APPROVED, payout_amount=200.0,
    )
    # One PAID claim
    make_claim(
        worker=worker, policy=policy, trigger=trigger,
        status=ClaimStatus.PAID, payout_amount=400.0,
        paid_at=week_start + timedelta(hours=4),
    )
    # One REJECTED claim (should NOT count)
    make_claim(
        worker=worker, policy=policy, trigger=trigger,
        status=ClaimStatus.REJECTED, payout_amount=0.0,
    )

    resp = authed_client.get("/analytics/weekly")
    data = resp.json()

    assert data["claims_total"] == 3
    # AUTO_APPROVED + PAID = 2
    assert data["claims_auto_approved"] == 2
    assert data["claims_rejected"] == 1


# ─────────────────────────────────────────────────────────────────
# 10. Non-admin access -> 403 on all 3 endpoints
# ─────────────────────────────────────────────────────────────────

def test_non_admin_forbidden_weekly(client, make_worker):
    worker = make_worker(is_admin=False)
    token = worker_token(worker.id)
    resp = client.get("/analytics/weekly", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_non_admin_forbidden_time_series(client, make_worker):
    worker = make_worker(is_admin=False)
    token = worker_token(worker.id)
    resp = client.get("/analytics/time-series", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


def test_non_admin_forbidden_cities(client, make_worker):
    worker = make_worker(is_admin=False)
    token = worker_token(worker.id)
    resp = client.get("/analytics/cities", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


# ─── Forecast Endpoint Tests (Phase 3: Predictive Analytics) ─────

def test_forecast_returns_200(authed_client):
    resp = authed_client.get("/analytics/forecast")
    assert resp.status_code == 200


def test_forecast_has_required_fields(authed_client):
    data = authed_client.get("/analytics/forecast").json()
    for key in ["forecast_week", "predicted_loss_ratio", "predicted_claims",
                "predicted_payouts_inr", "confidence_interval", "seasonal_factor",
                "trigger_risk_forecast", "city_risk_forecast", "historical_trend",
                "method", "data_points_used"]:
        assert key in data, f"Missing key: {key}"


def test_forecast_loss_ratio_non_negative(authed_client):
    data = authed_client.get("/analytics/forecast").json()
    assert data["predicted_loss_ratio"] >= 0


def test_forecast_confidence_interval_is_pair(authed_client):
    data = authed_client.get("/analytics/forecast").json()
    ci = data["confidence_interval"]
    assert isinstance(ci, list) and len(ci) == 2
    assert ci[0] <= ci[1]


def test_forecast_city_risk_has_7_cities(authed_client):
    data = authed_client.get("/analytics/forecast").json()
    cities = [c["city"] for c in data["city_risk_forecast"]]
    assert len(cities) == 7
    for city in ["Mumbai", "Delhi", "Bangalore", "Hyderabad", "Chennai", "Pune", "Kolkata"]:
        assert city in cities


def test_forecast_city_filter(authed_client):
    data = authed_client.get("/analytics/forecast?city=Mumbai").json()
    assert data["forecast_week"] > 0


def test_forecast_non_admin_forbidden(client, make_worker):
    worker = make_worker(is_admin=False)
    token = worker_token(worker.id)
    resp = client.get("/analytics/forecast", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403
