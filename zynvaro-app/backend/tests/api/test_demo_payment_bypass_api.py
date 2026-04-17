import pytest
import os
from datetime import datetime, timedelta
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from models import Policy, PayoutTransaction, PayoutTransactionStatus, PolicyStatus


def test_bypass_blocked_in_production_mode(authed_client: TestClient, test_db: Session, monkeypatch):
    """Test that the bypass endpoint outright rejects requests in production with MOCK_PAYMENTS off."""
    monkeypatch.setenv("ENVIRONMENT", "production")
    monkeypatch.setenv("MOCK_PAYMENTS", "0")
    
    response = authed_client.post(
        "/policies/demo-bypass",
        json={
            "tier": "Standard Guard",
            "order_id": "order_FAIL123",
            "source_screen": "checkout_failed",
            "original_provider_error": "DECLINED"
        }
    )
    assert response.status_code == 403
    assert "disabled" in response.json()["detail"].lower()


def test_failed_razorpay_flow_can_be_bypassed(authed_client: TestClient, test_db: Session, monkeypatch):
    """Test successful bypass correctly activates a policy and tags transaction DEMO_SETTLED."""
    monkeypatch.setenv("ENVIRONMENT", "demo")
    monkeypatch.setenv("MOCK_PAYMENTS", "1")
    
    order_id = "req_DEMO_ORDER_1234"
    
    response = authed_client.post(
        "/policies/demo-bypass",
        json={
            "tier": "Basic Shield",
            "order_id": order_id,
            "source_screen": "checkout_failed",
            "original_provider_error": "DECLINED_ALREADY_PAID"
        }
    )
    assert response.status_code == 201
    data = response.json()
    assert data["is_demo_bypass"] is True
    assert data["payment_state"] == PayoutTransactionStatus.DEMO_SETTLED
    assert data["original_provider_error"] == "DECLINED_ALREADY_PAID"
    
    # Verify Policy written
    policy = test_db.query(Policy).filter(Policy.id == data["policy_id"]).first()
    assert policy is not None
    assert policy.tier == "Basic Shield"
    assert policy.status == PolicyStatus.ACTIVE
    
    # Verify Audit written correctly
    txn = test_db.query(PayoutTransaction).filter(PayoutTransaction.id == data["transaction_id"]).first()
    assert txn.status == PayoutTransactionStatus.DEMO_SETTLED
    assert txn.is_demo_bypass is True
    assert txn.razorpay_order_id == order_id


def test_double_tap_idempotent_policy_creation(authed_client: TestClient, test_db: Session, monkeypatch):
    """Ensure duplicate bypass requests for the same order resolve cleanly without duplicating active policies."""
    monkeypatch.setenv("ENVIRONMENT", "demo")
    
    payload = {
        "tier": "Pro Armor",
        "order_id": "order_IDEMPOTENT99",
        "source_screen": "button",
        "original_provider_error": "NONE"
    }
    
    # Request 1
    r1 = authed_client.post("/policies/demo-bypass", json=payload)
    assert r1.status_code == 201
    
    # Request 2 (double tap)
    r2 = authed_client.post("/policies/demo-bypass", json=payload)
    assert r2.status_code == 201
    
    # Ensure they return the exact same target objects
    assert r1.json()["policy_id"] == r2.json()["policy_id"]
    assert r1.json()["transaction_id"] == r2.json()["transaction_id"]
    
    # Database integrity check
    policies = test_db.query(Policy).filter(Policy.worker_id == authed_client.worker.id, Policy.status == PolicyStatus.ACTIVE).all()
    assert len(policies) == 1
    
    txns = test_db.query(PayoutTransaction).filter(PayoutTransaction.razorpay_order_id == "order_IDEMPOTENT99").all()
    assert len(txns) == 1


def test_bypass_renewal_flow(authed_client: TestClient, test_db: Session, monkeypatch):
    """Test renewal extends policy correctly rather than creating a new one."""
    monkeypatch.setenv("ENVIRONMENT", "demo")
    
    # Create initial active policy via direct bypass
    r1 = authed_client.post(
        "/policies/demo-bypass",
        json={"tier": "Pro Armor", "order_id": "order_NEW123", "source_screen": "ui", "original_provider_error": ""}
    )
    pol_id = r1.json()["policy_id"]
    initial_policy = test_db.query(Policy).filter(Policy.id == pol_id).first()
    initial_end = initial_policy.end_date
    
    # Renew via Demo Bypass
    r2 = authed_client.post(
        "/policies/demo-bypass",
        json={
            "tier": "Pro Armor", 
            "order_id": "order_RENEW456", 
            "source_screen": "ui", 
            "original_provider_error": "",
            "is_renewal": True
        }
    )
    assert r2.status_code == 201
    
    updated_policy = test_db.query(Policy).filter(Policy.id == pol_id).first()
    assert updated_policy.end_date > initial_end
    
    # Verify 2 transactions exist now
    txns = test_db.query(PayoutTransaction).filter(PayoutTransaction.worker_id == authed_client.worker.id).all()
    assert len(txns) == 2
    for t in txns:
        assert t.is_demo_bypass is True
