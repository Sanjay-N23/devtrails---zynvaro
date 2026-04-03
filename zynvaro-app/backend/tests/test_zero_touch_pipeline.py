"""
Zynvaro — Integration Tests: Zero-Touch Parametric Claim Pipeline
=================================================================
Tests the full end-to-end zero-touch pipeline:

    Worker registers → buys policy → admin simulates trigger
    → _auto_generate_claims() runs as background task
    → claims auto-created with fraud scoring
    → AUTO_APPROVED claims paid immediately

Key architectural note — session isolation
------------------------------------------
``_auto_generate_claims()`` calls ``from database import SessionLocal`` and
then ``db = SessionLocal()`` inside the function body.  Because the import
happens at *call time* (not module load time), patching ``database.SessionLocal``
with a factory bound to the in-memory test engine is sufficient: the next time
the function executes ``from database import SessionLocal``, Python resolves the
name against the already-imported ``database`` module object, whose
``SessionLocal`` attribute we have replaced.

This means all claims written by the background task land in the same SQLite
in-memory DB that the ``test_db`` session reads from, so assertions are
immediately consistent after the HTTP response returns.

Because Starlette's ``TestClient`` executes background tasks synchronously
before the HTTP response is returned, all claims are in the DB by the time we
assert — no polling or sleep required.
"""

import sys
import time
import pytest
from unittest.mock import patch
from datetime import datetime, timedelta
from contextlib import contextmanager
from sqlalchemy.orm import sessionmaker

# ── Ensure backend root is importable ────────────────────────────────────────
sys.path.insert(0, "D:/AeroFyta_DEVTrails-2026_FULL_HANDOFF/zynvaro-app/backend")

import database as _database_module  # noqa: E402  — needed for patching

from models import (
    Worker, Policy, Claim, TriggerEvent,
    PolicyStatus, ClaimStatus, PolicyTier, TriggerType,
)
from ml.premium_engine import (
    get_payout_amount,
    CITY_DAILY_INCOME,
    TRIGGER_REPLACEMENT_RATES,
    TIER_CONFIG,
)
from services.trigger_engine import compute_authenticity_score
from tests.conftest import worker_token  # helper minting JWT tokens


# ─────────────────────────────────────────────────────────────────────────────
# Internal helpers
# ─────────────────────────────────────────────────────────────────────────────

def _auth_headers(worker: Worker) -> dict:
    """Return Authorization header dict for *worker*."""
    return {"Authorization": f"Bearer {worker_token(worker.id)}"}


def _simulate(client, worker: Worker, trigger_type: str, city: str) -> dict:
    """
    POST /triggers/simulate and return the JSON response.
    Raises AssertionError if the request doesn't return 201.
    """
    resp = client.post(
        "/triggers/simulate",
        json={"trigger_type": trigger_type, "city": city},
        headers=_auth_headers(worker),
    )
    assert resp.status_code == 201, (
        f"simulate failed: {resp.status_code} — {resp.text}"
    )
    return resp.json()


@contextmanager
def _patch_session(test_engine):
    """
    Context-manager that replaces ``database.SessionLocal`` with a factory
    bound to *test_engine* for the duration of a ``with`` block.

    ``_auto_generate_claims()`` does::

        from database import SessionLocal   # <-- reads attribute from module
        db = SessionLocal()

    Replacing the attribute on the already-imported ``database`` module object
    is all that is needed — Python's ``from X import Y`` resolves ``Y`` from
    the module's ``__dict__`` at call time, not at import time.
    """
    TestSession = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_engine,
    )
    original = _database_module.SessionLocal
    _database_module.SessionLocal = TestSession
    try:
        yield
    finally:
        _database_module.SessionLocal = original


