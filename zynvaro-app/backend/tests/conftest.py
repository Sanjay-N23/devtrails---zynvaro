"""
Zynvaro Backend — pytest conftest.py
=====================================
Provides fixtures for testing the FastAPI + SQLAlchemy + SQLite backend.

Fixture hierarchy:
    test_engine (session-scoped)
        └── test_db (function-scoped)
                ├── client (function-scoped)
                ├── authed_client (function-scoped)
                ├── make_worker(db) factory
                ├── make_policy(db, worker) factory
                ├── make_trigger(db) factory
                └── make_claim(db, worker, policy, trigger) factory

JWT tokens use the same SECRET_KEY and algorithm as routers/auth.py.
All tests run against an isolated in-memory SQLite database — the
production zynvaro.db file is never touched.
"""

import sys
import os
from datetime import datetime, timedelta

# ── Set SECRET_KEY env var BEFORE any app imports, so auth.py picks it up
#    instead of generating a random key that won't match test tokens. ──────
os.environ.setdefault("SECRET_KEY", "zynvaro-secret-2026-hackathon-key")

# ── Disable Razorpay in tests — always use mock payouts ──────────
os.environ["RAZORPAY_KEY_ID"] = ""
os.environ["RAZORPAY_KEY_SECRET"] = ""

# ── Make sure the backend package root is on sys.path so that relative
#    imports inside database.py, models.py, routers/, etc. resolve
#    correctly when pytest is invoked from any working directory. ──────
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool
from starlette.testclient import TestClient
from jose import jwt
from passlib.context import CryptContext
import json

# ── Internal imports (resolved via sys.path above) ────────────────────
from database import Base, get_db
from main import app
from models import (
    Worker,
    Policy,
    TriggerEvent,
    Claim,
    PayoutTransaction,
    PolicyStatus,
    ClaimStatus,
    TriggerType,
    PolicyTier,
    PayoutTransactionStatus,
    TransactionType,
)

# ─────────────────────────────────────────────────────────────────────
# Constants — must match routers/auth.py exactly
# ─────────────────────────────────────────────────────────────────────
SECRET_KEY = "zynvaro-secret-2026-hackathon-key"
ALGORITHM = "HS256"
TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours, same as auth.py

_pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
_DEFAULT_LAST_LOCATION = object()
_DEFAULT_CLAIM_FIELD = object()

# ─────────────────────────────────────────────────────────────────────
# Helper — not a fixture, can be imported directly by test modules
# ─────────────────────────────────────────────────────────────────────

def worker_token(worker_id: int) -> str:
    """
    Generate a valid JWT Bearer token for *worker_id*.

    Uses the same SECRET_KEY and HS256 algorithm as routers/auth.py so
    that ``get_current_worker`` will accept it without modification.

    Example::

        headers = {"Authorization": f"Bearer {worker_token(42)}"}
    """
    expire = datetime.utcnow() + timedelta(minutes=TOKEN_EXPIRE_MINUTES)
    payload = {"sub": str(worker_id), "exp": expire}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


# ─────────────────────────────────────────────────────────────────────
# ENGINE — one shared in-memory DB per test session
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def test_engine():
    """
    Session-scoped SQLAlchemy engine backed by an in-memory SQLite DB.

    ``check_same_thread=False`` is required by SQLite when the same
    connection is reused across threads (as TestClient may do).

    All ORM tables are created once at session start and dropped at the
    end, giving fast test startup with full schema coverage.
    """
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Create every table defined via Base.metadata (all models)
    Base.metadata.create_all(bind=engine)
    yield engine
    Base.metadata.drop_all(bind=engine)
    engine.dispose()


# ─────────────────────────────────────────────────────────────────────
# SESSION FACTORY — bound to the test engine
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="session")
def TestingSessionLocal(test_engine):
    """
    Session-scoped sessionmaker bound to *test_engine*.

    Kept as a separate fixture so factory fixtures can share the same
    factory without re-creating it on every function call.
    """
    return sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


