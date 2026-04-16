"""
Zynvaro — Razorpay Webhook Handler (Phase 3: SOAR)
Receives payment status callbacks from Razorpay and updates PayoutTransaction lifecycle.

Events handled:
  - payout.processed → SETTLED (payment successful, UTR stored)
  - payout.failed    → FAILED (failure reason stored)
  - payout.reversed  → REVERSED (refund/chargeback)

Security: Verifies X-Razorpay-Signature header when webhook secret is configured.
"""

import json
import hmac
import hashlib
from datetime import datetime

from fastapi import APIRouter, Request, Depends, HTTPException
from sqlalchemy.orm import Session

from database import get_db
from models import PayoutTransaction, PayoutTransactionStatus, Claim

router = APIRouter(prefix="/webhooks", tags=["Webhooks"])


def _verify_razorpay_signature(body: bytes, signature: str, secret: str) -> bool:
    """Verify Razorpay webhook signature using HMAC-SHA256."""
    if not secret:
        return True  # Skip verification if no webhook secret configured (test mode)
    expected = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature)


@router.post("/razorpay")
async def razorpay_webhook(request: Request, db: Session = Depends(get_db)):
    """
    Handle Razorpay webhook callbacks for payout status updates.

    Razorpay sends POST requests to this endpoint when payout status changes.
    In test mode, payouts to success@razorpay settle almost instantly.

    Webhook payload structure:
    {
        "event": "payout.processed",
        "payload": {
            "payout": {
                "entity": {
                    "id": "pout_xxxxx",
                    "status": "processed",
                    "utr": "XXXXXXXXXXXXX",
                    "amount": 30000,  // in paise
                    "reference_id": "ZYN-RZP-XXXX",
                    ...
                }
            }
        }
    }
    """
    body = await request.body()
    signature = request.headers.get("X-Razorpay-Signature", "")

    # Verify signature
    from services.payout_service import RAZORPAY_WEBHOOK_SECRET
    if RAZORPAY_WEBHOOK_SECRET and not _verify_razorpay_signature(body, signature, RAZORPAY_WEBHOOK_SECRET):
        raise HTTPException(status_code=400, detail="Invalid webhook signature")

    try:
        payload = json.loads(body)
    except json.JSONDecodeError:
        raise HTTPException(status_code=400, detail="Invalid JSON payload")

    event = payload.get("event", "")
    payout_entity = payload.get("payload", {}).get("payout", {}).get("entity", {})

    if not payout_entity:
        return {"status": "ignored", "reason": "no payout entity in payload"}

    razorpay_payout_id = payout_entity.get("id", "")
    reference_id = payout_entity.get("reference_id", "")

    # Find matching PayoutTransaction by reference_id (our internal_txn_id)
    txn = None
    if reference_id:
        txn = db.query(PayoutTransaction).filter(
            PayoutTransaction.internal_txn_id == reference_id
        ).first()

    # Fallback: search by razorpay payout_id in gateway_payload
    if not txn and razorpay_payout_id:
        txn = db.query(PayoutTransaction).filter(
            PayoutTransaction.gateway_payload.contains(razorpay_payout_id)
        ).first()

    if not txn:
        print(f"[Webhook] No matching transaction for payout {razorpay_payout_id} / ref {reference_id}")
        return {"status": "ignored", "reason": "no matching transaction"}

    # Get associated claim
    claim = db.query(Claim).filter(Claim.id == txn.claim_id).first()

    # Process event
    if event == "payout.processed":
        txn.status = PayoutTransactionStatus.SETTLED
        txn.settled_at = datetime.utcnow()
        txn.upi_ref = payout_entity.get("utr") or razorpay_payout_id
        txn.amount_settled = (payout_entity.get("amount") or 0) / 100.0  # paise → INR
        txn.gateway_payload = json.dumps(payout_entity)

        # Update claim with real UTR
        if claim:
            utr = payout_entity.get("utr") or razorpay_payout_id
            claim.payment_ref = f"RZP-{utr}"
        print(f"[Webhook] Payout settled: {razorpay_payout_id} UTR={txn.upi_ref}")

    elif event == "payout.failed":
        txn.status = PayoutTransactionStatus.FAILED
        txn.failure_reason = payout_entity.get("failure_reason") or payout_entity.get("status_details", {}).get("description", "Unknown")
        txn.gateway_payload = json.dumps(payout_entity)
        print(f"[Webhook] Payout failed: {razorpay_payout_id} reason={txn.failure_reason}")

    elif event == "payout.reversed":
        txn.status = PayoutTransactionStatus.REVERSED
        txn.gateway_payload = json.dumps(payout_entity)
        print(f"[Webhook] Payout reversed: {razorpay_payout_id}")

    else:
        print(f"[Webhook] Unhandled event: {event}")
        return {"status": "ignored", "reason": f"unhandled event: {event}"}

    db.commit()
    return {"status": "ok", "event": event, "payout_id": razorpay_payout_id}


@router.get("/razorpay/health")
def webhook_health():
    """Health check for webhook endpoint (useful for Razorpay webhook URL verification)."""
    from services.payout_service import is_razorpay_configured, RAZORPAY_WEBHOOK_SECRET
    return {
        "status": "ok",
        "razorpay_configured": is_razorpay_configured(),
        "webhook_secret_configured": bool(RAZORPAY_WEBHOOK_SECRET),
        "signature_verification": "enabled" if RAZORPAY_WEBHOOK_SECRET else "disabled",
        "premium_checkout_mode": "razorpay_checkout" if is_razorpay_configured() else "mock_order",
        "claim_payout_mode": "demo_reference_flow",
        "claim_payout_note": (
            "Claim payouts currently record demo/test references for audit UX. "
            "They are not a confirmed outbound bank-transfer rail."
        ),
        "endpoint": "/webhooks/razorpay",
    }
