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
    last_location_at = Column(DateTime, nullable=True, default=datetime.utcnow)  # When GPS was captured
    last_activity_source = Column(String(30), nullable=True, default="session_ping")  # signup_seed / session_ping / gps_ping
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
    is_renewal = Column(Boolean, default=False)       # True if created via renewal endpoint
    # Waiting-period audit (persisted at bind time, never recomputed)
    claim_eligible_at = Column(DateTime, nullable=True)    # start_date + waiting hours
    waiting_rule_type = Column(String(30), default="24h")  # '24h', '72h', 'next_cycle', 'zero'
    waiting_rule_version = Column(String(20), default="v1")  # frozen at bind time
    previous_policy_id = Column(Integer, ForeignKey("policies.id"), nullable=True)  # for continuity
    previous_policy_end = Column(DateTime, nullable=True)  # end_date of previous policy
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
    
    # Explainability & Trust
    confidence_score = Column(Float, default=100.0)
    source_log = Column(Text, nullable=True)

    # Trigger zone GPS (Advanced Fraud Detection — Phase 3)
    trigger_lat = Column(Float, nullable=True)          # Trigger zone center latitude
    trigger_lng = Column(Float, nullable=True)          # Trigger zone center longitude

    detected_at = Column(DateTime, default=datetime.utcnow)
    expires_at = Column(DateTime, nullable=True)
    is_simulated = Column(Boolean, default=False)   # True for demo-simulated triggers

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
    # ML fraud probability
    ml_fraud_probability = Column(Float, nullable=True)
    
    # Explainability & Appeals
    trigger_confidence_score = Column(Float, default=100.0)
    appeal_status = Column(String(30), default="none")  # none, initiated, processing, resolved_paid, resolved_denied
    appeal_reason = Column(Text, nullable=True)
    appealed_at = Column(DateTime, nullable=True)
    recent_activity_valid = Column(Boolean, default=True)
    recent_activity_at = Column(DateTime, nullable=True)
    recent_activity_age_hours = Column(Float, nullable=True)
    recent_activity_reason = Column(Text, nullable=True)

    # GPS at claim time (Advanced Fraud Detection - Phase 3)
    claim_lat = Column(Float, nullable=True)            # Worker GPS at claim creation
    claim_lng = Column(Float, nullable=True)            # Worker GPS at claim creation
    gps_distance_km = Column(Float, nullable=True)      # Distance from trigger zone (km)

    # Advanced fraud metadata (Phase 3)
    risk_tier = Column(String(20), nullable=True)       # LOW / MEDIUM / HIGH / CRITICAL
    shift_valid = Column(Boolean, default=True)         # Claim within declared shift hours
    weather_cross_valid = Column(Boolean, default=True) # Historical weather confirms trigger
    velocity_valid = Column(Boolean, default=True)      # No impossible travel detected

    # Payout
    upi_id = Column(String(50), nullable=True)
    payment_ref = Column(String(50), nullable=True)
    paid_at = Column(DateTime, nullable=True)

    auto_processed = Column(Boolean, default=True)
    is_simulated = Column(Boolean, default=False)    # True if claim came from simulated trigger
    cooling_off_cleared = Column(Boolean, default=True)       # Was cooling-off met at claim time?
    cooling_off_hours_at_claim = Column(Float, nullable=True) # Policy age (hours) when claim created
    # Waiting-period decision snapshot (persisted at evaluation time, immutable)
    waiting_decision = Column(String(30), nullable=True)      # ELIGIBLE / BLOCKED_WAITING / REVIEW_REQUIRED
    waiting_reason_code = Column(String(50), nullable=True)   # exact reason code at decision time
    claim_eligible_at_snapshot = Column(DateTime, nullable=True)  # claim_eligible_at used at eval time
    event_time_used = Column(DateTime, nullable=True)         # trigger event_time used in waiting check
    waiting_rule_version = Column(String(20), nullable=True)  # rule version at eval time
    created_at = Column(DateTime, default=datetime.utcnow)

    worker = relationship("Worker", back_populates="claims")
    policy = relationship("Policy", back_populates="claims")
    trigger_event = relationship("TriggerEvent", back_populates="claims")

    @property
    def source_log(self):
        return self.trigger_event.source_log if self.trigger_event else None
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

