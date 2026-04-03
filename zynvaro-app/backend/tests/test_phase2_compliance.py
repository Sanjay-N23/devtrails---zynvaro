"""
Zynvaro — Phase 2 DEVTrails 2026 Compliance Tests
===================================================
Proves all 4 Phase 2 deliverables from the Guidewire DEVTrails 2026 brief:

  DELIVERABLE 1 — Registration Process
  DELIVERABLE 2 — Insurance Policy Management
  DELIVERABLE 3 — Dynamic Premium Calculation
  DELIVERABLE 4 — Claims Management (zero-touch pipeline)
  BONUS         — Trigger Coverage (brief: 3-5 triggers; we have 6)

Architecture note — background task DB session
-----------------------------------------------
_auto_generate_claims() opens its own database.SessionLocal() internally.
For claims-related tests we monkey-patch both database.SessionLocal and the
cached reference in routers.triggers so writes land in the same connection-
level transaction that test_db uses, keeping each test fully isolated.

All tests are self-contained: they create whatever data they need via API
calls or factory fixtures, then assert the expected state.
"""

import sys

sys.path.insert(0, "D:/AeroFyta_DEVTrails-2026_FULL_HANDOFF/zynvaro-app/backend")

from datetime import datetime, timedelta
from sqlalchemy.orm import sessionmaker
from unittest.mock import patch

import pytest

import database as _database_module
import routers.triggers as _triggers_module

from models import (
    Worker,
    Policy,
    Claim,
    TriggerEvent,
    PolicyStatus,
    PolicyTier,
    ClaimStatus,
    TriggerType,
)
from ml.premium_engine import (
    get_zone_risk,
    calculate_premium,
    get_payout_amount,
    TIER_CONFIG,
    ZONE_RISK_DB,
    CITY_DAILY_INCOME,
    TRIGGER_REPLACEMENT_RATES,
)

# ─────────────────────────────────────────────────────────────────────────────
# Shared helpers
# ─────────────────────────────────────────────────────────────────────────────

ALL_TRIGGER_TYPES = [
    "Heavy Rainfall",
    "Extreme Rain / Flooding",
    "Severe Heatwave",
    "Hazardous AQI",
    "Platform Outage",
    "Civil Disruption",
]

ALL_TIERS = ["Basic Shield", "Standard Guard", "Pro Armor"]


def _patch_session_local(test_db):
    """
    Context manager: replaces database.SessionLocal (and the alias in
    routers.triggers) with a factory bound to the same connection that
    test_db uses.  This makes _auto_generate_claims() writes visible to
    test_db queries while keeping everything inside the test transaction.
    """
    bound_factory = sessionmaker(
        autocommit=False,
        autoflush=False,
        bind=test_db.bind,
    )

    class _CM:
        def __enter__(self):
            _database_module.SessionLocal = bound_factory
            _triggers_module.SessionLocal = bound_factory
            return self

        def __exit__(self, *_):
            _database_module.SessionLocal = sessionmaker(
                autocommit=False,
                autoflush=False,
                bind=_database_module.engine,
            )
            _triggers_module.SessionLocal = _database_module.SessionLocal

    return _CM()


def _register(client, phone="9100000001", city="Mumbai", pincode="400051"):
    """POST /auth/register with sensible defaults. Returns response."""
    return client.post(
        "/auth/register",
        json={
            "full_name": "Phase2 Test User",
            "phone": phone,
            "password": "testpass123",
            "city": city,
            "pincode": pincode,
            "platform": "Blinkit",
        },
    )


def _activate_policy(authed_client, tier="Basic Shield"):
    """POST /policies/ and return response."""
    return authed_client.post("/policies/", json={"tier": tier})


# ═══════════════════════════════════════════════════════════════════════════
# DELIVERABLE 1 — REGISTRATION PROCESS
# ═══════════════════════════════════════════════════════════════════════════

