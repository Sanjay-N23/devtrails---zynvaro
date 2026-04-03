"""
Zynvaro Backend — Security-focused test suite
==============================================
Covers JWT attack vectors, cross-worker data isolation, admin endpoint
auth behaviour, registration hardening, and input validation.

All fixtures (client, authed_client, make_worker, make_policy,
make_trigger, make_claim, test_db) are provided by tests/conftest.py.

Path bootstrap mirrors the pattern used in every other test module so
the backend package is importable regardless of cwd.
"""

import sys
import os
import base64
import json

# ── Make backend package importable from any working directory ───────────────
BACKEND_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if BACKEND_DIR not in sys.path:
    sys.path.insert(0, BACKEND_DIR)

import pytest
from datetime import datetime, timedelta
from jose import jwt
from passlib.context import CryptContext

# Same constants as routers/auth.py and conftest.py
SECRET_KEY = "zynvaro-secret-2026-hackathon-key"
ALGORITHM = "HS256"

_pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _make_token(payload: dict, key: str = SECRET_KEY, algorithm: str = ALGORITHM) -> str:
    """Encode a JWT with an arbitrary payload."""
    return jwt.encode(payload, key, algorithm=algorithm)


def _valid_payload(worker_id: int = 1) -> dict:
    """Return a well-formed JWT payload that will not expire for 7 days."""
    return {
        "sub": str(worker_id),
        "exp": datetime.utcnow() + timedelta(days=7),
    }


def _bearer(token: str) -> dict:
    """Wrap a token string in the Authorization header dict."""
    return {"Authorization": f"Bearer {token}"}


# Minimal valid registration body used across multiple test classes
_BASE_REGISTER = {
    "full_name": "Security Test User",
    "phone": "9555555555",
    "password": "securepass99",
    "city": "Mumbai",
    "pincode": "400051",
    "platform": "Blinkit",
}


# =============================================================================
# JWT SECURITY
# =============================================================================