class TransactionType(str, enum.Enum):
    PREMIUM_PAYMENT = "premium_payment"   # Worker pays premium to activate/renew policy
    CLAIM_PAYOUT    = "claim_payout"      # Platform pays worker for approved claim

class PayoutTransaction(Base):
    __tablename__ = "payout_transactions"

    id = Column(Integer, primary_key=True, index=True)

    # Transaction type (Phase 3: distinguishes premium collection vs claim payout)
    transaction_type = Column(String(20), default=TransactionType.CLAIM_PAYOUT, nullable=False)

    # Links
    claim_id  = Column(Integer, ForeignKey("claims.id"), nullable=True, index=True)   # Nullable for premium payments
    policy_id = Column(Integer, ForeignKey("policies.id"), nullable=True, index=True)  # Links premium payments to policy
    worker_id = Column(Integer, ForeignKey("workers.id"), nullable=False, index=True)

    # Payment identity
    upi_id          = Column(String(50), nullable=True)            # UPI VPA (nullable for card/netbanking)
    upi_ref         = Column(String(80), nullable=True, index=True) # Gateway transaction ID (UTR/RRN)
    internal_txn_id = Column(String(40), unique=True, nullable=False) # Zynvaro-generated idempotency key

    # Razorpay Checkout fields (Phase 3: premium payment flow)
    razorpay_order_id   = Column(String(50), nullable=True)   # Razorpay Order ID from create-order
    razorpay_payment_id = Column(String(50), nullable=True)   # Payment ID from Checkout callback

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
    policy = relationship("Policy")
    worker = relationship("Worker")


# ══════════════════════════════════════════════════════════════════
# GRIEVANCE & APPEALS  (Phase 1 MVP)
# ══════════════════════════════════════════════════════════════════

# ─── Status / type constants (plain strings — SQLite-safe) ────────

class CaseStatus:
    SUBMITTED                  = "SUBMITTED"
    ACKNOWLEDGED               = "ACKNOWLEDGED"
    TRIAGED                    = "TRIAGED"
    WAITING_FOR_INTERNAL_REVIEW= "WAITING_FOR_INTERNAL_REVIEW"
    WAITING_FOR_WORKER         = "WAITING_FOR_WORKER"
    WAITING_FOR_INSURER        = "WAITING_FOR_INSURER"
    RESOLVED_UPHELD            = "RESOLVED_UPHELD"
    RESOLVED_REVERSED          = "RESOLVED_REVERSED"
    RESOLVED_PARTIAL           = "RESOLVED_PARTIAL"
    CLOSED                     = "CLOSED"
    CLOSED_EXPIRED             = "CLOSED_EXPIRED"
    REOPENED                   = "REOPENED"

class CaseType:
    APPEAL    = "APPEAL"
    GRIEVANCE = "GRIEVANCE"

class CasePriority:
    LOW    = "LOW"
    NORMAL = "NORMAL"
    HIGH   = "HIGH"
    URGENT = "URGENT"

class DecisionType:
    UPHOLD              = "UPHOLD"
    REVERSE             = "REVERSE"
    PARTIAL             = "PARTIAL"
    REQUEST_INFO        = "REQUEST_INFO"
    NON_APPEALABLE_CLOSED = "NON_APPEALABLE_CLOSED"

class TriageQueue:
    AUTO         = "AUTO"          # deterministic re-check resolves it
    OPS          = "OPS"           # payment/operations issue
    CLAIM_REVIEW = "CLAIM_REVIEW"  # original claim decision may be wrong
    INSURER      = "INSURER"       # formal insurer escalation


# ─── Appeal reason codes ──────────────────────────────────────────