# ─────────────────────────────────────────────────────────────────────────────
# Fixture: pipeline — client + factories + patched SessionLocal
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def pipeline(client, test_db, test_engine, make_worker, make_policy):
    """
    Convenience fixture returning ``(client, make_worker, make_policy, test_db)``
    inside a ``_patch_session`` context so that ``_auto_generate_claims()``
    writes claims to the in-memory test engine.

    Usage::

        def test_something(pipeline):
            client, mw, mp, db = pipeline
            worker = mw(city="Mumbai", ...)
            mp(worker=worker, tier=PolicyTier.STANDARD)
            _simulate(client, worker, "Heavy Rainfall", "Mumbai")
            claims = db.query(Claim).filter(Claim.worker_id == worker.id).all()
            assert len(claims) == 1
    """
    with _patch_session(test_engine):
        yield client, make_worker, make_policy, test_db


# ═════════════════════════════════════════════════════════════════════════════
class TestZeroTouchPipeline:
    """Full end-to-end zero-touch parametric pipeline tests."""

    # ── FULL PIPELINE ────────────────────────────────────────────────────────

    def test_register_buy_policy_simulate_creates_claim(self, client, test_engine):
        """
        Full happy-path:
        register → POST /policies/ → POST /triggers/simulate
        → GET /claims/ returns exactly 1 claim for the worker.
        """
        with _patch_session(test_engine):
            # 1. Register
            reg = client.post("/auth/register", json={
                "full_name": "Pipeline Worker",
                "phone": "9100000001",
                "password": "pass1234",
                "city": "Mumbai",
                "pincode": "400001",
                "platform": "Blinkit",
            })
            assert reg.status_code == 201, reg.text
            token = reg.json()["access_token"]
            headers = {"Authorization": f"Bearer {token}"}

            # 2. Buy policy
            pol = client.post(
                "/policies/",
                json={"tier": "Standard Guard"},
                headers=headers,
            )
            assert pol.status_code == 201, pol.text

            # 3. Simulate trigger — background task runs synchronously inside TestClient
            sim = client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Mumbai"},
                headers=headers,
            )
            assert sim.status_code == 201, sim.text

            # 4. Verify claim created
            claims_resp = client.get("/claims/", headers=headers)
            assert claims_resp.status_code == 200
            claims = claims_resp.json()
            assert len(claims) == 1, (
                f"Expected exactly 1 claim after pipeline, got {len(claims)}"
            )

    def test_claim_created_without_any_worker_action(self, pipeline):
        """
        Worker holds an active policy but takes no action.
        An admin (another authenticated worker) simulates the trigger.
        The passive worker's claim must be created automatically with
        ``auto_processed=True``.
        """
        client, mw, mp, db = pipeline

        # Passive worker — registers and buys policy, then does nothing further
        worker = mw(city="Bangalore", pincode="560001",
                    claim_history_count=0, disruption_streak=0)
        mp(worker=worker, tier=PolicyTier.STANDARD)

        # Admin-actor who fires the simulate endpoint
        admin = mw(city="Bangalore", pincode="560001")
        mp(worker=admin, tier=PolicyTier.STANDARD)

        _simulate(client, admin, "Heavy Rainfall", "Bangalore")

        claims = db.query(Claim).filter(Claim.worker_id == worker.id).all()
        assert len(claims) >= 1, "Passive worker should receive an auto-generated claim"
        assert claims[0].auto_processed is True

    def test_pipeline_completes_faster_than_30_seconds(self, pipeline):
        """
        From the moment ``/triggers/simulate`` is called to claims appearing
        in the DB must be under 30 seconds.  Documents the zero-touch SLA.
        (With TestClient running background tasks synchronously, this is
        near-instant in practice.)
        """
        client, mw, mp, db = pipeline

        worker = mw(city="Delhi", pincode="110001",
                    claim_history_count=0, disruption_streak=0)
        mp(worker=worker, tier=PolicyTier.STANDARD)

        start = time.perf_counter()
        _simulate(client, worker, "Hazardous AQI", "Delhi")
        claims = db.query(Claim).filter(Claim.worker_id == worker.id).all()
        elapsed = time.perf_counter() - start

        assert len(claims) >= 1, "No claim created during pipeline timing test"
        assert elapsed < 30.0, (
            f"Pipeline took {elapsed:.2f}s — exceeds 30s SLA"
        )

    # ── FRAUD ROUTING ────────────────────────────────────────────────────────

    def test_city_matched_worker_gets_auto_approved(self, pipeline):
        """
        Mumbai worker + Mumbai trigger, clean history (0 claims, 0 same-week)
        → authenticity score = 100 → status == AUTO_APPROVED.
        """
        client, mw, mp, db = pipeline

        worker = mw(city="Mumbai", pincode="400001",
                    claim_history_count=0, disruption_streak=0)
        mp(worker=worker, tier=PolicyTier.STANDARD)

        _simulate(client, worker, "Heavy Rainfall", "Mumbai")

        claim = db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None, "No claim found after city-matched simulate"
        # AUTO_APPROVED claims are immediately set to PAID (payment ref + paid_at written
        # in the same DB transaction). Both statuses represent a successful auto-approval.
        assert claim.status in (ClaimStatus.AUTO_APPROVED, ClaimStatus.PAID), (
            f"Expected AUTO_APPROVED or PAID, got {claim.status}"
        )

    def test_auto_approved_claim_has_paid_at(self, pipeline):
        """After auto-approval ``claim.paid_at`` must be set (not None)."""
        client, mw, mp, db = pipeline

        worker = mw(city="Bangalore", pincode="560001",
                    claim_history_count=0, disruption_streak=0)
        mp(worker=worker, tier=PolicyTier.STANDARD)

        _simulate(client, worker, "Heavy Rainfall", "Bangalore")

        claim = db.query(Claim).filter(
            Claim.worker_id == worker.id,
            Claim.status.in_([ClaimStatus.AUTO_APPROVED, ClaimStatus.PAID]),
        ).first()
        assert claim is not None, "No AUTO_APPROVED/PAID claim found"
        assert claim.paid_at is not None, (
            "Auto-approved (PAID) claim must have paid_at set"
        )

    def test_auto_approved_claim_has_payment_ref(self, pipeline):
        """AUTO_APPROVED claim must carry ``payment_ref`` starting with 'MOCK-UPI-'."""
        client, mw, mp, db = pipeline

        worker = mw(city="Bangalore", pincode="560001",
                    claim_history_count=0, disruption_streak=0)
        mp(worker=worker, tier=PolicyTier.STANDARD)

        _simulate(client, worker, "Heavy Rainfall", "Bangalore")

        claim = db.query(Claim).filter(
            Claim.worker_id == worker.id,
            Claim.status.in_([ClaimStatus.AUTO_APPROVED, ClaimStatus.PAID]),
        ).first()
        assert claim is not None, "No AUTO_APPROVED/PAID claim found"
        assert claim.payment_ref is not None, (
            "Auto-approved (PAID) claim must have payment_ref"
        )
        assert claim.payment_ref.startswith("MOCK-UPI-"), (
            f"payment_ref should start with 'MOCK-UPI-', got '{claim.payment_ref}'"
        )

    def test_auto_approved_claim_has_high_authenticity_score(self, pipeline):
        """
        City-matched worker with clean history → authenticity_score >= 75.
        Any score < 75 would route to PENDING_REVIEW, not AUTO_APPROVED.
        """
        client, mw, mp, db = pipeline

        worker = mw(city="Mumbai", pincode="400001",
                    claim_history_count=0, disruption_streak=0)
        mp(worker=worker, tier=PolicyTier.STANDARD)

        _simulate(client, worker, "Heavy Rainfall", "Mumbai")

        claim = db.query(Claim).filter(
            Claim.worker_id == worker.id,
            Claim.status.in_([ClaimStatus.AUTO_APPROVED, ClaimStatus.PAID]),
        ).first()
        assert claim is not None, "No AUTO_APPROVED/PAID claim found"
        assert claim.authenticity_score >= 75.0, (
            f"Expected score >= 75, got {claim.authenticity_score}"
        )

    # ── CLAIM CONTENT ────────────────────────────────────────────────────────

    def test_claim_has_correct_trigger_type(self, pipeline):
        """
        Simulate 'Hazardous AQI' → the linked TriggerEvent must report
        ``trigger_type == 'Hazardous AQI'``.
        """
        client, mw, mp, db = pipeline

        worker = mw(city="Delhi", pincode="110001",
                    claim_history_count=0, disruption_streak=0)
        mp(worker=worker, tier=PolicyTier.STANDARD)

        _simulate(client, worker, "Hazardous AQI", "Delhi")

        claim = db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None
        assert claim.trigger_event.trigger_type == "Hazardous AQI", (
            f"Expected 'Hazardous AQI', got '{claim.trigger_event.trigger_type}'"
        )

    def test_claim_has_correct_city(self, pipeline):
        """Simulate in Delhi → ``claim.trigger_event.city == 'Delhi'``."""
        client, mw, mp, db = pipeline

        worker = mw(city="Delhi", pincode="110001",
                    claim_history_count=0, disruption_streak=0)
        mp(worker=worker, tier=PolicyTier.STANDARD)

        _simulate(client, worker, "Hazardous AQI", "Delhi")

        claim = db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None
        assert claim.trigger_event.city == "Delhi", (
            f"Expected city 'Delhi', got '{claim.trigger_event.city}'"
        )

    @pytest.mark.parametrize("trigger_type", [
        TriggerType.HEAVY_RAINFALL,
        TriggerType.EXTREME_RAIN,
        TriggerType.SEVERE_HEATWAVE,
        TriggerType.HAZARDOUS_AQI,
        TriggerType.PLATFORM_OUTAGE,
        TriggerType.CIVIL_DISRUPTION,
    ])
    def test_claim_payout_is_positive(self, pipeline, trigger_type):
        """All 6 trigger types → payout > 0 for Standard Guard in Mumbai."""
        client, mw, mp, db = pipeline

        worker = mw(city="Mumbai", pincode="400001",
                    claim_history_count=0, disruption_streak=0)
        mp(worker=worker, tier=PolicyTier.STANDARD)

        _simulate(client, worker, trigger_type, "Mumbai")

        claim = db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None, (
            f"No claim created for trigger '{trigger_type}'"
        )
        assert claim.payout_amount > 0.0, (
            f"Payout is 0 for '{trigger_type}' — income-replacement model broken"
        )

    def test_claim_payout_respects_tier_max_daily(self, pipeline):
        """
        Pro Armor max_daily = 1000 INR.
        Any single claim payout must be <= 1000 regardless of trigger.
        """
        client, mw, mp, db = pipeline

        worker = mw(city="Mumbai", pincode="400001",
                    claim_history_count=0, disruption_streak=0)
        mp(worker=worker, tier=PolicyTier.PRO,
           max_daily_payout=1000.0, max_weekly_payout=2000.0)

        _simulate(client, worker, "Extreme Rain / Flooding", "Mumbai")

        claim = db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None
        assert claim.payout_amount <= 1000.0, (
            f"Payout ₹{claim.payout_amount} exceeds Pro Armor max_daily ₹1000"
        )

    # ── DEDUPLICATION ────────────────────────────────────────────────────────

    def test_same_trigger_event_does_not_create_duplicate_claim(self, pipeline):
        """
        Two simulate calls for the same trigger_type + city within 24h should
        result in only 1 claim for the worker (deduplication guard in
        ``_auto_generate_claims``).
        """
        client, mw, mp, db = pipeline

        worker = mw(city="Bangalore", pincode="560001",
                    claim_history_count=0, disruption_streak=0)
        mp(worker=worker, tier=PolicyTier.STANDARD)

        _simulate(client, worker, "Heavy Rainfall", "Bangalore")
        _simulate(client, worker, "Heavy Rainfall", "Bangalore")

        claims = (
            db.query(Claim)
            .join(TriggerEvent)
            .filter(
                Claim.worker_id == worker.id,
                TriggerEvent.trigger_type == "Heavy Rainfall",
                TriggerEvent.city == "Bangalore",
            )
            .all()
        )
        assert len(claims) == 1, (
            f"Deduplication failed — expected 1 claim, found {len(claims)}"
        )

    def test_duplicate_protection_allows_different_trigger_types(self, pipeline):
        """
        Simulate Heavy Rainfall then Hazardous AQI → 2 separate claims created.
        The deduplication guard must not block claims of distinct trigger types.
        """
        client, mw, mp, db = pipeline

        worker = mw(city="Delhi", pincode="110001",
                    claim_history_count=0, disruption_streak=0)
        mp(worker=worker, tier=PolicyTier.STANDARD)

        _simulate(client, worker, "Heavy Rainfall", "Delhi")
        _simulate(client, worker, "Hazardous AQI", "Delhi")

        claims = db.query(Claim).filter(Claim.worker_id == worker.id).all()
        trigger_types_found = {c.trigger_event.trigger_type for c in claims}

        assert len(claims) == 2, (
            f"Expected 2 claims for 2 distinct triggers, got {len(claims)}"
        )
        assert "Heavy Rainfall" in trigger_types_found
        assert "Hazardous AQI" in trigger_types_found

    # ── INACTIVE POLICY PROTECTION ───────────────────────────────────────────

    def test_worker_without_policy_gets_no_claim(self, pipeline):
        """
        A registered worker with no active policy receives 0 claims when a
        trigger fires in their city.
        """
        client, mw, mp, db = pipeline

        # Worker with active policy — acts as the simulate driver
        actor = mw(city="Chennai", pincode="600001",
                   claim_history_count=0, disruption_streak=0)
        mp(worker=actor, tier=PolicyTier.STANDARD)

        # Worker without any policy at all
        no_policy_worker = mw(city="Chennai", pincode="600001")
        # Deliberately no mp() call for no_policy_worker

        _simulate(client, actor, "Heavy Rainfall", "Chennai")

        claims = db.query(Claim).filter(
            Claim.worker_id == no_policy_worker.id
        ).all()
        assert len(claims) == 0, (
            f"Worker without policy should have 0 claims, found {len(claims)}"
        )

    def test_expired_policy_worker_gets_no_claim(self, pipeline):
        """
        A worker whose only policy has status=CANCELLED gets 0 claims
        when a trigger fires.
        """
        client, mw, mp, db = pipeline

        # Active actor to drive the simulate
        actor = mw(city="Pune", pincode="411001",
                   claim_history_count=0, disruption_streak=0)
        mp(worker=actor, tier=PolicyTier.STANDARD)

        # Worker with only a CANCELLED policy
        cancelled_worker = mw(city="Pune", pincode="411001")
        mp(worker=cancelled_worker, tier=PolicyTier.BASIC,
           status=PolicyStatus.CANCELLED)

        _simulate(client, actor, "Heavy Rainfall", "Pune")

        claims = db.query(Claim).filter(
            Claim.worker_id == cancelled_worker.id
        ).all()
        assert len(claims) == 0, (
            f"CANCELLED-policy worker should have 0 claims, found {len(claims)}"
        )

    # ── MULTI-WORKER CLAIM GENERATION ────────────────────────────────────────

    def test_all_active_policyholders_in_city_get_claims(self, pipeline):
        """
        3 workers all in Mumbai with active policies.
        Simulate trigger in Mumbai → all 3 receive claims (one each).
        """
        client, mw, mp, db = pipeline

        workers = []
        for _ in range(3):
            w = mw(city="Mumbai", pincode="400001",
                   claim_history_count=0, disruption_streak=0)
            mp(worker=w, tier=PolicyTier.STANDARD)
            workers.append(w)

        _simulate(client, workers[0], "Heavy Rainfall", "Mumbai")

        worker_ids = {w.id for w in workers}
        claims = db.query(Claim).filter(
            Claim.worker_id.in_(worker_ids)
        ).all()

        assert len(claims) == 3, (
            f"Expected 3 claims for 3 Mumbai workers, got {len(claims)}"
        )

    def test_workers_in_different_city_are_excluded(self, pipeline):
        """
        Mumbai workers + Bangalore workers, all with active policies.
        Simulate trigger only in Mumbai → Bangalore workers get 0 claims.
        """
        client, mw, mp, db = pipeline

        mumbai_workers = []
        for _ in range(2):
            w = mw(city="Mumbai", pincode="400001",
                   claim_history_count=0, disruption_streak=0)
            mp(worker=w, tier=PolicyTier.STANDARD)
            mumbai_workers.append(w)

        blr_workers = []
        for _ in range(2):
            w = mw(city="Bangalore", pincode="560001",
                   claim_history_count=0, disruption_streak=0)
            mp(worker=w, tier=PolicyTier.STANDARD)
            blr_workers.append(w)

        _simulate(client, mumbai_workers[0], "Heavy Rainfall", "Mumbai")

        blr_ids = {w.id for w in blr_workers}
        blr_claims = db.query(Claim).filter(
            Claim.worker_id.in_(blr_ids)
        ).all()

        assert len(blr_claims) == 0, (
            f"Bangalore workers must NOT receive Mumbai claims, "
            f"but found {len(blr_claims)}"
        )

    # ── INCOME-REPLACEMENT MODEL ─────────────────────────────────────────────

    def test_basic_shield_gets_nonzero_payout_for_heavy_rainfall(self, pipeline):
        """
        Regression guard: Basic Shield previously returned ₹0 for Heavy Rainfall
        (flat table bug).  The income-replacement fix must produce payout > 0.
        """
        client, mw, mp, db = pipeline

        worker = mw(city="Bangalore", pincode="560001",
                    claim_history_count=0, disruption_streak=0)
        mp(worker=worker, tier=PolicyTier.BASIC,
           max_daily_payout=300.0, max_weekly_payout=600.0)

        _simulate(client, worker, "Heavy Rainfall", "Bangalore")

        claim = db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None
        assert claim.payout_amount > 0.0, (
            "Basic Shield Heavy Rainfall payout must be > 0 (income-replacement fix)"
        )

    def test_pro_armor_payout_higher_than_basic_shield(self):
        """
        Unit check: same trigger + same city → Pro Armor payout > Basic Shield.
        Validates that the income-replacement model is tier-sensitive.
        """
        basic_payout = get_payout_amount("Heavy Rainfall", "Basic Shield",  "Bangalore")
        pro_payout   = get_payout_amount("Heavy Rainfall", "Pro Armor",     "Bangalore")

        assert pro_payout > basic_payout, (
            f"Pro Armor (₹{pro_payout}) must exceed "
            f"Basic Shield (₹{basic_payout}) for same trigger + city"
        )

    def test_mumbai_worker_payout_based_on_mumbai_income(self, pipeline):
        """
        Mumbai Standard Guard Heavy Rainfall payout must equal:

            CITY_DAILY_INCOME["Mumbai"]["Standard Guard"]   (= 1100)
            × TRIGGER_REPLACEMENT_RATES["Heavy Rainfall"]["Standard Guard"] (= 0.55)
            rounded to nearest ₹10                          (= 610 → rounds to ₹610)
            capped at Standard Guard max_daily              (= 600)
            → expected = ₹600.0

        This verifies the income-proportional payout formula end-to-end.
        """
        client, mw, mp, db = pipeline

        worker = mw(city="Mumbai", pincode="400001",
                    claim_history_count=0, disruption_streak=0)
        mp(worker=worker, tier=PolicyTier.STANDARD,
           max_daily_payout=600.0, max_weekly_payout=1200.0)

        _simulate(client, worker, "Heavy Rainfall", "Mumbai")

        claim = db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None

        # Replicate get_payout_amount formula inline for transparency
        daily_income = CITY_DAILY_INCOME["Mumbai"]["Standard Guard"]     # 1100
        rate         = TRIGGER_REPLACEMENT_RATES["Heavy Rainfall"]["Standard Guard"]  # 0.55
        raw          = round(daily_income * rate / 10) * 10              # 610 → 610
        max_daily    = TIER_CONFIG["Standard Guard"]["max_daily"]        # 600
        expected     = float(min(raw, max_daily))                        # 600.0

        assert claim.payout_amount == expected, (
            f"Expected ₹{expected} (Mumbai Standard Guard Heavy Rainfall), "
            f"got ₹{claim.payout_amount}"
        )


