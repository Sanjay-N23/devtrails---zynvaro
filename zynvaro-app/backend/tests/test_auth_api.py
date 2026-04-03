"""
Zynvaro Backend — Integration tests for /auth endpoints
=========================================================
Tests cover POST /auth/register, POST /auth/login, and GET /auth/me
against the real FastAPI app wired to an in-memory SQLite test DB.

All fixtures (client, authed_client, make_worker, test_db) are provided
by tests/conftest.py and injected by pytest automatically.
"""

import sys
import os

# ── Make backend package importable from any working directory ──────────────
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import pytest
from passlib.context import CryptContext
from jose import jwt

# Same constants used by routers/auth.py and conftest.py
SECRET_KEY = "zynvaro-secret-2026-hackathon-key"
ALGORITHM = "HS256"

# Re-use the same hashing scheme as the application
_pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")

# ─────────────────────────────────────────────────────────────────────────────
# Shared test payload helpers
# ─────────────────────────────────────────────────────────────────────────────

REGISTER_PAYLOAD = {
    "full_name": "Test User",
    "phone": "9999999999",
    "password": "pass1234",
    "city": "Mumbai",
    "pincode": "400051",
    "platform": "Blinkit",
}

LOGIN_FORM = {
    "username": "9999999999",
    "password": "pass1234",
}


def _register(client, payload=None):
    """POST /auth/register with default or custom payload."""
    return client.post("/auth/register", json=payload or REGISTER_PAYLOAD)


def _login(client, form=None):
    """POST /auth/login with form-encoded credentials."""
    return client.post("/auth/login", data=form or LOGIN_FORM)


# =============================================================================
# POST /auth/register
# =============================================================================

class TestRegister:

    def test_register_creates_worker_with_201(self, client, test_db):
        """Successful registration must return HTTP 201 Created."""
        resp = _register(client)
        assert resp.status_code == 201

    def test_register_returns_token_and_worker_id(self, client, test_db):
        """Response body must contain access_token, worker_id, token_type, full_name."""
        resp = _register(client)
        assert resp.status_code == 201
        body = resp.json()
        assert "access_token" in body
        assert body["access_token"]  # non-empty
        assert "worker_id" in body
        assert isinstance(body["worker_id"], int)
        assert body["worker_id"] > 0
        assert "token_type" in body
        assert body["token_type"] == "bearer"
        assert body["full_name"] == REGISTER_PAYLOAD["full_name"]

    def test_register_hashes_password_not_stored_plaintext(self, client, test_db):
        """The password must never be stored in plaintext in the DB."""
        from models import Worker

        resp = _register(client)
        assert resp.status_code == 201

        worker_id = resp.json()["worker_id"]
        worker = test_db.query(Worker).filter(Worker.id == worker_id).first()

        assert worker is not None, "Worker row should exist after registration"
        # The stored hash must not equal the plaintext password
        assert worker.password_hash != REGISTER_PAYLOAD["password"]
        # The hash must be verifiable by passlib
        assert _pwd_context.verify(REGISTER_PAYLOAD["password"], worker.password_hash)

    def test_register_computes_zone_risk_score(self, client, test_db):
        """zone_risk_score must be non-zero and within the valid range [0, 1]."""
        from models import Worker
        from ml.premium_engine import get_zone_risk

        resp = _register(client)
        assert resp.status_code == 201

        worker_id = resp.json()["worker_id"]
        worker = test_db.query(Worker).filter(Worker.id == worker_id).first()

        assert worker is not None
        assert 0.0 <= worker.zone_risk_score <= 1.0

        # Must match what get_zone_risk(pincode, city) returns for this input
        expected = get_zone_risk(
            REGISTER_PAYLOAD["pincode"], REGISTER_PAYLOAD["city"]
        )
        assert worker.zone_risk_score == pytest.approx(expected, abs=1e-6)

    def test_register_duplicate_phone_returns_400(self, client, test_db):
        """Re-registering the same phone number must return HTTP 400."""
        first = _register(client)
        assert first.status_code == 201

        second = _register(client)
        assert second.status_code == 400

    def test_register_duplicate_phone_error_message_contains_phone(self, client, test_db):
        """The 400 error detail should mention the phone number."""
        _register(client)
        resp = _register(client)
        assert resp.status_code == 400
        detail = resp.json().get("detail", "").lower()
        # The router raises: "Phone number already registered"
        assert "phone" in detail or "already" in detail

    def test_register_minimal_fields_no_email_no_vehicle(self, client, test_db):
        """
        Registration should succeed with only the required fields;
        email and vehicle_type are optional and may be omitted.
        """
        payload = {
            "full_name": "Minimal User",
            "phone": "8888888888",
            "password": "minimal99",
            "city": "Bangalore",
            "pincode": "560001",
            "platform": "Zepto",
            # email omitted — Optional[str]
            # vehicle_type omitted — defaults to "2-Wheeler"
        }
        resp = client.post("/auth/register", json=payload)
        assert resp.status_code == 201
        body = resp.json()
        assert body["worker_id"] > 0
        assert body["token_type"] == "bearer"

    def test_register_token_is_valid_jwt(self, client, test_db):
        """The returned access_token must be a decodable JWT signed with SECRET_KEY."""
        resp = _register(client)
        assert resp.status_code == 201
        token = resp.json()["access_token"]

        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])
        worker_id = int(payload["sub"])
        assert worker_id == resp.json()["worker_id"]