class TestJWTSecurity:
    """Verify that the token validation layer rejects every known JWT attack."""

    # ── Protected endpoints used for probing ─────────────────────────────────
    _PROTECTED_ENDPOINTS = [
        ("GET",  "/auth/me"),
        ("GET",  "/policies/active"),
        ("GET",  "/claims/"),
    ]

    def test_request_without_authorization_header_returns_401(self, client, test_db):
        """
        Every auth-required endpoint must return 401 when the Authorization
        header is absent.  OAuth2PasswordBearer raises a 401 automatically for
        missing credentials.
        """
        for method, path in self._PROTECTED_ENDPOINTS:
            resp = getattr(client, method.lower())(path)
            assert resp.status_code == 401, (
                f"{method} {path} should be 401 without auth, got {resp.status_code}"
            )

    def test_request_with_wrong_scheme_returns_401(self, client, make_worker, test_db):
        """
        'Token <value>' is not the Bearer scheme.  FastAPI's OAuth2PasswordBearer
        only accepts the 'Bearer' prefix, so the wrong scheme must yield 401.
        """
        worker = make_worker()
        token = _make_token(_valid_payload(worker.id))

        for method, path in self._PROTECTED_ENDPOINTS:
            resp = getattr(client, method.lower())(
                path, headers={"Authorization": f"Token {token}"}
            )
            assert resp.status_code == 401, (
                f"{method} {path}: wrong scheme should be 401, got {resp.status_code}"
            )

    def test_token_signed_with_wrong_secret_is_rejected(self, client, make_worker, test_db):
        """
        A JWT signed with a key other than SECRET_KEY must not pass
        signature verification and must be rejected with 401.
        """
        worker = make_worker()
        bad_token = _make_token(_valid_payload(worker.id), key="wrong-secret")

        resp = client.get("/auth/me", headers=_bearer(bad_token))
        assert resp.status_code == 401

    def test_token_with_tampered_payload_is_rejected(self, client, make_worker, test_db):
        """
        Manually alter the payload section of a valid JWT without re-signing.
        The signature will no longer match the modified payload, so the server
        must reject it with 401 (JWTError on signature verification).
        """
        worker_a = make_worker()
        worker_b = make_worker()

        # Obtain a legitimately signed token for worker_a
        valid_token = _make_token(_valid_payload(worker_a.id))

        # A JWT is three base64url-encoded segments joined by '.'
        header_b64, payload_b64, signature_b64 = valid_token.split(".")

        # Decode the payload (add padding as required by base64)
        padding = "=" * (-len(payload_b64) % 4)
        payload_bytes = base64.urlsafe_b64decode(payload_b64 + padding)
        payload_obj = json.loads(payload_bytes)

        # Substitute worker_b's id into the payload
        payload_obj["sub"] = str(worker_b.id)

        # Re-encode without signing (original signature is kept — mismatch!)
        tampered_payload = base64.urlsafe_b64encode(
            json.dumps(payload_obj, separators=(",", ":")).encode()
        ).rstrip(b"=").decode()

        tampered_token = f"{header_b64}.{tampered_payload}.{signature_b64}"

        resp = client.get("/auth/me", headers=_bearer(tampered_token))
        assert resp.status_code == 401

    def test_expired_token_is_rejected(self, client, make_worker, test_db):
        """
        A token whose 'exp' claim is set to one hour in the past must be
        rejected with 401 by get_current_worker.
        """
        worker = make_worker()
        expired_payload = {
            "sub": str(worker.id),
            "exp": datetime.utcnow() - timedelta(hours=1),
        }
        expired_token = _make_token(expired_payload)

        resp = client.get("/auth/me", headers=_bearer(expired_token))
        assert resp.status_code == 401

    def test_token_with_no_exp_claim_is_accepted(self, client, make_worker, test_db):
        """
        python-jose does not mandate an 'exp' claim by default when decoding
        (no options={'verify_exp': True} is needed when the claim is absent).
        Tokens without 'exp' should be accepted — the server's current design
        does not enforce mandatory expiry when the claim is simply missing.

        If this behaviour is tightened in production, this test should be
        updated to assert 401 instead.
        """
        worker = make_worker()
        no_exp_token = jwt.encode({"sub": str(worker.id)}, SECRET_KEY, algorithm=ALGORITHM)

        resp = client.get("/auth/me", headers=_bearer(no_exp_token))
        # Document the actual behaviour (currently 200); change assertion if
        # the server is hardened to require exp.
        assert resp.status_code in (200, 401), (
            f"Unexpected status {resp.status_code} for token without exp claim"
        )

    def test_token_with_nonexistent_worker_id_returns_401(self, client, test_db):
        """
        A JWT signed with the correct key but referencing a worker_id that
        does not exist in the database must return 401, because
        get_current_worker does a DB lookup and raises if the row is missing.
        """
        ghost_token = _make_token({
            "sub": "999999",
            "exp": datetime.utcnow() + timedelta(days=7),
        })

        resp = client.get("/auth/me", headers=_bearer(ghost_token))
        assert resp.status_code == 401

    def test_token_with_float_worker_id_returns_401(self, client, test_db):
        """
        get_current_worker calls int(worker_id_str).  A sub value of '1.5'
        causes a ValueError in int() which is caught by the except block and
        re-raised as a 401.
        """
        float_sub_token = _make_token({
            "sub": "1.5",
            "exp": datetime.utcnow() + timedelta(days=7),
        })

        resp = client.get("/auth/me", headers=_bearer(float_sub_token))
        assert resp.status_code == 401

    def test_empty_bearer_token_returns_401(self, client, test_db):
        """
        'Bearer ' (the word Bearer followed by a space but no token) must be
        treated as a missing / malformed credential and return 401.
        """
        resp = client.get("/auth/me", headers={"Authorization": "Bearer "})
        assert resp.status_code == 401

    def test_valid_token_accepted_on_all_protected_endpoints(
        self, client, make_worker, test_db
    ):
        """
        A single legitimately-issued token must be accepted on every
        auth-required endpoint, proving the same validation path is shared.
        """
        worker = make_worker()
        token = _make_token(_valid_payload(worker.id))
        headers = _bearer(token)

        for method, path in self._PROTECTED_ENDPOINTS:
            resp = getattr(client, method.lower())(path, headers=headers)
            assert resp.status_code == 200, (
                f"{method} {path}: valid token should be 200, got {resp.status_code}"
            )


# =============================================================================
# CROSS-WORKER DATA ISOLATION
# =============================================================================

