from sqlalchemy import Column, Integer, String, Float, Boolean, DateTime, Enum, Text, ForeignKey
from sqlalchemy.orm import relationship
from database import Base
from datetime import datetime
import enum

class DeliveryPlatform(str, enum.Enum):
    BLINKIT = "Blinkit"
    ZEPTO = "Zepto"
    INSTAMART = "Instamart"
    ZOMATO = "Zomato"
    SWIGGY = "Swiggy"
    AMAZON = "Amazon"
    FLIPKART = "Flipkart"

class PolicyTier(str, enum.Enum):
    BASIC = "Basic Shield"
    STANDARD = "Standard Guard"
    PRO = "Pro Armor"

class PolicyStatus(str, enum.Enum):
    ACTIVE = "active"
    EXPIRED = "expired"
    CANCELLED = "cancelled"

class ClaimStatus(str, enum.Enum):
    AUTO_APPROVED = "auto_approved"
    PENDING_REVIEW = "pending_review"
    MANUAL_REVIEW = "manual_review"
    PAID = "paid"
    REJECTED = "rejected"

class TriggerType(str, enum.Enum):
    HEAVY_RAINFALL = "Heavy Rainfall"
    EXTREME_RAIN = "Extreme Rain / Flooding"
    SEVERE_HEATWAVE = "Severe Heatwave"
    HAZARDOUS_AQI = "Hazardous AQI"
    PLATFORM_OUTAGE = "Platform Outage"
    CIVIL_DISRUPTION = "Civil Disruption"

# ─────────────────────────────────────────────────────────────────
# WORKER
# ─────────────────────────────────────────────────────────────────
class Worker(Base):
    __tablename__ = "workers"

    id = Column(Integer, primary_key=True, index=True)
    full_name = Column(String(100), nullable=False)
    phone = Column(String(15), unique=True, nullable=False, index=True)
    email = Column(String(150), unique=True, nullable=True)
    password_hash = Column(String(200), nullable=False)

    city = Column(String(50), nullable=False)
    pincode = Column(String(10), nullable=False)
    platform = Column(String(30), nullable=False)
    vehicle_type = Column(String(20), default="2-Wheeler")
    shift = Column(String(30), default="Evening Peak (6PM-2AM)")

    # Risk profile
    zone_risk_score = Column(Float, default=0.5)       # 0.0 (low) to 1.0 (high)
    claim_history_count = Column(Integer, default=0)
    disruption_streak = Column(Integer, default=0)     # consecutive disruption-free weeks

    # GPS / Location (Advanced Fraud Detection — Phase 3)
    last_known_lat = Column(Float, nullable=True)       # Last GPS latitude
    last_known_lng = Column(Float, nullable=True)       # Last GPS longitude
    last_location_at = Column(DateTime, nullable=True)  # When GPS was captured
    home_lat = Column(Float, nullable=True)             # Registered home lat (from pincode)
    home_lng = Column(Float, nullable=True)             # Registered home lng (from pincode)

    # Behavioral profile (Advanced Fraud Detection — Phase 3)
    avg_claims_per_week = Column(Float, default=0.0)    # Rolling average
    last_claim_city = Column(String(50), nullable=True) # City of most recent claim
    last_claim_at = Column(DateTime, nullable=True)     # Timestamp of most recent claim
    fraud_flag_count = Column(Integer, default=0)       # Cumulative fraud flags received

    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=True)  # Hackathon demo: all workers are admin
    created_at = Column(DateTime, default=datetime.utcnow)

    policies = relationship("Policy", back_populates="worker")
    claims = relationship("Claim", back_populates="worker")

# ─────────────────────────────────────────────────────────────────
# POLICY
# ─────────────────────────────────────────────────────────────────
class Policy(Base):
    __tablename__ = "policies"

    id = Column(Integer, primary_key=True, index=True)
    worker_id = Column(Integer, ForeignKey("workers.id"), nullable=False)
    policy_number = Column(String(20), unique=True, nullable=False)
    tier = Column(String(30), nullable=False)
    status = Column(String(20), default=PolicyStatus.ACTIVE)

    weekly_premium = Column(Float, nullable=False)     # ₹ calculated by ML
    base_premium = Column(Float, nullable=False)       # ₹ base before adjustments
    max_daily_payout = Column(Float, nullable=False)
    max_weekly_payout = Column(Float, nullable=False)

    # Premium breakdown (SHAP-like explanation)
    zone_loading = Column(Float, default=0.0)
    seasonal_loading = Column(Float, default=0.0)
    claim_loading = Column(Float, default=0.0)
    streak_discount = Column(Float, default=0.0)

    start_date = Column(DateTime, default=datetime.utcnow)
    end_date = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    worker = relationship("Worker", back_populates="policies")
    claims = relationship("Claim", back_populates="policy")

# ─────────────────────────────────────────────────────────────────
# TRIGGER EVENT
# ─────────────────────────────────────────────────────────────────
class TriggerEvent(Base):
    __tablename__ = "trigger_events"

    id = Column(Integer, primary_key=True, index=True)
    trigger_type = Column(String(40), nullable=False)
    city = Column(String(50), nullable=False)
    pincode = Column(String(10), nullable=True)

    # Measured values
    measured_value = Column(Float, nullable=False)     # e.g. 72.5 (mm), 485 (AQI)
    threshold_value = Column(Float, nullable=False)    # e.g. 64.5 (mm), 400 (AQI)
    unit = Column(String(20), nullable=False)          # e.g. "mm/24hr", "AQI"

    # Dual-source validation
    source_primary = Column(String(50), nullable=False)
    source_secondary = Column(String(50), nullable=True)
    is_validated = Column(Boolean, default=False)

    severity = Column(String(20), default="moderate")  # low / moderate / high / extreme
    description = Column(Text, nullable=True)

    # Trigger zone GPS (Advanced Fraud Detection — Phase 3)
    trigger_lat = Column(Float, nullable=True)          # Trigger zone center latitude
    trigger_lng = Column(Float, nullable=True)          # Trigger zone center longitude

    detected_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)

    claims = relationship("Claim", back_populates="trigger_event")