# ─────────────────────────────────────────────────────────────────────
# GLOBAL LIVE-API STUB — block real network calls in all unit tests
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function", autouse=True)
def _stub_live_trigger_apis():
    """
    Auto-use fixture that stubs the three live-data functions added to
    trigger_engine.py so tests never make real HTTP calls to WAQI,
    GDELT, or delivery platform domains.

    Returning None from each causes check_all_triggers() to fall back
    to the existing mock_* helpers, which tests already control via patch.

    Tests that need to verify the live-path behaviour can override these
    stubs with their own ``patch`` context managers.
    """
    with patch("services.trigger_engine.fetch_real_aqi",
               new=AsyncMock(return_value=None)), \
         patch("services.trigger_engine.fetch_real_platform_status",
               new=AsyncMock(return_value=None)), \
         patch("services.trigger_engine.fetch_civil_disruption_live",
               new=AsyncMock(return_value=None)):
        yield


# ─────────────────────────────────────────────────────────────────────
# GLOBAL SESSION PATCH — redirect database.SessionLocal to test engine
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function", autouse=True)
def _patch_database_session_local(TestingSessionLocal):
    """
    Auto-use fixture that replaces ``database.SessionLocal`` with the
    test session factory for the duration of every test.

    This ensures that background tasks (e.g. ``_auto_generate_claims``)
    which call ``from database import SessionLocal; db = SessionLocal()``
    use the in-memory test database instead of the production SQLite
    file, preventing cross-test contamination via ``zynvaro.db``.
    """
    import database as _db_module
    original = _db_module.SessionLocal
    _db_module.SessionLocal = TestingSessionLocal
    yield
    _db_module.SessionLocal = original


# ─────────────────────────────────────────────────────────────────────
# DB SESSION — clean slate per test via transaction rollback
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def test_db(test_engine, TestingSessionLocal):
    """
    Function-scoped database session.

    Creates a session for the test and deletes all rows from every table
    after the test completes.  This gives clean-slate isolation without
    the cost of dropping/re-creating the schema, and is compatible with
    StaticPool (which shares a single SQLite connection across all
    sessions — including background-task sessions created by
    ``_auto_generate_claims``).

    Usage::

        def test_something(test_db):
            worker = Worker(full_name="Test", ...)
            test_db.add(worker)
            test_db.commit()
            assert test_db.query(Worker).count() == 1
        # After the test, all rows are deleted — clean state for next test.
    """
    session = TestingSessionLocal()

    yield session

    # Teardown: close the session, then wipe every table in FK-safe order
    session.close()
    with test_engine.begin() as conn:
        for table in reversed(Base.metadata.sorted_tables):
            conn.execute(table.delete())


# ─────────────────────────────────────────────────────────────────────
# TEST CLIENT — FastAPI TestClient with get_db overridden
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def client(test_db):
    """
    Function-scoped FastAPI ``TestClient``.

    Overrides the ``get_db`` dependency so every request made through
    this client uses *test_db* — the same rolled-back session — instead
    of the production ``SessionLocal``.

    The override is installed before each test and removed after to
    prevent state leaking between test functions.

    Usage::

        def test_health(client):
            resp = client.get("/health")
            assert resp.status_code == 200
    """
    def _override_get_db():
        try:
            yield test_db
        finally:
            pass  # cleanup handled by test_db fixture's rollback

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True) as c:
        yield c
    # Restore original dependency after test
    app.dependency_overrides.pop(get_db, None)


