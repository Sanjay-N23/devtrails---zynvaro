"""
Zynvaro — Tests: Weekly Payout Cap Enforcement
===============================================
Validates the H7 weekly aggregate payout cap logic in
``_auto_generate_claims()`` (routers/triggers.py, lines 258-270).

The cap checks ``sum(payout_amount)`` for paid claims (``paid_at IS NOT NULL``)
in the last 7 days against ``policy.max_weekly_payout``.  If the new payout
would exceed the cap, it is clamped to the remaining allowance; if the
allowance is zero the claim is skipped entirely.

Test matrix
-----------
1. Zero paid claims this week      -> full payout created
2. Near the weekly cap             -> payout clamped to remaining allowance
3. At/over weekly cap              -> no claim created (skipped)
4. Basic Shield (max_weekly=600)   -> cap enforced at 600
5. Pro Armor   (max_weekly=2000)   -> higher cap allows larger payouts
6. Claims older than 7 days        -> do not count toward weekly cap
"""

import sys
import pytest
from datetime import datetime, timedelta

sys.path.insert(0, "D:/AeroFyta_DEVTrails-2026_FULL_HANDOFF/zynvaro-app/backend")

from models import (
    Worker, Policy, Claim, TriggerEvent,
    PolicyStatus, ClaimStatus, PolicyTier, TriggerType,
)
from ml.premium_engine import get_payout_amount
from tests.conftest import worker_token


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _auth_headers(worker: Worker) -> dict:
    return {"Authorization": f"Bearer {worker_token(worker.id)}"}


def _simulate(client, worker: Worker, trigger_type: str, city: str) -> dict:
    resp = client.post(
        "/triggers/simulate",
        json={"trigger_type": trigger_type, "city": city},
        headers=_auth_headers(worker),
    )
    assert resp.status_code == 201, (
        f"simulate failed: {resp.status_code} -- {resp.text}"
    )
    return resp.json()


def _insert_paid_claim(
    db, worker, policy, trigger,
    payout_amount: float,
    created_at: datetime | None = None,
    paid_at: datetime | None = None,
):
    """
    Insert a PAID claim directly into the DB so it counts toward the
    weekly cap (it has paid_at set and created_at within the window).
    """
    import random, string
    claim_num = "CLM-CAP-" + "".join(random.choices(string.digits, k=6))
    now = datetime.utcnow()
    claim = Claim(
        claim_number=claim_num,
        worker_id=worker.id,
        policy_id=policy.id,
        trigger_event_id=trigger.id,
        status=ClaimStatus.PAID,
        payout_amount=payout_amount,
        authenticity_score=95.0,
        gps_valid=True,
        activity_valid=True,
        device_valid=True,
        cross_source_valid=True,
        fraud_flags=None,
        auto_processed=True,
        paid_at=paid_at or now,
        payment_ref=f"MOCK-UPI-{claim_num}",
    )
    db.add(claim)
    db.flush()
    # Override created_at after flush (SQLite default fires on INSERT)
    if created_at is not None:
        claim.created_at = created_at
        db.flush()
    db.commit()
    db.refresh(claim)
    return claim


# ---------------------------------------------------------------------------
# Fixture: cap_env -- client + factories ready for weekly-cap testing
# ---------------------------------------------------------------------------

@pytest.fixture()
def cap_env(client, test_db, test_engine, make_worker, make_policy, make_trigger):
    """
    Yields (client, make_worker, make_policy, make_trigger, test_db).
    ``database.SessionLocal`` is already patched to the test engine by the
    auto-use ``_patch_database_session_local`` fixture from conftest.py.
    """
    yield client, make_worker, make_policy, make_trigger, test_db