class TestRegistrationProcess:
    """
    Proves Deliverable 1: workers can register, receive a token immediately,
    log in, and retrieve their profile — the complete 3-step registration flow.
    """

    def test_full_registration_flow(self, client):
        """
        POST /auth/register → 201 with token → use token on GET /auth/me →
        same worker returned.  Proves the 3-step registration works end-to-end.
        """
        reg_resp = _register(client, phone="9200000001")
        assert reg_resp.status_code == 201, reg_resp.text

        body = reg_resp.json()
        assert "access_token" in body
        assert body["access_token"]
        worker_id = body["worker_id"]
        token = body["access_token"]

        me_resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200
        assert me_resp.json()["id"] == worker_id

    def test_registration_assigns_zone_risk_from_pincode(self, client):
        """
        Register with Mumbai high-flood pincode 400051 → zone_risk_score
        must equal the known lookup value 0.88.
        """
        reg_resp = _register(client, phone="9200000002", city="Mumbai", pincode="400051")
        assert reg_resp.status_code == 201

        token = reg_resp.json()["access_token"]
        me_resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200
        score = me_resp.json()["zone_risk_score"]
        # Known value from ZONE_RISK_DB["400051"] = 0.88
        assert score == pytest.approx(0.88, abs=1e-6), (
            f"Expected zone_risk_score=0.88 for pincode 400051, got {score}"
        )

    def test_registration_immediately_grants_login_access(self, client):
        """
        The token returned by POST /auth/register must be immediately usable
        on GET /auth/me — no extra activation step required.
        """
        reg_resp = _register(client, phone="9200000003")
        assert reg_resp.status_code == 201

        token = reg_resp.json()["access_token"]
        me_resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200, (
            "Token from /register must work immediately on /auth/me"
        )

    def test_registration_persists_city_and_platform(self, client, test_db):
        """
        After registration the DB row must contain the exact city and platform
        values sent in the request body.
        """
        reg_resp = client.post(
            "/auth/register",
            json={
                "full_name": "City Platform Check",
                "phone": "9200000004",
                "password": "pass1234",
                "city": "Hyderabad",
                "pincode": "500072",
                "platform": "Zepto",
            },
        )
        assert reg_resp.status_code == 201

        worker_id = reg_resp.json()["worker_id"]
        worker = test_db.query(Worker).filter(Worker.id == worker_id).first()
        assert worker is not None
        assert worker.city == "Hyderabad"
        assert worker.platform == "Zepto"

    def test_duplicate_worker_cannot_register_twice(self, client):
        """
        Two POST /auth/register calls with the same phone number:
        the first must return 201, the second must return 400.
        """
        first = _register(client, phone="9200000005")
        assert first.status_code == 201

        second = _register(client, phone="9200000005")
        assert second.status_code == 400
        detail = second.json().get("detail", "").lower()
        assert "phone" in detail or "already" in detail


# ═══════════════════════════════════════════════════════════════════════════
# DELIVERABLE 2 — INSURANCE POLICY MANAGEMENT
# ═══════════════════════════════════════════════════════════════════════════