# ─────────────────────────────────────────────────────────────────────
# AUTHED CLIENT — TestClient pre-loaded with a valid JWT header
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def authed_client(test_db):
    """
    Function-scoped FastAPI ``TestClient`` with a pre-injected JWT.

    Creates a real Worker row in *test_db*, mints a valid JWT for that
    worker, and attaches it as the default ``Authorization: Bearer``
    header on every request.

    Also exposes the underlying worker as ``authed_client.worker`` so
    tests can introspect the authenticated identity without a separate
    DB query.

    Usage::

        def test_me_endpoint(authed_client):
            resp = authed_client.get("/auth/me")
            assert resp.status_code == 200
            assert resp.json()["id"] == authed_client.worker.id
    """
    def _override_get_db():
        try:
            yield test_db
        finally:
            pass

    # Create the worker the token will represent
    hashed_pw = _pwd_context.hash("testpassword123")
    worker = Worker(
        full_name="Auth Test Worker",
        phone="9000000001",
        email="auth.test@zynvaro.test",
        password_hash=hashed_pw,
        city="Bangalore",
        pincode="560001",
        platform="Blinkit",
        vehicle_type="2-Wheeler",
        shift="Evening Peak (6PM-2AM)",
        zone_risk_score=0.5,
        claim_history_count=0,
        disruption_streak=0,
        is_active=True,
        is_admin=True,  # Admin so tests can access admin endpoints
        last_location_at=datetime.utcnow(),
        last_activity_source="session_ping",
    )
    test_db.add(worker)
    test_db.commit()
    test_db.refresh(worker)

    token = worker_token(worker.id)
    headers = {"Authorization": f"Bearer {token}"}

    app.dependency_overrides[get_db] = _override_get_db
    with TestClient(app, raise_server_exceptions=True, headers=headers) as c:
        # Attach the worker object so tests can reference it conveniently
        c.worker = worker  # type: ignore[attr-defined]
        yield c
    app.dependency_overrides.pop(get_db, None)


# ─────────────────────────────────────────────────────────────────────
# FACTORY FIXTURES
# ─────────────────────────────────────────────────────────────────────

@pytest.fixture(scope="function")
def make_worker(test_db):
    """
    Factory fixture that creates and returns a ``Worker`` ORM object.

    All keyword arguments are optional and fall back to sensible test
    defaults so callers only need to specify what they care about.

    Usage::

        def test_worker_creation(make_worker):
            w = make_worker()
            assert w.id is not None

        def test_custom_city(make_worker):
            w = make_worker(city="Mumbai", phone="9111111111")
            assert w.city == "Mumbai"
    """
    _counter = {"n": 0}

    def _factory(
        full_name: str = "Test Worker",
        phone: str | None = None,
        email: str | None = None,
        password: str = "testpassword123",
        city: str = "Bangalore",
        pincode: str = "560001",
        platform: str = "Blinkit",
        vehicle_type: str = "2-Wheeler",
        shift: str = "Evening Peak (6PM-2AM)",
        zone_risk_score: float = 0.45,
        claim_history_count: int = 0,
        disruption_streak: int = 2,
        is_active: bool = True,
        is_admin: bool = False,
        home_lat: float | None = None,
        home_lng: float | None = None,
        last_known_lat: float | None = None,
        last_known_lng: float | None = None,
        last_location_at=_DEFAULT_LAST_LOCATION,
        last_activity_source: str | None = "session_ping",
    ) -> Worker:
        _counter["n"] += 1
        n = _counter["n"]
        # Auto-generate unique phone/email if not provided so multiple
        # workers can be created within the same test without unique
        # constraint violations.
        effective_phone = phone if phone is not None else f"800000{n:04d}"
        effective_email = email if email is not None else f"worker{n}@zynvaro.test"
        effective_last_location = (
            datetime.utcnow() if last_location_at is _DEFAULT_LAST_LOCATION else last_location_at
        )

        worker = Worker(
            full_name=full_name,
            phone=effective_phone,
            email=effective_email,
            password_hash=_pwd_context.hash(password),
            city=city,
            pincode=pincode,
            platform=platform,
            vehicle_type=vehicle_type,
            shift=shift,
            zone_risk_score=zone_risk_score,
            claim_history_count=claim_history_count,
            disruption_streak=disruption_streak,
            home_lat=home_lat,
            home_lng=home_lng,
            last_known_lat=last_known_lat,
            last_known_lng=last_known_lng,
            last_location_at=effective_last_location,
            last_activity_source=last_activity_source,
            is_active=is_active,
            is_admin=is_admin,
        )
        test_db.add(worker)
        test_db.commit()
        test_db.refresh(worker)
        return worker

    return _factory


