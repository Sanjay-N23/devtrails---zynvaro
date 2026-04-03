"""
Zynvaro — ML Fraud Detection Model (Option A)
RandomForestClassifier trained on 2,000 synthetic gig-worker claim samples.
Replaces hard-coded if/else thresholds with probabilistic fraud scoring.

Features (10-dimensional):
  0  city_match           — worker city == trigger city (1/0)
  1  device_attested      — device attestation passed (1/0)
  2  same_week_claims     — # other claims this week (capped at 5)
  3  claim_history_norm   — total historical claims / 20 (normalized)
  4  hour_of_day_norm     — hour submitted / 23 (0=midnight, 1=11pm)
  5  trigger_type_norm    — trigger category encoded (0-5) / 5
  6  payout_norm          — payout amount / 1000 (normalized)
  7  streak_norm          — disruption-free streak weeks / 12
  8  city_x_device        — interaction: city_match AND device_attested
  9  mismatch_x_freq      — interaction: city_mismatch × same_week_claims

Model is trained once at import with a fixed seed → deterministic, ~150ms.
"""

import numpy as np
from datetime import datetime
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.model_selection import train_test_split

# ─────────────────────────────────────────────────────────────────
# TRIGGER TYPE ENCODING
# ─────────────────────────────────────────────────────────────────
TRIGGER_TYPE_MAP = {
    "Heavy Rainfall":          0,
    "Extreme Rain / Flooding": 1,
    "Severe Heatwave":         2,
    "Hazardous AQI":           3,
    "Platform Outage":         4,
    "Civil Disruption":        5,
}

# ─────────────────────────────────────────────────────────────────
# FEATURE EXTRACTION
# ─────────────────────────────────────────────────────────────────
def extract_features(
    city_match: bool,
    device_attested: bool,
    same_week_claims: int,
    claim_history_count: int,
    hour_of_day: int = None,
    trigger_type: str = None,
    payout_amount: float = None,
    disruption_streak: int = 0,
) -> np.ndarray:
    """
    Convert raw claim signals into a 10-dimensional feature vector.
    All values normalized to [0, 1] for consistent RF feature importance.
    """
    if hour_of_day is None:
        hour_of_day = datetime.utcnow().hour

    trigger_enc = TRIGGER_TYPE_MAP.get(trigger_type or "Hazardous AQI", 3)
    payout_norm = min(1.0, float(payout_amount or 500) / 1000.0)
    city_int = int(bool(city_match))
    device_int = int(bool(device_attested))
    swc = min(int(same_week_claims), 5)
    hist_norm = min(int(claim_history_count), 20) / 20.0
    streak_norm = min(int(disruption_streak), 12) / 12.0

    return np.array([[
        city_int,                          # 0: city match
        device_int,                        # 1: device attestation
        swc,                               # 2: same-week claims (raw, 0-5)
        hist_norm,                         # 3: claim history normalized
        hour_of_day / 23.0,                # 4: time of day
        trigger_enc / 5.0,                 # 5: trigger type
        payout_norm,                       # 6: payout amount normalized
        streak_norm,                       # 7: loyalty (streak) normalized
        city_int * device_int,             # 8: interaction: both valid
        (1 - city_int) * swc,              # 9: interaction: mismatch × freq
    ]])