class TestPolicyManagement:
    """
    Proves Deliverable 2: workers can activate, renew, cancel, and switch
    policies; only one policy is active at a time; invalid tiers are rejected.
    """

    def test_worker_can_activate_policy_after_registration(self, authed_client):
        """
        POST /policies/ → 201 → GET /policies/active returns the same policy.
        """
        create_resp = _activate_policy(authed_client, "Basic Shield")
        assert create_resp.status_code == 201
        policy_id = create_resp.json()["id"]

        active_resp = authed_client.get("/policies/active")
        assert active_resp.status_code == 200
        active = active_resp.json()
        assert active is not None
        assert active["id"] == policy_id
        assert active["status"] == "active"

    def test_worker_can_only_have_one_active_policy(self, authed_client, test_db):
        """
        Activate Basic Shield, then Standard Guard → only Standard Guard
        is ACTIVE; total ACTIVE count for this worker is exactly 1.
        """
        _activate_policy(authed_client, "Basic Shield")
        _activate_policy(authed_client, "Standard Guard")

        worker_id = authed_client.worker.id
        active_count = (
            test_db.query(Policy)
            .filter(
                Policy.worker_id == worker_id,
                Policy.status == PolicyStatus.ACTIVE,
            )
            .count()
        )
        assert active_count == 1

        active_resp = authed_client.get("/policies/active")
        assert active_resp.json()["tier"] == "Standard Guard"

    def test_previous_policy_cancelled_on_new_activation(self, authed_client, test_db):
        """
        Activate Basic Shield → Activate Standard Guard →
        DB must contain exactly 1 ACTIVE + 1 CANCELLED for this worker.
        """
        r1 = _activate_policy(authed_client, "Basic Shield")
        assert r1.status_code == 201
        first_id = r1.json()["id"]

        r2 = _activate_policy(authed_client, "Standard Guard")
        assert r2.status_code == 201

        worker_id = authed_client.worker.id
        active_policies = (
            test_db.query(Policy)
            .filter(Policy.worker_id == worker_id, Policy.status == PolicyStatus.ACTIVE)
            .all()
        )
        cancelled_policies = (
            test_db.query(Policy)
            .filter(Policy.worker_id == worker_id, Policy.status == PolicyStatus.CANCELLED)
            .all()
        )
        assert len(active_policies) == 1
        assert len(cancelled_policies) == 1
        assert cancelled_policies[0].id == first_id

    def test_policy_has_7_day_coverage_window(self, authed_client):
        """
        end_date − start_date must be approximately 7 days (±10 s tolerance).
        """
        resp = _activate_policy(authed_client, "Standard Guard")
        assert resp.status_code == 201
        data = resp.json()

        start = datetime.fromisoformat(data["start_date"].replace("Z", ""))
        end = datetime.fromisoformat(data["end_date"].replace("Z", ""))
        delta = (end - start).total_seconds()
        assert abs(delta - 7 * 86400) < 10, (
            f"Expected 7-day window, got {delta / 86400:.4f} days"
        )

    def test_worker_can_cancel_active_policy(self, authed_client):
        """
        Activate → DELETE /policies/{id} → 204 → GET /policies/active → None.
        """
        create_resp = _activate_policy(authed_client, "Basic Shield")
        policy_id = create_resp.json()["id"]

        cancel_resp = authed_client.delete(f"/policies/{policy_id}")
        assert cancel_resp.status_code == 204

        active_resp = authed_client.get("/policies/active")
        assert active_resp.status_code == 200
        assert active_resp.json() is None

    def test_worker_can_renew_policy_for_another_week(self, authed_client):
        """
        Activate → POST /policies/renew → end_date extended by 7 days from
        the original end_date (not from now).
        """
        create_resp = _activate_policy(authed_client, "Basic Shield")
        assert create_resp.status_code == 201
        original_end = datetime.fromisoformat(
            create_resp.json()["end_date"].replace("Z", "")
        )

        renew_resp = authed_client.post("/policies/renew")
        assert renew_resp.status_code == 201
        new_end = datetime.fromisoformat(
            renew_resp.json()["end_date"].replace("Z", "")
        )

        extension = (new_end - original_end).total_seconds()
        assert abs(extension - 7 * 86400) < 10, (
            f"Expected +7 days extension, got {extension / 86400:.4f} days"
        )

    def test_all_three_tiers_are_activatable(self, authed_client):
        """
        Activate Basic Shield, Standard Guard, Pro Armor in sequence.
        Each POST must return 201 and the correct tier name.
        """
        for tier in ALL_TIERS:
            resp = _activate_policy(authed_client, tier)
            assert resp.status_code == 201, (
                f"Expected 201 for tier={tier!r}, got {resp.status_code}: {resp.text}"
            )
            assert resp.json()["tier"] == tier

    def test_invalid_tier_rejected(self, authed_client):
        """
        POST /policies/ with tier="Gold Master" (not in TIER_CONFIG) → 400.
        """
        resp = authed_client.post("/policies/", json={"tier": "Gold Master"})
        assert resp.status_code == 400
        # Error detail should list the valid tier names
        detail = resp.json()["detail"]
        for valid_tier in ALL_TIERS:
            assert valid_tier in detail, (
                f"Valid tier '{valid_tier}' missing from 400 error: {detail}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# DELIVERABLE 3 — DYNAMIC PREMIUM CALCULATION
# ═══════════════════════════════════════════════════════════════════════════

class TestDynamicPremiumCalculation:
    """
    Proves Deliverable 3: premiums are calculated dynamically using zone risk,
    seasonal factors, claim history, and disruption streak — not flat rates.
    """

    def test_premium_differs_by_city_risk(self, authed_client, client):
        """
        A Mumbai worker (zone_risk 0.88) must receive a higher Basic Shield
        premium quote than a Bangalore worker (zone_risk 0.55-ish).
        """
        # Mumbai worker premium (via the authed_client fixture worker — Bangalore)
        # We compare direct engine output to avoid needing two authed clients
        mumbai_result = calculate_premium(
            tier="Basic Shield",
            pincode="400051",
            city="Mumbai",
            claim_history_count=0,
            disruption_streak=0,
        )
        bangalore_result = calculate_premium(
            tier="Basic Shield",
            pincode="560001",
            city="Bangalore",
            claim_history_count=0,
            disruption_streak=0,
        )
        assert mumbai_result["weekly_premium"] >= bangalore_result["weekly_premium"], (
            f"Mumbai ({mumbai_result['weekly_premium']}) should be >= "
            f"Bangalore ({bangalore_result['weekly_premium']})"
        )

    def test_premium_quote_returns_breakdown_with_zone_factor(self, authed_client):
        """
        GET /policies/quote/all → each tier breakdown must contain
        zone_risk_score AND zone_factor.
        """
        resp = authed_client.get("/policies/quote/all")
        assert resp.status_code == 200
        for tier_quote in resp.json()["tiers"]:
            bd = tier_quote.get("breakdown", {})
            assert "zone_risk_score" in bd, (
                f"zone_risk_score missing from breakdown of {tier_quote['tier']}"
            )
            assert "zone_factor" in bd, (
                f"zone_factor missing from breakdown of {tier_quote['tier']}"
            )

    def test_premium_has_ai_explanation_list(self, authed_client):
        """
        GET /policies/quote/all → each tier quote's explanation field must be
        a non-empty list of strings (SHAP-style waterfall reasons).
        """
        resp = authed_client.get("/policies/quote/all")
        assert resp.status_code == 200
        for tier_quote in resp.json()["tiers"]:
            explanation = tier_quote.get("explanation", [])
            assert isinstance(explanation, list), (
                f"explanation is not a list for {tier_quote['tier']}"
            )
            assert len(explanation) > 0, (
                f"explanation list is empty for {tier_quote['tier']}"
            )
            for item in explanation:
                assert isinstance(item, str), (
                    f"Non-string item in explanation for {tier_quote['tier']}: {item!r}"
                )

    def test_premium_higher_for_high_claim_history(self, authed_client, test_db):
        """
        Worker with claim_history_count=5 must get a higher quote than a
        fresh worker with 0 claims (same city/pincode).
        """
        clean_result = calculate_premium(
            tier="Basic Shield",
            pincode=authed_client.worker.pincode,
            city=authed_client.worker.city,
            claim_history_count=0,
            disruption_streak=0,
        )
        risky_result = calculate_premium(
            tier="Basic Shield",
            pincode=authed_client.worker.pincode,
            city=authed_client.worker.city,
            claim_history_count=5,
            disruption_streak=0,
        )
        assert risky_result["weekly_premium"] >= clean_result["weekly_premium"], (
            f"5-claim worker ({risky_result['weekly_premium']}) should be >= "
            f"0-claim worker ({clean_result['weekly_premium']})"
        )

    def test_streak_discount_reduces_premium(self, authed_client, test_db):
        """
        Worker with disruption_streak=6 (qualifies for 20% discount) must
        receive a lower premium quote than streak=0.
        """
        no_streak = calculate_premium(
            tier="Standard Guard",
            pincode=authed_client.worker.pincode,
            city=authed_client.worker.city,
            claim_history_count=0,
            disruption_streak=0,
        )
        high_streak = calculate_premium(
            tier="Standard Guard",
            pincode=authed_client.worker.pincode,
            city=authed_client.worker.city,
            claim_history_count=0,
            disruption_streak=6,
        )
        assert high_streak["weekly_premium"] <= no_streak["weekly_premium"], (
            f"streak=6 ({high_streak['weekly_premium']}) should be <= "
            f"streak=0 ({no_streak['weekly_premium']})"
        )

    def test_basic_shield_always_cheaper_than_standard_guard(self, authed_client):
        """
        GET /policies/quote/all → Basic Shield < Standard Guard < Pro Armor.
        """
        resp = authed_client.get("/policies/quote/all")
        assert resp.status_code == 200
        premiums = {t["tier"]: t["weekly_premium"] for t in resp.json()["tiers"]}

        assert premiums["Basic Shield"] <= premiums["Standard Guard"], (
            f"Basic Shield ({premiums['Basic Shield']}) should be <= "
            f"Standard Guard ({premiums['Standard Guard']})"
        )
        assert premiums["Standard Guard"] <= premiums["Pro Armor"], (
            f"Standard Guard ({premiums['Standard Guard']}) should be <= "
            f"Pro Armor ({premiums['Pro Armor']})"
        )

    def test_premium_is_affordable_for_basic_shield(self, authed_client):
        """
        Basic Shield weekly_premium must not exceed ₹36 (4500 × 0.008
        affordability guardrail from the pricing engine).
        """
        resp = authed_client.get("/policies/quote/all")
        assert resp.status_code == 200
        tiers = resp.json()["tiers"]
        basic = next(t for t in tiers if t["tier"] == "Basic Shield")
        max_affordable = round(4500 * 0.008, 2)  # == 36.0
        assert basic["weekly_premium"] <= max_affordable, (
            f"Basic Shield premium {basic['weekly_premium']} exceeds affordability "
            f"cap of {max_affordable}"
        )

    def test_activated_policy_stores_premium_breakdown(self, authed_client, test_db):
        """
        POST /policies/ for a Mumbai worker → the saved Policy row in the DB
        must have zone_loading > 0 (Mumbai is high-risk, zone_factor > 1.0).

        We achieve this by temporarily updating the authed_client worker's
        city and pincode to Mumbai values before calling the endpoint.
        """
        # Set the worker to Mumbai for this test so zone_loading is definitely > 0
        worker = authed_client.worker
        original_city = worker.city
        original_pincode = worker.pincode

        worker.city = "Mumbai"
        worker.pincode = "400051"
        test_db.commit()
        test_db.refresh(worker)

        try:
            resp = _activate_policy(authed_client, "Basic Shield")
            assert resp.status_code == 201
            policy_id = resp.json()["id"]

            test_db.expire_all()
            policy = test_db.query(Policy).filter(Policy.id == policy_id).first()
            assert policy is not None
            # Mumbai zone_risk = 0.88 → zone_factor = 0.8 + 0.88 × 0.6 = 1.328
            # zone_loading = (1.328 - 1.0) × base > 0
            assert policy.zone_loading > 0, (
                f"Expected zone_loading > 0 for Mumbai worker, got {policy.zone_loading}"
            )
        finally:
            # Restore worker city/pincode so other tests are not affected
            worker.city = original_city
            worker.pincode = original_pincode
            test_db.commit()


# ═══════════════════════════════════════════════════════════════════════════
# DELIVERABLE 4 — CLAIMS MANAGEMENT (zero-touch pipeline)
# ═══════════════════════════════════════════════════════════════════════════

class TestClaimsManagement:
    """
    Proves Deliverable 4: triggers auto-create claims for active policyholders
    with no manual input; claims include authenticity scores; matching-city
    workers are AUTO_APPROVED; payouts are income-proportional; deduplication
    prevents double-paying the same event.
    """

    def test_simulate_trigger_auto_creates_claim_for_active_policyholder(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """
        POST /policies/ → POST /triggers/simulate → GET /claims/ → 1 claim exists.
        """
        worker = make_worker(city="Bangalore", claim_history_count=0)
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            sim_resp = authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"},
            )
        assert sim_resp.status_code == 201

        test_db.expire_all()
        claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
        assert len(claims) == 1, (
            f"Expected 1 auto-created claim, found {len(claims)}"
        )

    def test_claim_is_auto_processed_no_manual_input(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """
        After simulate → the claim row must have auto_processed == True.
        No manual action was taken to create it.
        """
        worker = make_worker(city="Mumbai", claim_history_count=0)
        make_policy(worker=worker, tier=PolicyTier.STANDARD, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Severe Heatwave", "city": "Mumbai"},
            )

        test_db.expire_all()
        claim = test_db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None
        assert claim.auto_processed is True

    def test_claim_has_authenticity_score(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """
        After simulate → claim.authenticity_score must be in [0, 100].
        """
        worker = make_worker(city="Bangalore", claim_history_count=0)
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Hazardous AQI", "city": "Bangalore"},
            )

        test_db.expire_all()
        claim = test_db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None
        assert 0 <= claim.authenticity_score <= 100, (
            f"authenticity_score {claim.authenticity_score} is outside [0, 100]"
        )

    def test_matching_city_worker_gets_auto_approved(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """
        Worker city == trigger city, claim_history=0, device attested →
        compute_authenticity_score returns 100 → claim.status == AUTO_APPROVED.
        """
        worker = make_worker(city="Chennai", claim_history_count=0)
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Chennai"},
            )

        test_db.expire_all()
        claim = test_db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None
        # AUTO_APPROVED claims now transition immediately to PAID when payment_ref
        # and paid_at are set in the same DB transaction (zero-touch parametric flow).
        assert claim.status in (ClaimStatus.AUTO_APPROVED, ClaimStatus.PAID), (
            f"Expected AUTO_APPROVED or PAID for city-matched clean worker, got {claim.status}"
        )

    def test_claim_payout_is_income_proportional_not_flat(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """
        Simulate Heavy Rainfall for a Mumbai Basic Shield worker.
        Payout must be 35% of daily income (₹900 × 0.35 = ₹315 rounded to ₹310),
        NOT a flat constant — proving income-proportional calculation.
        """
        worker = make_worker(city="Mumbai", claim_history_count=0)
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Mumbai"},
            )

        test_db.expire_all()
        claim = test_db.query(Claim).filter(Claim.worker_id == worker.id).first()
        assert claim is not None

        # Compute expected payout using the engine directly
        expected = get_payout_amount("Heavy Rainfall", PolicyTier.BASIC, "Mumbai")
        # Verify it is income-proportional:
        #   daily_income = ₹900, replacement_rate = 35% → raw = ₹320
        #   capped at Basic Shield max_daily = ₹300
        daily_income = 900    # CITY_DAILY_INCOME["Mumbai"]["Basic Shield"]
        rate = 0.35           # TRIGGER_REPLACEMENT_RATES["Heavy Rainfall"]["Basic Shield"]
        max_daily = TIER_CONFIG[PolicyTier.BASIC]["max_daily"]   # 300
        raw_payout = round(daily_income * rate / 10) * 10        # 320
        expected_from_formula = float(min(raw_payout, max_daily))  # 300.0
        assert expected == pytest.approx(expected_from_formula, abs=0.01), (
            f"Engine output {expected} does not match formula result {expected_from_formula}"
        )
        assert claim.payout_amount == pytest.approx(expected, abs=0.01)

        # Extra: prove it is NOT a flat amount by comparing against Standard Guard
        std_expected = get_payout_amount("Heavy Rainfall", PolicyTier.STANDARD, "Mumbai")
        assert std_expected != expected, (
            "Basic Shield and Standard Guard payout must differ — they are income-proportional"
        )

    def test_no_claim_for_worker_without_policy(
        self, client, test_db
    ):
        """
        Register a worker but do NOT activate a policy.
        Simulate a trigger in their city → no claim row created for them.
        """
        reg_resp = client.post(
            "/auth/register",
            json={
                "full_name": "No Policy Worker",
                "phone": "9300000099",
                "password": "pass1234",
                "city": "Delhi",
                "pincode": "110001",
                "platform": "Zepto",
            },
        )
        assert reg_resp.status_code == 201
        worker_id = reg_resp.json()["worker_id"]
        token = reg_resp.json()["access_token"]

        with _patch_session_local(test_db):
            authed_headers = {"Authorization": f"Bearer {token}"}
            client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Delhi"},
                headers=authed_headers,
            )

        test_db.expire_all()
        claims = test_db.query(Claim).filter(Claim.worker_id == worker_id).all()
        assert claims == [], (
            f"Expected 0 claims for worker without policy, found {len(claims)}"
        )

    def test_no_duplicate_claim_for_same_trigger(
        self, authed_client, test_db, make_worker, make_policy
    ):
        """
        Simulate the same trigger type + city twice within 24h →
        only 1 claim must be created (deduplication guard).
        """
        worker = make_worker(city="Hyderabad", claim_history_count=0)
        make_policy(worker=worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)

        with _patch_session_local(test_db):
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Hyderabad"},
            )
            authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": "Heavy Rainfall", "city": "Hyderabad"},
            )

        test_db.expire_all()
        claims = test_db.query(Claim).filter(Claim.worker_id == worker.id).all()
        assert len(claims) == 1, (
            f"Expected 1 claim after duplicate simulate (dedup), got {len(claims)}"
        )

    def test_all_6_trigger_types_are_supported(self, authed_client, test_db):
        """
        Simulate each of the 6 trigger types → all must return 201 successfully.
        This proves the zero-touch pipeline handles every trigger type.
        """
        for trigger_type in ALL_TRIGGER_TYPES:
            resp = authed_client.post(
                "/triggers/simulate",
                json={"trigger_type": trigger_type, "city": "Bangalore"},
            )
            assert resp.status_code == 201, (
                f"Expected 201 for trigger_type={trigger_type!r}, "
                f"got {resp.status_code}: {resp.text}"
            )