@pytest.fixture(scope="function")
def make_policy(test_db):
    """
    Factory fixture that creates and returns a ``Policy`` ORM object.

    Requires an existing Worker (pass the ORM instance).  All other
    arguments have sensible defaults for active Basic Shield coverage.

    Usage::

        def test_policy(make_worker, make_policy):
            w = make_worker()
            p = make_policy(worker=w)
            assert p.status == PolicyStatus.ACTIVE
            assert p.worker_id == w.id
    """
    _counter = {"n": 0}

    def _factory(
        worker: Worker,
        tier: str = PolicyTier.BASIC,
        status: str = PolicyStatus.ACTIVE,
        weekly_premium: float = 49.0,
        base_premium: float = 45.0,
        max_daily_payout: float = 500.0,
        max_weekly_payout: float = 2000.0,
        zone_loading: float = 5.0,
        seasonal_loading: float = 2.0,
        claim_loading: float = 0.0,
        streak_discount: float = 3.0,
        start_date: datetime | None = None,
        end_date: datetime | None = None,
        policy_number: str | None = None,
    ) -> Policy:
        _counter["n"] += 1
        n = _counter["n"]
        effective_number = policy_number if policy_number is not None else f"ZYN-TEST-{n:05d}"
        effective_start = start_date or datetime.utcnow() - timedelta(days=1)
        effective_end = end_date or datetime.utcnow() + timedelta(days=30)

        policy = Policy(
            worker_id=worker.id,
            policy_number=effective_number,
            tier=tier,
            status=status,
            weekly_premium=weekly_premium,
            base_premium=base_premium,
            max_daily_payout=max_daily_payout,
            max_weekly_payout=max_weekly_payout,
            zone_loading=zone_loading,
            seasonal_loading=seasonal_loading,
            claim_loading=claim_loading,
            streak_discount=streak_discount,
            start_date=effective_start,
            end_date=effective_end,
        )
        test_db.add(policy)
        test_db.commit()
        test_db.refresh(policy)
        return policy

    return _factory


@pytest.fixture(scope="function")
def make_trigger(test_db):
    """
    Factory fixture that creates and returns a ``TriggerEvent`` ORM object.

    Defaults to a validated Heavy Rainfall event in Bangalore so tests
    that need a trigger can get one with a single call.

    Usage::

        def test_trigger(make_trigger):
            te = make_trigger()
            assert te.is_validated is True

        def test_aqi_trigger(make_trigger):
            te = make_trigger(
                trigger_type=TriggerType.HAZARDOUS_AQI,
                city="Delhi",
                measured_value=490.0,
                threshold_value=400.0,
                unit="AQI",
            )
    """
    def _factory(
        trigger_type: str = TriggerType.HEAVY_RAINFALL,
        city: str = "Bangalore",
        pincode: str = "560001",
        measured_value: float = 75.0,
        threshold_value: float = 64.5,
        unit: str = "mm/24hr",
        source_primary: str = "OpenWeatherMap",
        source_secondary: str = "IMD API",
        is_validated: bool = True,
        severity: str = "high",
        description: str | None = None,
        confidence_score: float = 100.0,
        source_log: str | None = None,
        detected_at: datetime | None = None,
        expires_at: datetime | None = None,
    ) -> TriggerEvent:
        effective_description = description or (
            f"[Test] {trigger_type} in {city}: {measured_value} {unit}"
        )
        effective_detected = detected_at or datetime.utcnow() - timedelta(hours=1)
        effective_expires = expires_at or datetime.utcnow() + timedelta(hours=6)

        trigger = TriggerEvent(
            trigger_type=trigger_type,
            city=city,
            pincode=pincode,
            measured_value=measured_value,
            threshold_value=threshold_value,
            unit=unit,
            source_primary=source_primary,
            source_secondary=source_secondary,
            is_validated=is_validated,
            severity=severity,
            description=effective_description,
            confidence_score=confidence_score,
            source_log=source_log,
            detected_at=effective_detected,
            expires_at=effective_expires,
        )
        test_db.add(trigger)
        test_db.commit()
        test_db.refresh(trigger)
        return trigger

    return _factory