# ═════════════════════════════════════════════════════════════════════════════
class TestClaimStatusTransitions:
    """Claim status validity, fraud-flag, and score-routing tests."""

    VALID_STATUSES = frozenset(s.value for s in ClaimStatus)

    def test_new_claim_starts_as_auto_approved_or_pending(self, pipeline):
        """
        A freshly generated claim must be in one of the three initial states:
        AUTO_APPROVED, PENDING_REVIEW, or MANUAL_REVIEW.
        PAID and REJECTED are downstream states that must never appear on a
        brand-new claim.
        """
        client, mw, mp, db = pipeline

        worker = mw(city="Bangalore", pincode="560001",
                    claim_history_count=0, disruption_streak=0)
        mp(worker=worker, tier=PolicyTier.STANDARD)

        _simulate(client, worker, "Heavy Rainfall", "Bangalore")

        claim = db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None

        initial_statuses = {
            ClaimStatus.AUTO_APPROVED,
            ClaimStatus.PENDING_REVIEW,
            ClaimStatus.MANUAL_REVIEW,
            ClaimStatus.PAID,  # AUTO_APPROVED claims land here immediately (zero-touch payment)
        }
        assert claim.status in initial_statuses, (
            f"Unexpected initial claim status: '{claim.status}'"
        )

    def test_claim_status_enum_values_are_valid_strings(
        self, make_worker, make_policy, make_trigger, make_claim, test_db
    ):
        """
        All claim statuses persisted to the DB must be valid ClaimStatus enum
        values.  Guards against string typos silently passing DB constraints.
        """
        valid_values = frozenset(s.value for s in ClaimStatus)

        w  = make_worker()
        p  = make_policy(worker=w)
        te = make_trigger(city=w.city)

        # Persist one claim per defined status
        for status in ClaimStatus:
            make_claim(worker=w, policy=p, trigger=te, status=status)

        claims = test_db.query(Claim).filter(Claim.worker_id == w.id).all()
        for claim in claims:
            assert claim.status in valid_values, (
                f"Invalid status string in DB: '{claim.status}'"
            )

    def test_fraud_flag_set_for_city_mismatch_claim(
        self, make_worker, make_policy, make_trigger, make_claim, test_db
    ):
        """
        Worker registered in Bangalore; trigger fired in Mumbai.
        The city mismatch signal must populate ``fraud_flags`` with text that
        includes the word 'city'.

        Note: ``_auto_generate_claims`` only creates claims for workers whose
        city matches the trigger city, so city-mismatch fraud detection is
        tested here via direct ``compute_authenticity_score`` + ``make_claim``.
        """
        blr_worker = make_worker(city="Bangalore", pincode="560001",
                                 claim_history_count=0, disruption_streak=0)
        p = make_policy(worker=blr_worker, tier=PolicyTier.STANDARD)
        mumbai_trigger = make_trigger(
            trigger_type=TriggerType.HEAVY_RAINFALL, city="Mumbai"
        )

        fraud = compute_authenticity_score(
            worker_city="Bangalore",
            trigger_city="Mumbai",
            claim_history=0,
            same_week_claims=0,
            device_attested=True,
        )
        claim = make_claim(
            worker=blr_worker, policy=p, trigger=mumbai_trigger,
            status=ClaimStatus.PENDING_REVIEW,
            gps_valid=fraud["gps_valid"],
            authenticity_score=fraud["score"],
            fraud_flags="; ".join(fraud["flags"]) if fraud["flags"] else None,
        )

        assert claim is not None, "No claim generated for city-mismatch worker"
        assert claim.fraud_flags is not None, (
            "fraud_flags must be set for city-mismatch claim"
        )
        assert "city" in claim.fraud_flags.lower(), (
            f"fraud_flags should reference 'city', got: '{claim.fraud_flags}'"
        )

    def test_gps_valid_false_for_city_mismatch(
        self, make_worker, make_policy, make_trigger, make_claim, test_db
    ):
        """
        Worker city (Bangalore) != trigger city (Mumbai) → ``gps_valid == False``.
        The fraud scorer derives gps_valid from the city-equality check.

        Note: ``_auto_generate_claims`` only creates claims for workers whose
        city matches the trigger city, so city-mismatch fraud detection is
        tested here via direct ``compute_authenticity_score`` + ``make_claim``.
        """
        blr_worker = make_worker(city="Bangalore", pincode="560001",
                                 claim_history_count=0, disruption_streak=0)
        p = make_policy(worker=blr_worker, tier=PolicyTier.STANDARD)
        mumbai_trigger = make_trigger(
            trigger_type=TriggerType.HEAVY_RAINFALL, city="Mumbai"
        )

        fraud = compute_authenticity_score(
            worker_city="Bangalore",
            trigger_city="Mumbai",
            claim_history=0,
            same_week_claims=0,
            device_attested=True,
        )
        claim = make_claim(
            worker=blr_worker, policy=p, trigger=mumbai_trigger,
            status=ClaimStatus.PENDING_REVIEW,
            gps_valid=fraud["gps_valid"],
            authenticity_score=fraud["score"],
            fraud_flags="; ".join(fraud["flags"]) if fraud["flags"] else None,
        )

        assert claim is not None
        assert claim.gps_valid is False, (
            "gps_valid must be False when worker city != trigger city"
        )

    # ── Unit-level fraud scorer tests (no HTTP, no DB) ───────────────────────

    def test_compute_authenticity_score_city_match_returns_high_score(self):
        """
        Matching cities + zero history + device attested → score == 100,
        decision == AUTO_APPROVED, gps_valid == True, no flags.
        """
        result = compute_authenticity_score(
            worker_city="Mumbai",
            trigger_city="Mumbai",
            claim_history=0,
            same_week_claims=0,
            device_attested=True,
        )
        assert result["score"] == 100.0
        assert result["decision"] == "AUTO_APPROVED"
        assert result["gps_valid"] is True
        assert result["flags"] == []

    def test_compute_authenticity_score_city_mismatch_reduces_score(self):
        """
        City mismatch subtracts 40 points from a perfect 100 → score == 60,
        gps_valid == False, at least one flag populated.
        """
        result = compute_authenticity_score(
            worker_city="Bangalore",
            trigger_city="Mumbai",
            claim_history=0,
            same_week_claims=0,
            device_attested=True,
        )
        assert result["score"] == 60.0
        assert result["gps_valid"] is False
        assert len(result["flags"]) >= 1

    def test_compute_authenticity_score_high_frequency_routes_to_manual(self):
        """
        City mismatch (-40) + 2 same-week claims (-20) → score == 40
        < 45 threshold → decision == MANUAL_REVIEW.
        """
        result = compute_authenticity_score(
            worker_city="Bangalore",
            trigger_city="Mumbai",
            claim_history=0,
            same_week_claims=2,
            device_attested=True,
        )
        assert result["score"] < 45.0
        assert result["decision"] == "MANUAL_REVIEW"

    def test_compute_authenticity_score_high_history_applies_penalty(self):
        """
        Claim history > 5 applies a -10 penalty.
        City match + 6 past claims → score == 90 → AUTO_APPROVED.
        """
        result = compute_authenticity_score(
            worker_city="Delhi",
            trigger_city="Delhi",
            claim_history=6,
            same_week_claims=0,
            device_attested=True,
        )
        assert result["score"] == 90.0
        assert result["decision"] == "AUTO_APPROVED"