APPEAL_REASON_CODES = {
    "SOURCE_VALUE_DISPUTE":         "The measured value or data source shown appears incorrect.",
    "SOURCE_TIME_WINDOW_DISPUTE":   "The time window used for the trigger is disputed.",
    "ZONE_MISMATCH_DISPUTE":        "I was working in the affected zone but it was not recognised.",
    "SHIFT_OVERLAP_DISPUTE":        "My shift overlapped the event window but was not counted.",
    "RECENT_ACTIVITY_DISPUTE":      "I was active but recent activity was not verified.",
    "WAITING_PERIOD_DISPUTE":       "My policy bind date or renewal continuity appears incorrect.",
    "DUPLICATE_CLAIM_DISPUTE":      "My claim was suppressed as duplicate but I believe it is distinct.",
    "PAYOUT_AMOUNT_DISPUTE":        "The payout amount or formula differs from what I expected.",
    "PAYOUT_CAP_DISPUTE":           "A cap was applied that I believe was incorrect.",
    "MANUAL_REVIEW_DELAY":          "My claim has been in manual review for too long.",
    "PAYOUT_FAILED_AFTER_APPROVAL": "My claim was approved but the payment failed.",
    "WRONG_TRIGGER_CLASSIFICATION": "The trigger type or city used was wrong.",
}

GRIEVANCE_REASON_CODES = {
    "PREMIUM_DEBIT_ISSUE":          "Wrong premium amount or timing.",
    "RENEWAL_ISSUE":                "Auto-renew confusion or incorrect charge.",
    "CONSENT_PRIVACY_ISSUE":        "Data consent or privacy concern.",
    "DATA_CORRECTION_REQUEST":      "Personal data needs correction.",
    "LANGUAGE_ACCESSIBILITY_ISSUE": "App language or accessibility problem.",
    "NOTIFICATION_ISSUE":           "Missed or incorrect notification.",
    "SUPPORT_EXPERIENCE_ISSUE":     "Unhappy with support interaction.",
    "APP_BUG":                      "Technical bug in the app.",
    "GENERAL_SERVICE_COMPLAINT":    "General complaint about Zynvaro.",
}


# ─────────────────────────────────────────────────────────────────
# GRIEVANCE CASE
# ─────────────────────────────────────────────────────────────────
class GrievanceCase(Base):
    __tablename__ = "grievance_cases"

    id              = Column(Integer, primary_key=True, index=True)
    public_case_id  = Column(String(20), unique=True, nullable=False, index=True)  # GRV-2026-XXXXXX

    # Ownership
    worker_id       = Column(Integer, ForeignKey("workers.id"), nullable=False, index=True)

    # Type & category
    case_type       = Column(String(20), nullable=False)   # CaseType.*
    category_code   = Column(String(60), nullable=False)   # APPEAL_REASON_CODES key
    subcategory_code= Column(String(60), nullable=True)

    # Linked entities
    linked_claim_id      = Column(Integer, ForeignKey("claims.id"),             nullable=True)
    linked_payout_txn_id = Column(Integer, ForeignKey("payout_transactions.id"),nullable=True)
    linked_policy_id     = Column(Integer, ForeignKey("policies.id"),           nullable=True)

    # Lifecycle
    status          = Column(String(40), default=CaseStatus.SUBMITTED, nullable=False, index=True)
    priority        = Column(String(10), default=CasePriority.NORMAL)
    severity        = Column(String(10), default="NORMAL")
    channel_origin  = Column(String(20), default="APP")    # APP | WHATSAPP | ADMIN | SYSTEM

    # Timestamps
    created_at      = Column(DateTime, default=datetime.utcnow, nullable=False)
    acknowledged_at = Column(DateTime, nullable=True)
    triaged_at      = Column(DateTime, nullable=True)
    sla_due_at      = Column(DateTime, nullable=True)      # created_at + 72h
    resolved_at     = Column(DateTime, nullable=True)
    closed_at       = Column(DateTime, nullable=True)

    # Assignment
    assigned_team   = Column(String(20), nullable=True)    # TriageQueue.*
    assigned_user_id= Column(Integer, nullable=True)

    # State
    reopen_count        = Column(Integer, default=0)
    latest_reason_code  = Column(String(60), nullable=True)
    worker_summary_text = Column(Text, nullable=True)
    internal_summary_text= Column(Text, nullable=True)
    snapshot_version    = Column(String(10), default="v1")

    # Relationships
    worker          = relationship("Worker")
    linked_claim    = relationship("Claim",             foreign_keys=[linked_claim_id])
    linked_payout   = relationship("PayoutTransaction", foreign_keys=[linked_payout_txn_id])
    linked_policy   = relationship("Policy",            foreign_keys=[linked_policy_id])
    messages        = relationship("GrievanceMessage",  back_populates="case",
                                   order_by="GrievanceMessage.created_at")
    decisions       = relationship("GrievanceDecision", back_populates="case",
                                   order_by="GrievanceDecision.decision_time")
    audit_events    = relationship("GrievanceAuditEvent", back_populates="case",
                                   order_by="GrievanceAuditEvent.created_at")