@pytest.fixture(scope="function")
def make_claim(test_db):
    """
    Factory fixture that creates and returns a ``Claim`` ORM object.

    Requires Worker, Policy, and TriggerEvent ORM instances.  A unique
    claim number is auto-generated unless explicitly overridden.

    Usage::

        def test_claim(make_worker, make_policy, make_trigger, make_claim):
            w  = make_worker()
            p  = make_policy(worker=w)
            te = make_trigger(city=w.city)
            c  = make_claim(worker=w, policy=p, trigger=te)
            assert c.worker_id == w.id
            assert c.policy_id == p.id
    """
    _counter = {"n": 0}

    def _factory(
        worker: Worker,
        policy: Policy,
        trigger: TriggerEvent,
        status: str = ClaimStatus.AUTO_APPROVED,
        payout_amount: float = 350.0,
        authenticity_score: float = 92.0,
        gps_valid: bool = True,
        activity_valid: bool = True,
        device_valid: bool = True,
        cross_source_valid: bool = True,
        fraud_flags: str | None = None,
        upi_id: str | None = "test@okaxis",
        payment_ref=_DEFAULT_CLAIM_FIELD,
        paid_at=_DEFAULT_CLAIM_FIELD,
        auto_processed: bool = True,
        trigger_confidence_score: float | None = None,
        appeal_status: str = "none",
        appeal_reason: str | None = None,
        appealed_at: datetime | None = None,
        recent_activity_valid: bool = True,
        recent_activity_at=_DEFAULT_CLAIM_FIELD,
        recent_activity_age_hours: float | None = 1.0,
        recent_activity_reason: str | None = "Recent session activity confirmed within payout window.",
        claim_number: str | None = None,
    ) -> Claim:
        _counter["n"] += 1
        n = _counter["n"]
        effective_number = claim_number if claim_number is not None else f"CLM-TEST-{n:05d}"
        if paid_at is _DEFAULT_CLAIM_FIELD:
            effective_paid_at = datetime.utcnow() - timedelta(minutes=5) if status == ClaimStatus.AUTO_APPROVED else None
        else:
            effective_paid_at = paid_at

        if payment_ref is _DEFAULT_CLAIM_FIELD:
            effective_payment_ref = f"MOCK-UPI-{effective_number}" if status == ClaimStatus.AUTO_APPROVED else None
        else:
            effective_payment_ref = payment_ref

        if recent_activity_at is _DEFAULT_CLAIM_FIELD:
            effective_recent_activity_at = datetime.utcnow()
        else:
            effective_recent_activity_at = recent_activity_at

        claim = Claim(
            claim_number=effective_number,
            worker_id=worker.id,
            policy_id=policy.id,
            trigger_event_id=trigger.id,
            status=status,
            payout_amount=payout_amount,
            authenticity_score=authenticity_score,
            gps_valid=gps_valid,
            activity_valid=activity_valid,
            device_valid=device_valid,
            cross_source_valid=cross_source_valid,
            fraud_flags=fraud_flags,
            upi_id=upi_id,
            payment_ref=effective_payment_ref,
            paid_at=effective_paid_at,
            auto_processed=auto_processed,
            trigger_confidence_score=trigger_confidence_score,
            appeal_status=appeal_status,
            appeal_reason=appeal_reason,
            appealed_at=appealed_at,
            recent_activity_valid=recent_activity_valid,
            recent_activity_at=effective_recent_activity_at,
            recent_activity_age_hours=recent_activity_age_hours,
            recent_activity_reason=recent_activity_reason,
        )
        test_db.add(claim)
        test_db.commit()
        test_db.refresh(claim)
        return claim

    return _factory