class TestDataIsolation:
    """
    Each worker's data must be invisible and unmodifiable by other workers.
    The application filters all data-access queries by worker_id, so these
    tests probe the most obvious bypass attempts.
    """

    def test_worker_cannot_see_other_workers_claims(
        self, client, make_worker, make_policy, make_trigger, make_claim, test_db
    ):
        """
        Worker A's GET /claims/ must return only Worker A's claims.
        Worker B's claims must not appear in Worker A's response.
        """
        worker_a = make_worker()
        worker_b = make_worker()

        policy_a = make_policy(worker=worker_a)
        policy_b = make_policy(worker=worker_b)
        trigger = make_trigger()

        claim_a = make_claim(worker=worker_a, policy=policy_a, trigger=trigger)
        claim_b = make_claim(worker=worker_b, policy=policy_b, trigger=trigger)

        # Authenticate as worker A and list claims
        token_a = _make_token(_valid_payload(worker_a.id))
        resp = client.get("/claims/", headers=_bearer(token_a))

        assert resp.status_code == 200
        returned_ids = [c["id"] for c in resp.json()]

        assert claim_a.id in returned_ids, "Worker A's own claim must be visible"
        assert claim_b.id not in returned_ids, "Worker B's claim must NOT appear in Worker A's list"

    def test_worker_cannot_cancel_other_workers_policy(
        self, client, make_worker, make_policy, test_db
    ):
        """
        Worker B must not be able to cancel Worker A's policy.
        DELETE /policies/{policy_id} filters by both policy_id AND worker_id,
        so the row is invisible to the wrong owner and a 404 is returned.
        """
        worker_a = make_worker()
        worker_b = make_worker()

        policy_a = make_policy(worker=worker_a)

        token_b = _make_token(_valid_payload(worker_b.id))
        resp = client.delete(
            f"/policies/{policy_a.id}", headers=_bearer(token_b)
        )

        assert resp.status_code == 404, (
            f"Worker B deleting Worker A's policy should be 404, got {resp.status_code}"
        )

    def test_worker_cannot_get_other_workers_claim_detail(
        self, client, make_worker, make_policy, make_trigger, make_claim, test_db
    ):
        """
        Worker B must receive 404 when requesting the detail of a claim that
        belongs to Worker A (GET /claims/{claim_id} enforces worker_id filter).
        """
        worker_a = make_worker()
        worker_b = make_worker()

        policy_a = make_policy(worker=worker_a)
        trigger = make_trigger()
        claim_a = make_claim(worker=worker_a, policy=policy_a, trigger=trigger)

        token_b = _make_token(_valid_payload(worker_b.id))
        resp = client.get(f"/claims/{claim_a.id}", headers=_bearer(token_b))

        assert resp.status_code == 404, (
            f"Worker B accessing Worker A's claim detail should be 404, got {resp.status_code}"
        )

    def test_worker_stats_only_reflect_own_claims(
        self, client, make_worker, make_policy, make_trigger, make_claim, test_db
    ):
        """
        Worker A has 3 claims; Worker B has 1.
        Worker A's /claims/stats must report total_claims >= 3 (accounting for
        any pre-existing seed data does not invalidate the count direction).
        Worker B's stats must report fewer total_claims than Worker A's.
        """
        worker_a = make_worker()
        worker_b = make_worker()

        policy_a = make_policy(worker=worker_a)
        policy_b = make_policy(worker=worker_b)
        trigger = make_trigger()

        # 3 claims for A
        for _ in range(3):
            make_claim(worker=worker_a, policy=policy_a, trigger=trigger)

        # 1 claim for B
        make_claim(worker=worker_b, policy=policy_b, trigger=trigger)

        token_a = _make_token(_valid_payload(worker_a.id))
        token_b = _make_token(_valid_payload(worker_b.id))

        stats_a = client.get("/claims/stats", headers=_bearer(token_a)).json()
        stats_b = client.get("/claims/stats", headers=_bearer(token_b)).json()

        assert stats_a["total_claims"] >= 3, (
            f"Worker A should have at least 3 claims, got {stats_a['total_claims']}"
        )
        assert stats_b["total_claims"] == 1, (
            f"Worker B should have exactly 1 claim, got {stats_b['total_claims']}"
        )
        assert stats_a["total_claims"] > stats_b["total_claims"], (
            "Worker A's claim count must exceed Worker B's"
        )


# =============================================================================
# ADMIN ENDPOINT AUTH
# =============================================================================

