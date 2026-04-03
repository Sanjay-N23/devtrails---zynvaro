"""
Zynvaro Backend — PayoutTransaction Model & State Machine Tests
================================================================
Covers the full PayoutTransaction DB model at the ORM layer.
No API calls are made. All tests operate directly against test_db
(an in-memory SQLite session that is rolled back after each test).

Test classes
------------
TestPayoutTransactionModel       — Field defaults, FK links, relationships
TestPayoutStatusLifecycle        — Status transitions and associated fields
TestPayoutTransactionIdempotency — internal_txn_id uniqueness guarantees
TestPayoutTransactionAmounts     — Amount fields and their nullability rules

Fixture quick-reference (from conftest.py)
------------------------------------------
test_db          — function-scoped SQLAlchemy session (rolled back after test)
make_worker(...) — factory that returns a persisted Worker row
make_policy(...) — factory that returns a persisted Policy row
make_trigger(...)— factory that returns a persisted TriggerEvent row
make_claim(...)  — factory that returns a persisted Claim row
"""

import sys
import os

_BACKEND_DIR = "D:/AeroFyta_DEVTrails-2026_FULL_HANDOFF/zynvaro-app/backend"
if _BACKEND_DIR not in sys.path:
    sys.path.insert(0, _BACKEND_DIR)

_TESTS_DIR = os.path.join(_BACKEND_DIR, "tests")
if _TESTS_DIR not in sys.path:
    sys.path.insert(0, _TESTS_DIR)

import uuid
from datetime import datetime

import pytest
from sqlalchemy.exc import IntegrityError

from models import (
    Claim,
    PayoutTransaction,
    PayoutTransactionStatus,
    Worker,
)


# ─────────────────────────────────────────────────────────────────────────────
# Module-level helper (not a fixture — called with explicit arguments)
# ─────────────────────────────────────────────────────────────────────────────

def _make_txn(
    db,
    claim: Claim,
    worker: Worker,
    *,
    upi_id: str = "worker@okaxis",
    amount_requested: float = 350.0,
    internal_txn_id: str | None = None,
    **kwargs,
) -> PayoutTransaction:
    """
    Create, persist, and refresh a PayoutTransaction row.

    Generates a unique internal_txn_id (UUID4) when one is not provided so
    that multiple calls within the same test never collide on the UNIQUE
    constraint unless the test deliberately supplies a duplicate.
    """
    effective_id = internal_txn_id or str(uuid.uuid4())
    txn = PayoutTransaction(
        claim_id=claim.id,
        worker_id=worker.id,
        upi_id=upi_id,
        internal_txn_id=effective_id,
        amount_requested=amount_requested,
        **kwargs,
    )
    db.add(txn)
    db.commit()
    db.refresh(txn)
    return txn


# ─────────────────────────────────────────────────────────────────────────────
# Shared test fixtures
# ─────────────────────────────────────────────────────────────────────────────

@pytest.fixture()
def worker(make_worker):
    return make_worker()


@pytest.fixture()
def claim(test_db, make_worker, make_policy, make_trigger, make_claim):
    """Return a single claim with its own worker, policy, and trigger."""
    w  = make_worker()
    p  = make_policy(worker=w)
    te = make_trigger(city=w.city)
    return make_claim(worker=w, policy=p, trigger=te)


# =============================================================================
# CLASS 1 — Model defaults, FK links, and relationships
# =============================================================================