# =============================================================================
# POST /auth/login
# =============================================================================

class TestLogin:

    def test_login_valid_credentials_returns_token(self, client, test_db):
        """Valid phone + password must return an access_token with HTTP 200."""
        _register(client)
        resp = _login(client)
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert body["access_token"]

    def test_login_valid_credentials_returns_worker_id(self, client, test_db):
        """Login response must include the same worker_id as returned at registration."""
        reg_resp = _register(client)
        assert reg_resp.status_code == 201
        register_worker_id = reg_resp.json()["worker_id"]

        login_resp = _login(client)
        assert login_resp.status_code == 200
        assert login_resp.json()["worker_id"] == register_worker_id

    def test_login_wrong_phone_returns_401(self, client, test_db):
        """A non-existent phone number must result in HTTP 401."""
        _register(client)
        resp = client.post(
            "/auth/login",
            data={"username": "0000000000", "password": "pass1234"},
        )
        assert resp.status_code == 401

    def test_login_wrong_password_returns_401(self, client, test_db):
        """Correct phone with wrong password must result in HTTP 401."""
        _register(client)
        resp = client.post(
            "/auth/login",
            data={"username": REGISTER_PAYLOAD["phone"], "password": "wrongpass"},
        )
        assert resp.status_code == 401

    def test_login_returns_same_worker_id_as_register(self, client, test_db):
        """
        Cross-endpoint consistency: the worker_id from login must equal
        the worker_id issued at registration.
        """
        reg_resp = _register(client)
        login_resp = _login(client)
        assert reg_resp.status_code == 201
        assert login_resp.status_code == 200
        assert reg_resp.json()["worker_id"] == login_resp.json()["worker_id"]

    def test_login_token_valid_for_me_endpoint(self, client, test_db):
        """
        The token returned by /auth/login must be accepted by GET /auth/me.
        This proves the token is consistent with what get_current_worker expects.
        """
        _register(client)
        login_resp = _login(client)
        token = login_resp.json()["access_token"]

        me_resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200

    def test_login_returns_token_type_bearer(self, client, test_db):
        """token_type must always be 'bearer' for OAuth2 compatibility."""
        _register(client)
        resp = _login(client)
        assert resp.status_code == 200
        assert resp.json()["token_type"] == "bearer"


# =============================================================================
# GET /auth/me
# =============================================================================