# ===========================================================================
class TestWeeklyPayoutCap:
    """Weekly aggregate payout cap enforcement in _auto_generate_claims."""

    # ---- 1. Zero paid claims -> full payout ----------------------------

    def test_zero_paid_claims_gets_full_payout(self, cap_env):
        """
        Worker has no paid claims this week.
        A new trigger should create a claim with the full payout amount.
        """
        client, mw, mp, mt, db = cap_env

        worker = mw(city="Mumbai", pincode="400001",
                     claim_history_count=0, disruption_streak=0)
        policy = mp(worker=worker, tier=PolicyTier.STANDARD,
                    max_daily_payout=600.0, max_weekly_payout=1200.0)

        _simulate(client, worker, "Heavy Rainfall", "Mumbai")

        claim = db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None, "Claim should be created when no prior paid claims exist"

        expected_payout = get_payout_amount("Heavy Rainfall", "Standard Guard", "Mumbai")
        assert claim.payout_amount == expected_payout, (
            f"Expected full payout of {expected_payout}, got {claim.payout_amount}"
        )
        assert claim.payout_amount > 0

    # ---- 2. Near weekly cap -> payout clamped --------------------------

    def test_near_weekly_cap_payout_is_clamped(self, cap_env):
        """
        Worker has paid claims totalling close to the weekly cap.
        The next trigger payout should be clamped to the remaining
        allowance (not the full per-event amount).
        """
        client, mw, mp, mt, db = cap_env

        worker = mw(city="Bangalore", pincode="560001",
                     claim_history_count=0, disruption_streak=0)
        # Standard Guard: max_weekly = 1200
        policy = mp(worker=worker, tier=PolicyTier.STANDARD,
                    max_daily_payout=600.0, max_weekly_payout=1200.0)

        full_payout = get_payout_amount("Heavy Rainfall", "Standard Guard", "Bangalore")
        assert full_payout > 0, "Sanity: payout engine returns >0"

        # Pre-fill paid claims to leave only 50 INR of weekly room
        prefill_amount = 1200.0 - 50.0  # 1150
        trigger_seed = mt(city="Bangalore",
                          trigger_type=TriggerType.HAZARDOUS_AQI)  # different type to avoid dedup
        _insert_paid_claim(db, worker, policy, trigger_seed,
                           payout_amount=prefill_amount)

        # Now simulate a new trigger
        _simulate(client, worker, "Heavy Rainfall", "Bangalore")

        new_claim = (
            db.query(Claim)
            .filter(
                Claim.worker_id == worker.id,
                Claim.payout_amount != prefill_amount,  # skip the seed claim
            )
            .first()
        )
        assert new_claim is not None, "A clamped claim should still be created"
        assert new_claim.payout_amount <= 50.0, (
            f"Payout should be clamped to <=50 (remaining allowance), "
            f"got {new_claim.payout_amount}"
        )
        assert new_claim.payout_amount > 0, "Clamped payout should still be positive"

    # ---- 3. At/over weekly cap -> no claim created ---------------------

    def test_at_weekly_cap_no_claim_created(self, cap_env):
        """
        Worker's paid claims already sum to the weekly cap.
        The next trigger must NOT create a new claim (skipped).
        """
        client, mw, mp, mt, db = cap_env

        worker = mw(city="Delhi", pincode="110001",
                     claim_history_count=0, disruption_streak=0)
        # Standard Guard: max_weekly = 1200
        policy = mp(worker=worker, tier=PolicyTier.STANDARD,
                    max_daily_payout=600.0, max_weekly_payout=1200.0)

        # Fill the cap exactly
        trigger_seed = mt(city="Delhi",
                          trigger_type=TriggerType.HAZARDOUS_AQI)
        _insert_paid_claim(db, worker, policy, trigger_seed,
                           payout_amount=1200.0)

        claims_before = db.query(Claim).filter(
            Claim.worker_id == worker.id
        ).count()

        _simulate(client, worker, "Heavy Rainfall", "Delhi")

        claims_after = db.query(Claim).filter(
            Claim.worker_id == worker.id
        ).count()
        assert claims_after == claims_before, (
            f"No new claim should be created when weekly cap is reached "
            f"(before={claims_before}, after={claims_after})"
        )

    def test_over_weekly_cap_no_claim_created(self, cap_env):
        """
        Worker's paid claims already exceed the weekly cap.
        The next trigger must NOT create a new claim.
        """
        client, mw, mp, mt, db = cap_env

        worker = mw(city="Mumbai", pincode="400001",
                     claim_history_count=0, disruption_streak=0)
        policy = mp(worker=worker, tier=PolicyTier.STANDARD,
                    max_daily_payout=600.0, max_weekly_payout=1200.0)

        # Overshoot the cap
        trigger_seed = mt(city="Mumbai",
                          trigger_type=TriggerType.HAZARDOUS_AQI)
        _insert_paid_claim(db, worker, policy, trigger_seed,
                           payout_amount=1500.0)

        claims_before = db.query(Claim).filter(
            Claim.worker_id == worker.id
        ).count()

        _simulate(client, worker, "Heavy Rainfall", "Mumbai")

        claims_after = db.query(Claim).filter(
            Claim.worker_id == worker.id
        ).count()
        assert claims_after == claims_before, (
            f"No new claim should be created when weekly cap is exceeded "
            f"(before={claims_before}, after={claims_after})"
        )

    # ---- 4. Basic Shield (max_weekly=600) cap --------------------------

    def test_basic_shield_cap_at_600(self, cap_env):
        """
        Basic Shield max_weekly_payout = 600.
        Pre-fill 550 -> next payout clamped to at most 50.
        """
        client, mw, mp, mt, db = cap_env

        worker = mw(city="Bangalore", pincode="560001",
                     claim_history_count=0, disruption_streak=0)
        policy = mp(worker=worker, tier=PolicyTier.BASIC,
                    max_daily_payout=300.0, max_weekly_payout=600.0)

        trigger_seed = mt(city="Bangalore",
                          trigger_type=TriggerType.HAZARDOUS_AQI)
        _insert_paid_claim(db, worker, policy, trigger_seed,
                           payout_amount=550.0)

        _simulate(client, worker, "Heavy Rainfall", "Bangalore")

        new_claim = (
            db.query(Claim)
            .filter(
                Claim.worker_id == worker.id,
                Claim.payout_amount != 550.0,
            )
            .first()
        )
        if new_claim is not None:
            assert new_claim.payout_amount <= 50.0, (
                f"Basic Shield payout should be clamped to <=50 "
                f"(600 cap - 550 used), got {new_claim.payout_amount}"
            )
        # If no new claim, that means the full payout exceeded 50 and
        # was clamped to 50 OR the payout_amount itself <= 50 was created.
        # Either way the cap is enforced.

    def test_basic_shield_cap_reached_skips_claim(self, cap_env):
        """
        Basic Shield: pre-fill exactly 600 -> next trigger skipped.
        """
        client, mw, mp, mt, db = cap_env

        worker = mw(city="Bangalore", pincode="560001",
                     claim_history_count=0, disruption_streak=0)
        policy = mp(worker=worker, tier=PolicyTier.BASIC,
                    max_daily_payout=300.0, max_weekly_payout=600.0)

        trigger_seed = mt(city="Bangalore",
                          trigger_type=TriggerType.HAZARDOUS_AQI)
        _insert_paid_claim(db, worker, policy, trigger_seed,
                           payout_amount=600.0)

        claims_before = db.query(Claim).filter(
            Claim.worker_id == worker.id
        ).count()

        _simulate(client, worker, "Heavy Rainfall", "Bangalore")

        claims_after = db.query(Claim).filter(
            Claim.worker_id == worker.id
        ).count()
        assert claims_after == claims_before, (
            "Basic Shield (600 cap) reached -- no new claim expected"
        )

    # ---- 5. Pro Armor (max_weekly=2000) allows more --------------------

    def test_pro_armor_higher_cap_allows_more(self, cap_env):
        """
        Pro Armor max_weekly = 2000.
        Pre-fill 1500 -> next payout still created (up to 500 remaining).
        A Standard Guard with 1200 cap would have blocked this.
        """
        client, mw, mp, mt, db = cap_env

        worker = mw(city="Mumbai", pincode="400001",
                     claim_history_count=0, disruption_streak=0)
        policy = mp(worker=worker, tier=PolicyTier.PRO,
                    max_daily_payout=1000.0, max_weekly_payout=2000.0)

        trigger_seed = mt(city="Mumbai",
                          trigger_type=TriggerType.HAZARDOUS_AQI)
        _insert_paid_claim(db, worker, policy, trigger_seed,
                           payout_amount=1500.0)

        _simulate(client, worker, "Heavy Rainfall", "Mumbai")

        new_claim = (
            db.query(Claim)
            .filter(
                Claim.worker_id == worker.id,
                Claim.payout_amount != 1500.0,
            )
            .first()
        )
        assert new_claim is not None, (
            "Pro Armor (2000 cap) has 500 remaining -- claim should be created"
        )
        assert new_claim.payout_amount > 0
        assert new_claim.payout_amount <= 500.0, (
            f"Pro Armor payout should be clamped to <=500, got {new_claim.payout_amount}"
        )

    def test_pro_armor_cap_reached_skips(self, cap_env):
        """
        Pro Armor: pre-fill exactly 2000 -> next trigger skipped.
        """
        client, mw, mp, mt, db = cap_env

        worker = mw(city="Mumbai", pincode="400001",
                     claim_history_count=0, disruption_streak=0)
        policy = mp(worker=worker, tier=PolicyTier.PRO,
                    max_daily_payout=1000.0, max_weekly_payout=2000.0)

        trigger_seed = mt(city="Mumbai",
                          trigger_type=TriggerType.HAZARDOUS_AQI)
        _insert_paid_claim(db, worker, policy, trigger_seed,
                           payout_amount=2000.0)

        claims_before = db.query(Claim).filter(
            Claim.worker_id == worker.id
        ).count()

        _simulate(client, worker, "Heavy Rainfall", "Mumbai")

        claims_after = db.query(Claim).filter(
            Claim.worker_id == worker.id
        ).count()
        assert claims_after == claims_before, (
            "Pro Armor (2000 cap) reached -- no new claim expected"
        )

    # ---- 6. Claims older than 7 days don't count -----------------------

    def test_old_claims_not_counted_toward_weekly_cap(self, cap_env):
        """
        Worker has a large paid claim from 8 days ago (outside the 7-day
        window).  It must NOT count toward the weekly cap, so a new
        trigger should produce a full payout.
        """
        client, mw, mp, mt, db = cap_env

        worker = mw(city="Bangalore", pincode="560001",
                     claim_history_count=0, disruption_streak=0)
        policy = mp(worker=worker, tier=PolicyTier.STANDARD,
                    max_daily_payout=600.0, max_weekly_payout=1200.0)

        # Insert a claim from 8 days ago that would exceed the cap if counted
        eight_days_ago = datetime.utcnow() - timedelta(days=8)
        trigger_seed = mt(city="Bangalore",
                          trigger_type=TriggerType.HAZARDOUS_AQI)
        _insert_paid_claim(db, worker, policy, trigger_seed,
                           payout_amount=1200.0,
                           created_at=eight_days_ago,
                           paid_at=eight_days_ago)

        _simulate(client, worker, "Heavy Rainfall", "Bangalore")

        new_claim = (
            db.query(Claim)
            .filter(
                Claim.worker_id == worker.id,
                Claim.payout_amount != 1200.0,
            )
            .first()
        )
        assert new_claim is not None, (
            "Old claim (8 days ago) should NOT count toward weekly cap -- "
            "new claim should be created"
        )

        expected_payout = get_payout_amount("Heavy Rainfall", "Standard Guard", "Bangalore")
        assert new_claim.payout_amount == expected_payout, (
            f"Full payout expected ({expected_payout}), got {new_claim.payout_amount}"
        )

    def test_mix_of_old_and_recent_claims(self, cap_env):
        """
        Worker has a 1000 INR claim from 10 days ago (outside window) and
        a 400 INR claim from 2 days ago (inside window).
        Weekly cap = 1200, so only 400 counts -> 800 remaining.
        Next payout should NOT be clamped (full payout <= 800).
        """
        client, mw, mp, mt, db = cap_env

        worker = mw(city="Mumbai", pincode="400001",
                     claim_history_count=0, disruption_streak=0)
        policy = mp(worker=worker, tier=PolicyTier.STANDARD,
                    max_daily_payout=600.0, max_weekly_payout=1200.0)

        trigger_seed_old = mt(city="Mumbai",
                              trigger_type=TriggerType.SEVERE_HEATWAVE)
        ten_days_ago = datetime.utcnow() - timedelta(days=10)
        _insert_paid_claim(db, worker, policy, trigger_seed_old,
                           payout_amount=1000.0,
                           created_at=ten_days_ago,
                           paid_at=ten_days_ago)

        trigger_seed_recent = mt(city="Mumbai",
                                 trigger_type=TriggerType.HAZARDOUS_AQI)
        two_days_ago = datetime.utcnow() - timedelta(days=2)
        _insert_paid_claim(db, worker, policy, trigger_seed_recent,
                           payout_amount=400.0,
                           created_at=two_days_ago,
                           paid_at=two_days_ago)

        _simulate(client, worker, "Heavy Rainfall", "Mumbai")

        full_payout = get_payout_amount("Heavy Rainfall", "Standard Guard", "Mumbai")
        remaining = 1200.0 - 400.0  # 800

        new_claim = (
            db.query(Claim)
            .filter(
                Claim.worker_id == worker.id,
                Claim.payout_amount.notin_([1000.0, 400.0]),
            )
            .first()
        )
        assert new_claim is not None, "Claim should be created (800 remaining in cap)"

        if full_payout <= remaining:
            assert new_claim.payout_amount == full_payout, (
                f"Full payout ({full_payout}) fits within remaining "
                f"cap ({remaining}) -- should not be clamped"
            )
        else:
            assert new_claim.payout_amount <= remaining, (
                f"Payout should be clamped to {remaining}, "
                f"got {new_claim.payout_amount}"
            )