# ─────────────────────────────────────────────────────────────────
# CLAIM
# ─────────────────────────────────────────────────────────────────
class Claim(Base):
    __tablename__ = "claims"

    id = Column(Integer, primary_key=True, index=True)
    claim_number = Column(String(20), unique=True, nullable=False)
    worker_id = Column(Integer, ForeignKey("workers.id"), nullable=False)
    policy_id = Column(Integer, ForeignKey("policies.id"), nullable=False)
    trigger_event_id = Column(Integer, ForeignKey("trigger_events.id"), nullable=False)

    status = Column(String(30), default=ClaimStatus.PENDING_REVIEW)
    payout_amount = Column(Float, nullable=False)

    # Fraud scoring (0-100, higher = more authentic)
    authenticity_score = Column(Float, default=0.0)
    gps_valid = Column(Boolean, default=False)
    activity_valid = Column(Boolean, default=False)
    device_valid = Column(Boolean, default=False)
    cross_source_valid = Column(Boolean, default=False)
    fraud_flags = Column(Text, nullable=True)

    # GPS at claim time (Advanced Fraud Detection — Phase 3)
    claim_lat = Column(Float, nullable=True)            # Worker GPS at claim creation
    claim_lng = Column(Float, nullable=True)            # Worker GPS at claim creation
    gps_distance_km = Column(Float, nullable=True)      # Distance from trigger zone (km)

    # Advanced fraud metadata (Phase 3)
    ml_fraud_probability = Column(Float, nullable=True) # Raw ML probability (0-1)
    risk_tier = Column(String(20), nullable=True)       # LOW / MEDIUM / HIGH / CRITICAL
    shift_valid = Column(Boolean, default=True)         # Claim within declared shift hours
    weather_cross_valid = Column(Boolean, default=True) # Historical weather confirms trigger
    velocity_valid = Column(Boolean, default=True)      # No impossible travel detected

    # Payout
    upi_id = Column(String(50), nullable=True)
    payment_ref = Column(String(50), nullable=True)
    paid_at = Column(DateTime, nullable=True)

    auto_processed = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    worker = relationship("Worker", back_populates="claims")
    policy = relationship("Policy", back_populates="claims")
    trigger_event = relationship("TriggerEvent", back_populates="claims")
    transactions = relationship("PayoutTransaction", back_populates="claim")

# ─────────────────────────────────────────────────────────────────
# PAYOUT TRANSACTION
# Each UPI payment attempt is a separate row. A Claim may have
# multiple transactions (retries, refunds). Only one should reach
# status=SETTLED for the claim to be considered paid.
# ─────────────────────────────────────────────────────────────────
class PayoutTransactionStatus(str, enum.Enum):
    INITIATED   = "initiated"    # Request sent to payment gateway
    PENDING     = "pending"      # Awaiting UPI confirmation
    SETTLED     = "settled"      # Payment confirmed by gateway/webhook
    FAILED      = "failed"       # Gateway returned failure
    REVERSED    = "reversed"     # Payment reversed / refunded
    RETRYING    = "retrying"     # Scheduled for retry after failure

class PayoutTransaction(Base):
    __tablename__ = "payout_transactions"

    id = Column(Integer, primary_key=True, index=True)

    # Links
    claim_id  = Column(Integer, ForeignKey("claims.id"), nullable=False, index=True)
    worker_id = Column(Integer, ForeignKey("workers.id"), nullable=False, index=True)

    # UPI payment identity
    upi_id          = Column(String(50), nullable=False)           # e.g. worker@okaxis
    upi_ref         = Column(String(80), nullable=True, index=True) # Gateway transaction ID (UTR/RRN)
    internal_txn_id = Column(String(40), unique=True, nullable=False) # Zynvaro-generated idempotency key

    # Amounts
    amount_requested = Column(Float, nullable=False)   # What was sent to the gateway
    amount_settled   = Column(Float, nullable=True)    # Confirmed settled amount (may differ on partial)
    currency         = Column(String(5), default="INR")

    # Status lifecycle
    status          = Column(String(20), default=PayoutTransactionStatus.INITIATED, nullable=False, index=True)
    failure_reason  = Column(String(200), nullable=True)  # Gateway error message on FAILED
    retry_count     = Column(Integer, default=0)
    max_retries     = Column(Integer, default=3)

    # Gateway metadata
    gateway_name    = Column(String(30), default="razorpay")  # razorpay / phonepe / cashfree / mock
    gateway_payload = Column(Text, nullable=True)             # Raw JSON response stored for audit

    # Timestamps
    initiated_at    = Column(DateTime, default=datetime.utcnow, nullable=False)
    settled_at      = Column(DateTime, nullable=True)
    last_updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)

    # Relationships
    claim  = relationship("Claim", back_populates="transactions")
    worker = relationship("Worker")