# ─────────────────────────────────────────────────────────────────
# GRIEVANCE MESSAGE
# ─────────────────────────────────────────────────────────────────
class GrievanceMessage(Base):
    __tablename__ = "grievance_messages"

    id                      = Column(Integer, primary_key=True, index=True)
    case_id                 = Column(Integer, ForeignKey("grievance_cases.id"), nullable=False, index=True)
    sender_type             = Column(String(20), nullable=False)  # WORKER / SYSTEM / SUPPORT / OPS / INSURER
    sender_id               = Column(Integer, nullable=True)      # worker_id or admin user_id
    channel                 = Column(String(20), default="APP")   # APP / INTERNAL / WHATSAPP
    body_text               = Column(Text, nullable=False)
    structured_payload_json = Column(Text, nullable=True)         # JSON for structured data (e.g. decision)
    visible_to_worker       = Column(Boolean, default=True)
    created_at              = Column(DateTime, default=datetime.utcnow)

    case = relationship("GrievanceCase", back_populates="messages")


# ─────────────────────────────────────────────────────────────────
# GRIEVANCE DECISION
# ─────────────────────────────────────────────────────────────────
class GrievanceDecision(Base):
    __tablename__ = "grievance_decisions"

    id                   = Column(Integer, primary_key=True, index=True)
    case_id              = Column(Integer, ForeignKey("grievance_cases.id"), nullable=False, index=True)
    decision_type        = Column(String(30), nullable=False)   # DecisionType.*
    decision_reason_code = Column(String(60), nullable=False)
    worker_visible_text  = Column(Text, nullable=False)         # what worker sees
    internal_note        = Column(Text, nullable=False)         # mandatory audit note
    decided_by           = Column(Integer, nullable=False)      # admin worker_id
    decision_time        = Column(DateTime, default=datetime.utcnow)
    payout_retry_required= Column(Boolean, default=False)
    claim_override_action= Column(String(20), nullable=True)    # APPROVE / REJECT / NONE

    case = relationship("GrievanceCase", back_populates="decisions")


# ─────────────────────────────────────────────────────────────────
# GRIEVANCE AUDIT EVENT
# ─────────────────────────────────────────────────────────────────
class GrievanceAuditEvent(Base):
    __tablename__ = "grievance_audit_events"

    id            = Column(Integer, primary_key=True, index=True)
    case_id       = Column(Integer, ForeignKey("grievance_cases.id"), nullable=False, index=True)
    entity_type   = Column(String(30), nullable=False)   # CASE / CLAIM / PAYOUT
    entity_id     = Column(Integer, nullable=True)
    event_type    = Column(String(50), nullable=False)   # CASE_CREATED, TRIAGED, RESOLVED, etc.
    actor_type    = Column(String(20), nullable=False)   # WORKER / ADMIN / SYSTEM
    actor_id      = Column(Integer, nullable=True)
    old_value_json= Column(Text, nullable=True)
    new_value_json= Column(Text, nullable=True)
    created_at    = Column(DateTime, default=datetime.utcnow)

    case = relationship("GrievanceCase", back_populates="audit_events")


# ─────────────────────────────────────────────────────────────────
# CLAIM SNAPSHOT  (immutable at claim-creation time)
# ─────────────────────────────────────────────────────────────────
class ClaimSnapshot(Base):
    __tablename__ = "claim_snapshots"

    claim_id                   = Column(Integer, ForeignKey("claims.id"),
                                        primary_key=True, unique=True)
    decision_snapshot_json     = Column(Text, nullable=True)  # auto_approved, status, reason
    source_snapshot_json       = Column(Text, nullable=True)  # trigger source chain
    eligibility_snapshot_json  = Column(Text, nullable=True)  # zone, shift, activity, waiting
    payout_formula_snapshot_json= Column(Text, nullable=True) # amount, tier, cap
    created_at                 = Column(DateTime, default=datetime.utcnow)

    claim = relationship("Claim")