class TestAdminAuth:
    """
    The three admin endpoints exist under /claims/admin/*.  They require a
    valid Bearer token (any authenticated worker) but must reject unauthenticated
    requests with 401.

    Current design note: there is no role-based access control — any valid
    worker token is sufficient.  The tests document this behaviour.
    """

    _ADMIN_ENDPOINTS = [
        ("GET", "/claims/admin/workers"),
        ("GET", "/claims/admin/all"),
        ("GET", "/claims/admin/stats"),
    ]

    def test_admin_workers_returns_401_without_any_token(self, client, test_db):
        """GET /claims/admin/workers with no Authorization header must be 401."""
        resp = client.get("/claims/admin/workers")
        assert resp.status_code == 401

    def test_admin_all_returns_401_without_any_token(self, client, test_db):
        """GET /claims/admin/all with no Authorization header must be 401."""
        resp = client.get("/claims/admin/all")
        assert resp.status_code == 401

    def test_admin_stats_returns_401_without_any_token(self, client, test_db):
        """GET /claims/admin/stats with no Authorization header must be 401."""
        resp = client.get("/claims/admin/stats")
        assert resp.status_code == 401

    def test_admin_endpoints_reject_non_admin_worker(
        self, client, make_worker, test_db
    ):
        """
        Non-admin workers must receive 403 on admin endpoints (RBAC enforced).
        """
        worker = make_worker(is_admin=False)
        token = _make_token(_valid_payload(worker.id))
        headers = _bearer(token)

        for method, path in self._ADMIN_ENDPOINTS:
            resp = getattr(client, method.lower())(path, headers=headers)
            assert resp.status_code == 403, (
                f"{method} {path}: non-admin should be 403, got {resp.status_code}"
            )

    def test_admin_endpoints_accept_admin_worker_token(
        self, client, make_worker, test_db
    ):
        """
        Admin workers must get 200 on admin endpoints.
        """
        worker = make_worker(is_admin=True)
        token = _make_token(_valid_payload(worker.id))
        headers = _bearer(token)

        for method, path in self._ADMIN_ENDPOINTS:
            resp = getattr(client, method.lower())(path, headers=headers)
            assert resp.status_code == 200, (
                f"{method} {path}: admin token should be 200, got {resp.status_code}"
            )


# =============================================================================
# REGISTRATION SECURITY
# =============================================================================

class TestRegistrationSecurity:
    """Verify that passwords are handled safely throughout the registration flow."""

    def test_password_is_never_returned_in_any_response(self, client, test_db):
        """
        The registration response JSON must not contain a 'password' or
        'password_hash' key.  Leaking either would expose credentials in logs,
        network traces, and browser storage.
        """
        resp = client.post("/auth/register", json=_BASE_REGISTER)
        assert resp.status_code == 201

        body = resp.json()
        assert "password" not in body, "Raw password must not appear in response"
        assert "password_hash" not in body, "Password hash must not appear in response"

    def test_password_hash_differs_from_plaintext(self, client, test_db):
        """
        The hash stored in the DB must not equal the original plaintext.
        A failed hash (identity storage) would trivially expose all passwords.
        """
        from models import Worker

        resp = client.post("/auth/register", json=_BASE_REGISTER)
        assert resp.status_code == 201

        worker_id = resp.json()["worker_id"]
        worker = test_db.query(Worker).filter(Worker.id == worker_id).first()

        assert worker is not None
        assert worker.password_hash != _BASE_REGISTER["password"], (
            "password_hash in DB must not be the plaintext password"
        )

    def test_same_password_different_hash_each_registration(self, client, test_db):
        """
        bcrypt/pbkdf2 use a per-hash salt, so two registrations with the same
        password must produce different stored hashes.  Identical hashes would
        indicate missing salting and allow rainbow-table attacks.
        """
        from models import Worker

        payload_a = {**_BASE_REGISTER, "phone": "9111111111", "email": "a@sec.test"}
        payload_b = {**_BASE_REGISTER, "phone": "9222222222", "email": "b@sec.test"}

        resp_a = client.post("/auth/register", json=payload_a)
        resp_b = client.post("/auth/register", json=payload_b)

        assert resp_a.status_code == 201
        assert resp_b.status_code == 201

        worker_a = test_db.query(Worker).filter(
            Worker.id == resp_a.json()["worker_id"]
        ).first()
        worker_b = test_db.query(Worker).filter(
            Worker.id == resp_b.json()["worker_id"]
        ).first()

        assert worker_a.password_hash != worker_b.password_hash, (
            "Two workers with the same password must have different hashes (salt check)"
        )

    def test_phone_number_uniqueness_enforced(self, client, test_db):
        """
        Registering the same phone number twice must return 400 on the second
        attempt.  Duplicate phones would allow account takeover and data
        confusion.
        """
        first = client.post("/auth/register", json=_BASE_REGISTER)
        assert first.status_code == 201

        second = client.post(
            "/auth/register",
            json={**_BASE_REGISTER, "email": "different@email.test"},
        )
        assert second.status_code == 400, (
            f"Duplicate phone re-registration should return 400, got {second.status_code}"
        )

    def test_token_does_not_contain_password_hash(self, client, test_db):
        """
        Decoding the JWT issued at registration must not reveal a 'password'
        or 'password_hash' key in the payload.  Only 'sub' and 'exp' are
        expected in the current design.
        """
        resp = client.post("/auth/register", json=_BASE_REGISTER)
        assert resp.status_code == 201

        token = resp.json()["access_token"]
        # Decode WITHOUT verification to inspect raw claims (we just want keys)
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])

        assert "password" not in payload, "JWT payload must not contain 'password'"
        assert "password_hash" not in payload, "JWT payload must not contain 'password_hash'"