@pytest.fixture(scope="function")
def make_payout_txn(test_db):
    """
    Factory fixture that creates and returns a ``PayoutTransaction`` ORM object.

    Defaults to a settled claim payout transaction linked to the provided
    claim/worker so tests can focus on explainability and payment-state
    behavior without repeating transaction boilerplate.
    """
    _counter = {"n": 0}

    def _factory(
        *,
        claim: Claim | None = None,
        worker: Worker | None = None,
        policy: Policy | None = None,
        transaction_type: str = TransactionType.CLAIM_PAYOUT,
        status: str = PayoutTransactionStatus.SETTLED,
        gateway_name: str = "razorpay",
        amount_requested: float | None = None,
        amount_settled: float | None = None,
        currency: str = "INR",
        upi_id: str | None = "test@okaxis",
        upi_ref: str | None = None,
        razorpay_order_id: str | None = None,
        razorpay_payment_id: str | None = None,
        gateway_payload: str | None = None,
        failure_reason: str | None = None,
        initiated_at: datetime | None = None,
        settled_at: datetime | None = None,
        retry_count: int = 0,
        max_retries: int = 3,
        internal_txn_id: str | None = None,
    ) -> PayoutTransaction:
        _counter["n"] += 1
        n = _counter["n"]

        effective_worker = worker or (claim.worker if claim is not None else None)
        effective_policy = policy or (claim.policy if claim is not None else None)
        if effective_worker is None:
            raise ValueError("make_payout_txn requires worker=... or claim=...")

        effective_amount_requested = (
            amount_requested
            if amount_requested is not None
            else (claim.payout_amount if claim is not None else 350.0)
        )
        effective_amount_settled = (
            amount_settled
            if amount_settled is not None
            else (effective_amount_requested if status == PayoutTransactionStatus.SETTLED else None)
        )
        effective_initiated_at = initiated_at or datetime.utcnow() - timedelta(minutes=3)
        effective_settled_at = (
            settled_at
            if settled_at is not None
            else (datetime.utcnow() - timedelta(minutes=1) if status == PayoutTransactionStatus.SETTLED else None)
        )
        effective_upi_ref = (
            upi_ref
            if upi_ref is not None
            else (f"UTRTEST{n:06d}" if status == PayoutTransactionStatus.SETTLED else None)
        )
        effective_internal_id = internal_txn_id or f"TXN-TEST-{n:06d}"
        effective_payload = gateway_payload or json.dumps(
            {
                "status": status,
                "type": transaction_type,
                "worker_id": effective_worker.id,
            }
        )

        txn = PayoutTransaction(
            transaction_type=transaction_type,
            claim_id=claim.id if claim is not None else None,
            policy_id=effective_policy.id if effective_policy is not None else None,
            worker_id=effective_worker.id,
            upi_id=upi_id,
            upi_ref=effective_upi_ref,
            internal_txn_id=effective_internal_id,
            razorpay_order_id=razorpay_order_id,
            razorpay_payment_id=razorpay_payment_id,
            amount_requested=effective_amount_requested,
            amount_settled=effective_amount_settled,
            currency=currency,
            status=status,
            failure_reason=failure_reason,
            retry_count=retry_count,
            max_retries=max_retries,
            gateway_name=gateway_name,
            gateway_payload=effective_payload,
            initiated_at=effective_initiated_at,
            settled_at=effective_settled_at,
        )
        test_db.add(txn)
        test_db.commit()
        test_db.refresh(txn)
        return txn

    return _factory