class TestMe:

    def test_me_with_valid_token_returns_profile(self, authed_client):
        """A valid Bearer token must give HTTP 200 and a worker profile body."""
        resp = authed_client.get("/auth/me")
        assert resp.status_code == 200

    def test_me_returns_correct_worker_fields(self, authed_client):
        """
        The /me response must contain all WorkerProfile schema fields and the
        values must match the worker that owns the token.
        """
        resp = authed_client.get("/auth/me")
        assert resp.status_code == 200
        body = resp.json()

        # All declared fields must be present
        required_fields = {
            "id", "full_name", "phone", "email", "city", "pincode",
            "platform", "vehicle_type", "shift", "zone_risk_score",
            "claim_history_count", "disruption_streak", "created_at",
        }
        assert required_fields.issubset(body.keys()), (
            f"Missing fields: {required_fields - body.keys()}"
        )

        # Spot-check values against the worker created by authed_client fixture
        worker = authed_client.worker  # type: ignore[attr-defined]
        assert body["id"] == worker.id
        assert body["full_name"] == worker.full_name
        assert body["phone"] == worker.phone
        assert body["city"] == worker.city
        assert body["platform"] == worker.platform
        assert body["zone_risk_score"] == pytest.approx(worker.zone_risk_score, abs=1e-6)

    def test_me_without_token_returns_401(self, client, test_db):
        """Requests without an Authorization header must return HTTP 401."""
        resp = client.get("/auth/me")
        assert resp.status_code == 401

    def test_me_with_invalid_token_returns_401(self, client, test_db):
        """A structurally valid JWT signed with the wrong key must return 401."""
        wrong_key_token = jwt.encode(
            {"sub": "1"},
            "completely-wrong-key",
            algorithm=ALGORITHM,
        )
        resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {wrong_key_token}"},
        )
        assert resp.status_code == 401

    def test_me_with_malformed_bearer_returns_401(self, client, test_db):
        """An obviously malformed token string must return HTTP 401."""
        resp = client.get(
            "/auth/me",
            headers={"Authorization": "Bearer this.is.not.a.real.jwt"},
        )
        assert resp.status_code == 401

    def test_me_with_expired_token_returns_401(self, client, test_db, make_worker):
        """A token whose 'exp' claim lies in the past must be rejected with 401."""
        from datetime import datetime, timedelta

        worker = make_worker(phone="7777777777")
        # Craft a token that expired 1 hour ago
        expired_payload = {
            "sub": str(worker.id),
            "exp": datetime.utcnow() - timedelta(hours=1),
        }
        expired_token = jwt.encode(expired_payload, SECRET_KEY, algorithm=ALGORITHM)

        resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {expired_token}"},
        )
        assert resp.status_code == 401

    def test_me_with_missing_sub_claim_returns_401(self, client, test_db):
        """A JWT that lacks the 'sub' claim must be rejected with 401."""
        from datetime import datetime, timedelta

        bad_payload = {"exp": datetime.utcnow() + timedelta(hours=1)}
        bad_token = jwt.encode(bad_payload, SECRET_KEY, algorithm=ALGORITHM)

        resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {bad_token}"},
        )
        assert resp.status_code == 401

    def test_me_token_from_register_is_accepted(self, client, test_db):
        """The JWT issued directly by /register must work as a /me bearer token."""
        reg_resp = _register(client)
        assert reg_resp.status_code == 201
        token = reg_resp.json()["access_token"]

        me_resp = client.get(
            "/auth/me",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert me_resp.status_code == 200
        assert me_resp.json()["phone"] == REGISTER_PAYLOAD["phone"]

    def test_me_zone_risk_score_in_valid_range(self, authed_client):
        """zone_risk_score returned by /me must be a float in [0.0, 1.0]."""
        resp = authed_client.get("/auth/me")
        assert resp.status_code == 200
        score = resp.json()["zone_risk_score"]
        assert isinstance(score, float)
        assert 0.0 <= score <= 1.0