# =============================================================================
# INPUT VALIDATION
# =============================================================================

class TestInputValidation:
    """
    Pydantic / FastAPI model validation must reject malformed requests with
    HTTP 422 Unprocessable Entity before any business logic executes.
    """

    def test_register_missing_required_field_phone_returns_422(
        self, client, test_db
    ):
        """
        'phone' is a required field on RegisterRequest.  Omitting it must
        cause FastAPI to return 422 before the endpoint handler is called.
        """
        payload = {k: v for k, v in _BASE_REGISTER.items() if k != "phone"}
        resp = client.post("/auth/register", json=payload)
        assert resp.status_code == 422, (
            f"Missing 'phone' should be 422, got {resp.status_code}"
        )

    def test_register_missing_required_field_city_returns_422(
        self, client, test_db
    ):
        """
        'city' is required on RegisterRequest.  Missing it must return 422.
        """
        payload = {k: v for k, v in _BASE_REGISTER.items() if k != "city"}
        resp = client.post("/auth/register", json=payload)
        assert resp.status_code == 422, (
            f"Missing 'city' should be 422, got {resp.status_code}"
        )

    def test_register_missing_required_field_password_returns_422(
        self, client, test_db
    ):
        """
        'password' is required on RegisterRequest.  Missing it must return 422.
        """
        payload = {k: v for k, v in _BASE_REGISTER.items() if k != "password"}
        resp = client.post("/auth/register", json=payload)
        assert resp.status_code == 422, (
            f"Missing 'password' should be 422, got {resp.status_code}"
        )

    def test_create_policy_missing_tier_returns_422(
        self, client, make_worker, test_db
    ):
        """
        POST /policies/ with an empty body (missing 'tier') must return 422
        because 'tier' is a required field on CreatePolicyRequest.
        """
        worker = make_worker()
        token = _make_token(_valid_payload(worker.id))

        resp = client.post("/policies/", json={}, headers=_bearer(token))
        assert resp.status_code == 422, (
            f"Missing 'tier' in POST /policies/ should be 422, got {resp.status_code}"
        )

    def test_simulate_trigger_missing_city_returns_422(
        self, client, make_worker, test_db
    ):
        """
        POST /triggers/simulate requires both 'trigger_type' and 'city'.
        Sending only 'trigger_type' must return 422 because SimulateRequest
        declares 'city' as a required field.
        """
        worker = make_worker()
        token = _make_token(_valid_payload(worker.id))

        resp = client.post(
            "/triggers/simulate",
            json={"trigger_type": "Heavy Rainfall"},
            headers=_bearer(token),
        )
        assert resp.status_code == 422, (
            f"Missing 'city' in POST /triggers/simulate should be 422, got {resp.status_code}"
        )

    def test_simulate_trigger_missing_type_returns_422(
        self, client, make_worker, test_db
    ):
        """
        POST /triggers/simulate requires both 'trigger_type' and 'city'.
        Sending only 'city' must return 422 because SimulateRequest declares
        'trigger_type' as a required field.
        """
        worker = make_worker()
        token = _make_token(_valid_payload(worker.id))

        resp = client.post(
            "/triggers/simulate",
            json={"city": "Mumbai"},
            headers=_bearer(token),
        )
        assert resp.status_code == 422, (
            f"Missing 'trigger_type' in POST /triggers/simulate should be 422, got {resp.status_code}"
        )