# ─────────────────────────────────────────────────────────────────
# SYNTHETIC TRAINING DATA
# ─────────────────────────────────────────────────────────────────
def _generate_training_data(n_samples: int = 2000, seed: int = 42) -> tuple:
    """
    Generate 2,000 realistic synthetic claim scenarios for training.

    Fraud label is probabilistic — derived from domain knowledge:
      - City mismatch: +55% fraud probability (largest signal)
      - No device attestation: +25%
      - ≥3 same-week claims: +30%; ≥2: +15%; 1: +5%
      - History > 10: +15%; > 5: +8%
      - Unusual hours (before 7am / after 10pm): +5%
      - Long disruption-free streak: -10% (loyal workers less likely)
      - Gaussian noise σ=0.03 → smooth decision boundary for RF

    Overall fraud rate in synthetic dataset: ~15% (realistic for insurance).
    """
    rng = np.random.RandomState(seed)
    X, y = [], []

    for _ in range(n_samples):
        city_match       = rng.rand() > 0.22
        device_attested  = rng.rand() > 0.10
        same_week_claims = min(int(rng.poisson(0.45)), 5)
        claim_history    = min(int(rng.poisson(1.8)), 20)
        hour             = int(rng.choice(range(6, 24), p=None))  # uniform 6am-midnight
        trigger_type     = int(rng.randint(0, 6))
        payout_norm      = float(rng.uniform(0.15, 0.95))
        streak           = int(rng.randint(0, 13))

        # ── Fraud probability assembly ──────────────────────────────
        p = 0.04  # baseline 4% fraud rate

        if not city_match:
            p += 0.55
        if not device_attested:
            p += 0.25
        if same_week_claims >= 3:
            p += 0.30
        elif same_week_claims == 2:
            p += 0.15
        elif same_week_claims == 1:
            p += 0.05
        if claim_history > 10:
            p += 0.15
        elif claim_history > 5:
            p += 0.08
        if hour < 7 or hour > 22:
            p += 0.05
        if streak > 8:
            p -= 0.10

        # Add interaction: if city matches AND device valid → strong legitimacy signal
        if city_match and device_attested and same_week_claims == 0:
            p -= 0.05

        p = float(np.clip(p + rng.normal(0, 0.03), 0.0, 1.0))
        is_fraud = int(rng.rand() < p)

        city_int   = int(city_match)
        device_int = int(device_attested)
        swc        = same_week_claims
        hist_norm  = claim_history / 20.0
        streak_norm = streak / 12.0

        X.append([
            city_int,
            device_int,
            swc,
            hist_norm,
            hour / 23.0,
            trigger_type / 5.0,
            payout_norm,
            streak_norm,
            city_int * device_int,
            (1 - city_int) * swc,
        ])
        y.append(is_fraud)

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int32)


# ─────────────────────────────────────────────────────────────────
# MODEL TRAINING (runs once at import)
# ─────────────────────────────────────────────────────────────────
def _train_model() -> tuple:
    """Train RandomForestClassifier and return (model, scaler, val_accuracy)."""
    X, y = _generate_training_data(n_samples=2000, seed=42)
    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, random_state=42)

    scaler = StandardScaler()
    X_train_s = scaler.fit_transform(X_train)
    X_val_s   = scaler.transform(X_val)

    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=8,
        min_samples_leaf=5,
        class_weight="balanced",   # handles imbalanced fraud rate
        random_state=42,
        n_jobs=-1,
    )
    clf.fit(X_train_s, y_train)

    val_accuracy = clf.score(X_val_s, y_val)
    print(f"[FraudML] RandomForestClassifier trained - val accuracy: {val_accuracy:.1%} (200 trees, 2000 samples)")

    return clf, scaler, val_accuracy


# Module-level model (trained once on import)
_MODEL, _SCALER, _VAL_ACCURACY = _train_model()

# Feature names for explanation
_FEATURE_NAMES = [
    "City Match",
    "Device Attestation",
    "Same-Week Claims",
    "Claim History",
    "Hour of Day",
    "Trigger Type",
    "Payout Amount",
    "Loyalty Streak",
    "City+Device Combined",
    "Mismatch×Frequency",
]


# ─────────────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────────────
def predict_fraud_probability(
    city_match: bool,
    device_attested: bool,
    same_week_claims: int,
    claim_history_count: int,
    hour_of_day: int = None,
    trigger_type: str = None,
    payout_amount: float = None,
    disruption_streak: int = 0,
) -> float:
    """
    Returns ML-predicted probability of fraud (0.0 = legitimate, 1.0 = fraud).
    """
    X = extract_features(
        city_match, device_attested, same_week_claims, claim_history_count,
        hour_of_day, trigger_type, payout_amount, disruption_streak,
    )
    X_scaled = _SCALER.transform(X)
    prob = float(_MODEL.predict_proba(X_scaled)[0][1])
    return round(prob, 4)