class TestPayoutTransactionModel:

    def test_transaction_defaults_to_initiated_status(self, test_db, claim, worker):
        """A newly-created transaction must have status INITIATED."""
        txn = _make_txn(test_db, claim, worker)
        assert txn.status == PayoutTransactionStatus.INITIATED

    def test_transaction_defaults_to_razorpay_gateway(self, test_db, claim, worker):
        """gateway_name must default to 'razorpay' when not supplied."""
        txn = _make_txn(test_db, claim, worker)
        assert txn.gateway_name == "razorpay"

    def test_transaction_defaults_to_inr_currency(self, test_db, claim, worker):
        """currency must default to 'INR' when not supplied."""
        txn = _make_txn(test_db, claim, worker)
        assert txn.currency == "INR"

    def test_transaction_defaults_to_max_3_retries(self, test_db, claim, worker):
        """max_retries must default to 3."""
        txn = _make_txn(test_db, claim, worker)
        assert txn.max_retries == 3

    def test_transaction_defaults_to_zero_retry_count(self, test_db, claim, worker):
        """retry_count must start at 0."""
        txn = _make_txn(test_db, claim, worker)
        assert txn.retry_count == 0

    def test_internal_txn_id_must_be_unique(self, test_db, claim, worker):
        """
        Inserting two transactions with the same internal_txn_id must raise
        IntegrityError due to the UNIQUE constraint on that column.
        """
        shared_id = "DUPLICATE-TXN-001"
        _make_txn(test_db, claim, worker, internal_txn_id=shared_id)

        with pytest.raises(IntegrityError):
            # Attempt to add a second row with the same idempotency key
            dupe = PayoutTransaction(
                claim_id=claim.id,
                worker_id=worker.id,
                upi_id="worker@okaxis",
                internal_txn_id=shared_id,
                amount_requested=200.0,
            )
            test_db.add(dupe)
            test_db.commit()

    def test_transaction_linked_to_claim(self, test_db, claim, worker):
        """txn.claim_id must equal the ID of the claim it was created for."""
        txn = _make_txn(test_db, claim, worker)
        assert txn.claim_id == claim.id

    def test_transaction_linked_to_worker(self, test_db, claim, worker):
        """txn.worker_id must equal the ID of the worker passed in."""
        txn = _make_txn(test_db, claim, worker)
        assert txn.worker_id == worker.id

    def test_claim_has_transactions_relationship(self, test_db, claim, worker):
        """
        After creating a transaction, claim.transactions must be a non-empty
        list that includes the new transaction.
        """
        txn = _make_txn(test_db, claim, worker)

        # Expire the claim so SQLAlchemy re-loads its relationships from the DB
        test_db.expire(claim)
        test_db.refresh(claim)

        assert isinstance(claim.transactions, list)
        assert len(claim.transactions) >= 1
        txn_ids = [t.id for t in claim.transactions]
        assert txn.id in txn_ids


# =============================================================================
# CLASS 2 — Status lifecycle / state-machine transitions
# =============================================================================