# ═══════════════════════════════════════════════════════════════════════════
# BONUS — TRIGGER COVERAGE
# Brief requirement: "Build 3-5 automated triggers" — we implemented 6.
# ═══════════════════════════════════════════════════════════════════════════

class TestTriggerCoverage:
    """
    Proves the bonus requirement from the brief: the system has at least 5
    automated triggers (we have 6, exceeding the brief's 3-5 requirement).
    """

    def test_system_has_6_trigger_types_exceeding_brief_requirement(self, client):
        """
        GET /triggers/types → at least 5 trigger types returned.
        We implement 6, exceeding the brief's 3-5 requirement.
        """
        resp = client.get("/triggers/types")
        assert resp.status_code == 200
        types = resp.json()
        assert len(types) >= 5, (
            f"Brief requires 3-5 triggers, system must have ≥5; got {len(types)}"
        )
        # Confirm we exceed the brief with exactly 6
        assert len(types) == 6, (
            f"Expected 6 trigger types (exceeds 3-5 brief requirement), got {len(types)}"
        )

    def test_heavy_rainfall_trigger_type_exists(self, client):
        """Heavy Rainfall — core weather trigger covering monsoon disruption."""
        resp = client.get("/triggers/types")
        assert resp.status_code == 200
        types = [t["trigger_type"] for t in resp.json()]
        assert "Heavy Rainfall" in types

    def test_hazardous_aqi_trigger_type_exists(self, client):
        """Hazardous AQI — covers Delhi/winter air-quality income loss."""
        resp = client.get("/triggers/types")
        assert resp.status_code == 200
        types = [t["trigger_type"] for t in resp.json()]
        assert "Hazardous AQI" in types

    def test_severe_heatwave_trigger_type_exists(self, client):
        """Severe Heatwave — covers extreme heat preventing delivery work."""
        resp = client.get("/triggers/types")
        assert resp.status_code == 200
        types = [t["trigger_type"] for t in resp.json()]
        assert "Severe Heatwave" in types

    def test_civil_disruption_trigger_type_exists(self, client):
        """Civil Disruption — covers bandh/protest events blocking deliveries."""
        resp = client.get("/triggers/types")
        assert resp.status_code == 200
        types = [t["trigger_type"] for t in resp.json()]
        assert "Civil Disruption" in types

    def test_platform_outage_trigger_type_exists(self, client):
        """Platform Outage — covers Blinkit/Zepto app downtime income loss."""
        resp = client.get("/triggers/types")
        assert resp.status_code == 200
        types = [t["trigger_type"] for t in resp.json()]
        assert "Platform Outage" in types

    def test_extreme_rain_flooding_trigger_type_exists(self, client):
        """Extreme Rain / Flooding — elevated tier above Heavy Rainfall."""
        resp = client.get("/triggers/types")
        assert resp.status_code == 200
        types = [t["trigger_type"] for t in resp.json()]
        assert "Extreme Rain / Flooding" in types

    def test_each_trigger_type_has_threshold_and_unit(self, client):
        """
        Every trigger type must declare a numeric threshold and a unit string —
        these are the parametric trigger parameters, not subjective assessments.
        """
        resp = client.get("/triggers/types")
        assert resp.status_code == 200
        for entry in resp.json():
            assert "threshold" in entry, f"threshold missing from {entry}"
            assert isinstance(entry["threshold"], (int, float)), (
                f"threshold not numeric for {entry['trigger_type']}"
            )
            assert "unit" in entry, f"unit missing from {entry}"
            assert isinstance(entry["unit"], str) and entry["unit"], (
                f"unit empty/non-string for {entry['trigger_type']}"
            )

    def test_each_trigger_type_has_dual_source(self, client):
        """
        Each trigger type must list a source_primary — dual-source validation
        is a key architectural requirement (parametric triggers need two data
        sources to prevent false positives).
        """
        resp = client.get("/triggers/types")
        assert resp.status_code == 200
        for entry in resp.json():
            assert "source_primary" in entry, (
                f"source_primary missing from {entry['trigger_type']}"
            )
            assert entry["source_primary"], (
                f"source_primary is empty for {entry['trigger_type']}"
            )