def get_ml_fraud_decision(
    city_match: bool,
    device_attested: bool,
    same_week_claims: int,
    claim_history_count: int,
    hour_of_day: int = None,
    trigger_type: str = None,
    payout_amount: float = None,
    disruption_streak: int = 0,
) -> dict:
    """
    Full ML fraud assessment with authenticity score, decision, and top signals.

    Authenticity score = (1 - fraud_probability) × 100
    Thresholds (same as rule-based for consistency):
      ≥ 75 → AUTO_APPROVED
      45–74 → PENDING_REVIEW
      < 45  → MANUAL_REVIEW
    """
    fraud_prob = predict_fraud_probability(
        city_match, device_attested, same_week_claims, claim_history_count,
        hour_of_day, trigger_type, payout_amount, disruption_streak,
    )
    ml_score = round((1.0 - fraud_prob) * 100, 1)

    # Decision
    if ml_score >= 75:
        decision = "AUTO_APPROVED"
        decision_label = "✅ Auto-Approved (ML)"
    elif ml_score >= 45:
        decision = "PENDING_REVIEW"
        decision_label = "⏳ Escrow Hold — 2hr Review (ML)"
    else:
        decision = "MANUAL_REVIEW"
        decision_label = "🔍 Manual Review Required (ML)"

    # Top risk signals from feature importances (SHAP-style)
    X = extract_features(
        city_match, device_attested, same_week_claims, claim_history_count,
        hour_of_day, trigger_type, payout_amount, disruption_streak,
    )
    importances = _MODEL.feature_importances_
    feature_contributions = [
        {"feature": _FEATURE_NAMES[i], "importance": round(float(importances[i]), 4), "value": round(float(X[0][i]), 4)}
        for i in range(len(_FEATURE_NAMES))
    ]
    feature_contributions.sort(key=lambda x: x["importance"], reverse=True)

    flags = []
    if not city_match:
        flags.append("⚠️ City mismatch — ML flagged high-risk")
    if not device_attested:
        flags.append("⚠️ Device attestation failed")
    if same_week_claims >= 2:
        flags.append(f"⚠️ {same_week_claims} claims this week — frequency anomaly")
    if claim_history_count > 5:
        flags.append(f"⚠️ {claim_history_count} historical claims — elevated pattern")
    if fraud_prob > 0.5 and city_match:
        flags.append("⚠️ ML detected unusual signal combination")

    return {
        "ml_score": ml_score,
        "fraud_probability": fraud_prob,
        "decision": decision,
        "decision_label": decision_label,
        "flags": flags,
        "top_signals": feature_contributions[:5],
        "model_confidence": round(abs(fraud_prob - 0.5) * 2, 3),  # 0=uncertain, 1=very confident
        "model_accuracy": round(_VAL_ACCURACY, 4),
        "model_type": "RandomForestClassifier (200 trees)",
    }


def get_model_info() -> dict:
    """Return metadata about the trained fraud model (for admin API)."""
    return {
        "model_type": "RandomForestClassifier",
        "n_estimators": 200,
        "max_depth": 8,
        "training_samples": 2000,
        "validation_accuracy": round(_VAL_ACCURACY, 4),
        "features": _FEATURE_NAMES,
        "feature_importances": [
            {"feature": name, "importance": round(float(imp), 4)}
            for name, imp in zip(_FEATURE_NAMES, _MODEL.feature_importances_)
        ],
        "fraud_decision_thresholds": {
            "auto_approved": "authenticity_score >= 75",
            "pending_review": "45 <= authenticity_score < 75",
            "manual_review": "authenticity_score < 45",
        },
    }