class TestPayoutStatusLifecycle:

    def test_initiated_status_is_default(self, test_db, claim, worker):
        """Default status must equal the INITIATED enum member."""
        txn = _make_txn(test_db, claim, worker)
        assert txn.status == PayoutTransactionStatus.INITIATED

    def test_can_transition_to_pending(self, test_db, claim, worker):
        """
        Setting status to PENDING, committing, and re-fetching must yield
        PENDING on the reloaded row.
        """
        txn = _make_txn(test_db, claim, worker)
        txn.status = PayoutTransactionStatus.PENDING
        test_db.commit()

        test_db.expire(txn)
        test_db.refresh(txn)
        assert txn.status == PayoutTransactionStatus.PENDING

    def test_can_transition_to_settled(self, test_db, claim, worker):
        """
        Setting status to SETTLED with amount_settled and settled_at, then
        re-fetching, must yield SETTLED.
        """
        txn = _make_txn(test_db, claim, worker)
        txn.status = PayoutTransactionStatus.SETTLED
        txn.amount_settled = 350.0
        txn.settled_at = datetime.utcnow()
        test_db.commit()

        test_db.expire(txn)
        test_db.refresh(txn)
        assert txn.status == PayoutTransactionStatus.SETTLED

    def test_settled_transaction_has_settled_at(self, test_db, claim, worker):
        """
        After transitioning to SETTLED, settled_at must be a datetime, not None.
        """
        txn = _make_txn(test_db, claim, worker)
        settled_time = datetime.utcnow()
        txn.status = PayoutTransactionStatus.SETTLED
        txn.amount_settled = 350.0
        txn.settled_at = settled_time
        test_db.commit()

        test_db.expire(txn)
        test_db.refresh(txn)
        assert txn.settled_at is not None
        assert isinstance(txn.settled_at, datetime)

    def test_can_transition_to_failed(self, test_db, claim, worker):
        """Transitioning to FAILED with a failure_reason must persist correctly."""
        txn = _make_txn(test_db, claim, worker)
        txn.status = PayoutTransactionStatus.FAILED
        txn.failure_reason = "UPI timeout"
        test_db.commit()

        test_db.expire(txn)
        test_db.refresh(txn)
        assert txn.status == PayoutTransactionStatus.FAILED

    def test_failed_transaction_has_failure_reason(self, test_db, claim, worker):
        """failure_reason must be retrievable after a FAILED transition."""
        txn = _make_txn(test_db, claim, worker)
        reason = "UPI timeout — gateway did not respond within 30s"
        txn.status = PayoutTransactionStatus.FAILED
        txn.failure_reason = reason
        test_db.commit()

        test_db.expire(txn)
        test_db.refresh(txn)
        assert txn.failure_reason == reason

    def test_can_transition_to_retrying(self, test_db, claim, worker):
        """
        A failed transaction can be moved to RETRYING status.
        Status must persist after commit and re-fetch.
        """
        txn = _make_txn(test_db, claim, worker)
        txn.status = PayoutTransactionStatus.FAILED
        txn.failure_reason = "Network error"
        test_db.commit()

        txn.status = PayoutTransactionStatus.RETRYING
        test_db.commit()

        test_db.expire(txn)
        test_db.refresh(txn)
        assert txn.status == PayoutTransactionStatus.RETRYING

    def test_retry_count_increments(self, test_db, claim, worker):
        """
        Incrementing retry_count and committing must persist the new value.
        Starting from 0, after one increment the stored value must be 1.
        """
        txn = _make_txn(test_db, claim, worker)
        assert txn.retry_count == 0

        txn.retry_count += 1
        test_db.commit()

        test_db.expire(txn)
        test_db.refresh(txn)
        assert txn.retry_count == 1

    def test_max_retries_limit_is_enforced_in_business_logic(self, test_db, claim, worker):
        """
        The business rule guard is: retry_count < max_retries.
        When retry_count equals max_retries, no further retry is allowed.

        This test verifies the guard expression evaluates correctly — the DB
        layer stores both values; the caller is responsible for checking the
        guard before incrementing.
        """
        txn = _make_txn(test_db, claim, worker)
        # Exhaust all retries
        txn.retry_count = txn.max_retries  # both default to 3
        test_db.commit()

        test_db.expire(txn)
        test_db.refresh(txn)

        # Guard: retry allowed only when retry_count < max_retries
        retry_allowed = txn.retry_count < txn.max_retries
        assert retry_allowed is False, (
            f"Expected no retry allowed when retry_count ({txn.retry_count}) "
            f"== max_retries ({txn.max_retries})"
        )

    def test_can_transition_to_reversed(self, test_db, claim, worker):
        """
        A settled transaction can be moved to REVERSED.
        Status must equal REVERSED after commit and re-fetch.
        """
        txn = _make_txn(test_db, claim, worker)
        txn.status = PayoutTransactionStatus.SETTLED
        txn.amount_settled = 350.0
        txn.settled_at = datetime.utcnow()
        test_db.commit()

        txn.status = PayoutTransactionStatus.REVERSED
        test_db.commit()

        test_db.expire(txn)
        test_db.refresh(txn)
        assert txn.status == PayoutTransactionStatus.REVERSED


# =============================================================================
# CLASS 3 — Idempotency / duplicate-payment prevention
# =============================================================================

class TestPayoutTransactionIdempotency:

    def test_internal_txn_id_uniqueness_prevents_duplicate_payments(
        self, test_db, claim, worker
    ):
        """
        Inserting two rows with the same internal_txn_id must raise
        IntegrityError — the UNIQUE constraint is the DB-level safeguard
        against processing the same payment twice.
        """
        idempotency_key = "IDEMPOTENT-PAY-001"
        _make_txn(test_db, claim, worker, internal_txn_id=idempotency_key)

        with pytest.raises(IntegrityError):
            dupe = PayoutTransaction(
                claim_id=claim.id,
                worker_id=worker.id,
                upi_id="worker@okaxis",
                internal_txn_id=idempotency_key,
                amount_requested=350.0,
            )
            test_db.add(dupe)
            test_db.commit()

    def test_two_claims_can_have_separate_transactions(
        self, test_db, make_worker, make_policy, make_trigger, make_claim
    ):
        """
        Two different claims (each with a unique internal_txn_id) must both
        insert successfully with no constraint violations.
        """
        w  = make_worker()
        p  = make_policy(worker=w)
        te = make_trigger(city=w.city)

        claim_a = make_claim(worker=w, policy=p, trigger=te)
        claim_b = make_claim(worker=w, policy=p, trigger=te)

        txn_a = _make_txn(
            test_db, claim_a, w,
            internal_txn_id="TXN-CLAIM-A-001",
            amount_requested=300.0,
        )
        txn_b = _make_txn(
            test_db, claim_b, w,
            internal_txn_id="TXN-CLAIM-B-001",
            amount_requested=400.0,
        )

        assert txn_a.id is not None
        assert txn_b.id is not None
        assert txn_a.id != txn_b.id
        assert txn_a.claim_id == claim_a.id
        assert txn_b.claim_id == claim_b.id

    def test_one_claim_can_have_multiple_transaction_attempts(
        self, test_db, claim, worker
    ):
        """
        A single claim may have multiple transaction rows (initial attempt +
        retries), provided each has a distinct internal_txn_id.

        After inserting 3 rows, claim.transactions must contain exactly 3 entries.
        """
        _make_txn(test_db, claim, worker,
                  internal_txn_id="TXN-ATTEMPT-1",
                  status=PayoutTransactionStatus.FAILED,
                  failure_reason="Gateway timeout")
        _make_txn(test_db, claim, worker,
                  internal_txn_id="TXN-ATTEMPT-2",
                  status=PayoutTransactionStatus.FAILED,
                  failure_reason="Insufficient balance")
        _make_txn(test_db, claim, worker,
                  internal_txn_id="TXN-ATTEMPT-3",
                  status=PayoutTransactionStatus.SETTLED,
                  amount_settled=350.0,
                  settled_at=datetime.utcnow())

        test_db.expire(claim)
        test_db.refresh(claim)

        assert len(claim.transactions) == 3

        statuses = {t.status for t in claim.transactions}
        assert PayoutTransactionStatus.FAILED in statuses
        assert PayoutTransactionStatus.SETTLED in statuses


# =============================================================================
# CLASS 4 — Amount fields
# =============================================================================

class TestPayoutTransactionAmounts:

    def test_amount_requested_matches_claim_payout_amount(
        self, test_db, claim, worker
    ):
        """
        When amount_requested is set to claim.payout_amount, the stored value
        must equal the claim's payout_amount (no rounding or transformation).
        """
        txn = _make_txn(
            test_db, claim, worker,
            amount_requested=claim.payout_amount,
        )
        assert txn.amount_requested == claim.payout_amount

    def test_amount_settled_can_differ_from_requested(self, test_db, claim, worker):
        """
        amount_settled may be less than amount_requested (partial settlement).
        Both values must persist independently without any constraint error.
        """
        txn = _make_txn(test_db, claim, worker, amount_requested=500.0)
        txn.status = PayoutTransactionStatus.SETTLED
        txn.amount_settled = 480.0          # ₹20 short — valid partial settlement
        txn.settled_at = datetime.utcnow()
        test_db.commit()

        test_db.expire(txn)
        test_db.refresh(txn)

        assert txn.amount_requested == 500.0
        assert txn.amount_settled == 480.0
        assert txn.amount_settled < txn.amount_requested

    def test_amount_settled_null_until_settled(self, test_db, claim, worker):
        """
        A newly-created transaction must have amount_settled as None.
        The field is only populated when the gateway confirms settlement.
        """
        txn = _make_txn(test_db, claim, worker)
        assert txn.amount_settled is None

    def test_currency_default_is_inr(self, test_db, claim, worker):
        """
        currency must default to 'INR' when not explicitly provided.
        Verified against the value stored in the DB (not just the Python default).
        """
        txn = _make_txn(test_db, claim, worker)

        test_db.expire(txn)
        test_db.refresh(txn)

        assert txn.currency == "INR"
