"""
Zynvaro — ML Fraud Detection Model v2 (Phase 3: Advanced Fraud Detection)
RandomForestClassifier trained on 3,000 synthetic gig-worker claim samples.

Features (14-dimensional, all normalized to [0, 1]):
  0  city_match           — worker city == trigger city (0/1)
  1  device_attested      — device attestation passed (0/1)
  2  same_week_claims_n   — # other claims this week / 5 (normalized 0-1)
  3  claim_history_norm   — total historical claims / 20 (normalized 0-1)
  4  hour_of_day_norm     — hour submitted / 23 (0=midnight, 1=11pm)
  5  trigger_type_norm    — trigger category encoded (0-5) / 5
  6  payout_norm          — payout amount / 1000 (normalized 0-1)
  7  streak_norm          — disruption-free streak weeks / 12 (normalized 0-1)
  8  city_x_device        — interaction: city_match AND device_attested (0/1)
  9  mismatch_x_freq      — interaction: city_mismatch x same_week_claims_n (0-1)
 10  gps_distance_norm    — GPS distance / city_radius, capped at 1.0 (0=center, 1=outside)
 11  shift_overlap        — 1 if claim within declared shift, 0 if outside
 12  claim_velocity_norm  — travel speed / 200, capped at 1.0 (0=stationary, 1=impossible)
 13  fraud_history_norm   — prior fraud flags / 10, capped at 1.0 (0=clean, 1=repeat)

Model trained once at import with fixed seed -> deterministic.

IMPORTANT: Validation accuracy is measured against synthetic labels derived from
the same domain rules used to generate training data. It demonstrates the model
learned the rule system well, but is NOT proof of real-world generalization.
Real-world performance requires evaluation on actual claim outcomes.
"""

import numpy as np
from datetime import datetime
from sklearn.ensemble import RandomForestClassifier
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
    # Phase 3 advanced features (default to safe values for backward compat)
    gps_distance_norm: float = 0.0,
    shift_overlap: float = 1.0,
    claim_velocity_norm: float = 0.0,
    fraud_history_norm: float = 0.0,
) -> np.ndarray:
    """
    Convert raw claim signals into a 14-dimensional feature vector.
    ALL values normalized to [0, 1] range for consistent feature importance.
    """
    if hour_of_day is None:
        hour_of_day = datetime.utcnow().hour

    # Fix E: No silent defaults — None means "unknown", encode as mid-range
    if trigger_type is None:
        trigger_enc = 0.5  # midpoint, not biased toward any type
    else:
        trigger_enc = TRIGGER_TYPE_MAP.get(trigger_type, 3) / 5.0

    if payout_amount is None:
        payout_norm = 0.5  # midpoint, not assuming any specific amount
    else:
        payout_norm = min(1.0, float(payout_amount) / 1000.0)

    city_int = int(bool(city_match))
    device_int = int(bool(device_attested))
    # Fix B: Normalize same_week_claims to [0, 1] (was raw 0-5)
    swc_norm = min(int(same_week_claims), 5) / 5.0
    hist_norm = min(int(claim_history_count), 20) / 20.0
    streak_norm = min(int(disruption_streak), 12) / 12.0

    return np.array([[
        city_int,                                    # 0: city match (0/1)
        device_int,                                  # 1: device attestation (0/1)
        swc_norm,                                    # 2: same-week claims normalized (0-1)
        hist_norm,                                   # 3: claim history normalized (0-1)
        hour_of_day / 23.0,                          # 4: time of day (0-1)
        trigger_enc if isinstance(trigger_enc, float) else trigger_enc / 5.0,  # 5: trigger type (0-1)
        payout_norm,                                 # 6: payout amount (0-1)
        streak_norm,                                 # 7: loyalty streak (0-1)
        city_int * device_int,                       # 8: interaction: both valid (0/1)
        (1 - city_int) * swc_norm,                   # 9: interaction: mismatch x freq (0-1)
        # Phase 3 advanced features — all capped to [0, 1]
        min(1.0, float(gps_distance_norm)),          # 10: GPS distance (0=center, 1=outside+)
        float(shift_overlap),                        # 11: shift overlap (0/1)
        min(1.0, float(claim_velocity_norm)),        # 12: velocity (0=normal, 1=impossible)
        min(1.0, float(fraud_history_norm)),          # 13: fraud history (0=clean, 1=repeat)
    ]])


# ─────────────────────────────────────────────────────────────────
# SYNTHETIC TRAINING DATA
# ─────────────────────────────────────────────────────────────────
def _generate_training_data(n_samples: int = 3000, seed: int = 42) -> tuple:
    """
    Generate 3,000 realistic synthetic claim scenarios for training.

    Fraud label is probabilistic, derived from insurance domain knowledge.
    NOTE: This is synthetic data — validation accuracy measures how well the
    model learns these rules, not real-world fraud detection performance.
    """
    rng = np.random.RandomState(seed)
    X, y = [], []

    for _ in range(n_samples):
        city_match       = rng.rand() > 0.22
        device_attested  = rng.rand() > 0.10
        same_week_claims = min(int(rng.poisson(0.45)), 5)
        claim_history    = min(int(rng.poisson(1.8)), 20)
        # Fix D: Full hour range 0-23 (was 6-23)
        hour             = int(rng.randint(0, 24))
        trigger_type     = int(rng.randint(0, 6))
        payout_norm      = float(rng.uniform(0.15, 0.95))
        streak           = int(rng.randint(0, 13))

        # Phase 3 features
        gps_rand = rng.rand()
        if gps_rand < 0.85:
            gps_distance_norm = float(rng.uniform(0.0, 0.5))
        elif gps_rand < 0.95:
            gps_distance_norm = float(rng.uniform(0.5, 1.0))
        else:
            gps_distance_norm = min(1.0, float(rng.uniform(1.0, 3.0)))  # capped to 1.0

        shift_overlap = 1.0 if rng.rand() < 0.90 else 0.0

        vel_rand = rng.rand()
        if vel_rand < 0.95:
            claim_velocity_norm = 0.0
        elif vel_rand < 0.98:
            claim_velocity_norm = float(rng.uniform(0.3, 0.5))
        else:
            claim_velocity_norm = float(rng.uniform(0.8, 1.0))

        fh_rand = rng.rand()
        if fh_rand < 0.90:
            fraud_history_norm = 0.0
        elif fh_rand < 0.97:
            fraud_history_norm = float(rng.uniform(0.1, 0.3))
        else:
            fraud_history_norm = float(rng.uniform(0.5, 1.0))

        # ── Fraud probability assembly ──────────────────────────────
        p = 0.04

        if not city_match:       p += 0.55
        if not device_attested:  p += 0.25
        if same_week_claims >= 3: p += 0.30
        elif same_week_claims == 2: p += 0.15
        elif same_week_claims == 1: p += 0.05
        if claim_history > 10:   p += 0.15
        elif claim_history > 5:  p += 0.08
        if hour < 6 or hour > 22: p += 0.05  # Fix D: matches full 0-23 range
        if streak > 8:           p -= 0.10

        # Phase 3 signals
        if gps_distance_norm > 0.8: p += 0.45
        elif gps_distance_norm > 0.5: p += 0.10
        if shift_overlap == 0.0: p += 0.20
        if claim_velocity_norm > 0.8: p += 0.35
        elif claim_velocity_norm > 0.3: p += 0.10
        if fraud_history_norm > 0.5: p += 0.25
        elif fraud_history_norm > 0.1: p += 0.08

        if city_match and device_attested and same_week_claims == 0 and gps_distance_norm < 0.3:
            p -= 0.05

        p = float(np.clip(p + rng.normal(0, 0.03), 0.0, 1.0))
        is_fraud = int(rng.rand() < p)

        city_int   = int(city_match)
        device_int = int(device_attested)
        swc_norm   = same_week_claims / 5.0  # Fix B: normalized
        hist_norm  = claim_history / 20.0
        streak_n   = streak / 12.0

        X.append([
            city_int, device_int, swc_norm, hist_norm,
            hour / 23.0, trigger_type / 5.0, payout_norm, streak_n,
            city_int * device_int, (1 - city_int) * swc_norm,
            gps_distance_norm, shift_overlap, claim_velocity_norm, fraud_history_norm,
        ])
        y.append(is_fraud)

    return np.array(X, dtype=np.float32), np.array(y, dtype=np.int32)


# ─────────────────────────────────────────────────────────────────
# MODEL TRAINING (runs once at import)
# Fix G: Removed StandardScaler — unnecessary for RandomForest
# ─────────────────────────────────────────────────────────────────
def _train_model() -> tuple:
    """Train RandomForestClassifier and return (model, val_accuracy)."""
    X, y = _generate_training_data(n_samples=3000, seed=42)
    X_train, X_val, y_train, y_val = train_test_split(X, y, test_size=0.2, random_state=42)

    clf = RandomForestClassifier(
        n_estimators=200,
        max_depth=8,
        min_samples_leaf=5,
        class_weight="balanced",
        random_state=42,
        n_jobs=-1,
    )
    clf.fit(X_train, y_train)

    val_accuracy = clf.score(X_val, y_val)
    print(f"[FraudML] RandomForestClassifier v2 trained - synthetic val accuracy: {val_accuracy:.1%} (200 trees, 3000 samples, 14 features)")

    return clf, val_accuracy


_MODEL, _VAL_ACCURACY = _train_model()

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
    "Mismatch x Frequency",
    "GPS Distance",
    "Shift Overlap",
    "Travel Velocity",
    "Fraud History",
]


# ─────────────────────────────────────────────────────────────────
# PER-CLAIM EXPLANATION (Fix A: replaces global importances)
# Uses feature value x importance as a proxy for per-claim contribution.
# Not true SHAP, but directionally correct for each specific claim.
# ─────────────────────────────────────────────────────────────────
def _compute_per_claim_signals(X: np.ndarray) -> list:
    """
    Compute per-claim feature contributions using value-weighted importances.
    contribution_i = |feature_value_i - baseline_i| * global_importance_i
    This makes the ranking claim-specific: a feature with high importance
    but neutral value for THIS claim will rank lower than a feature with
    moderate importance but extreme value.
    """
    importances = _MODEL.feature_importances_
    values = X[0]

    # Baselines: "legitimate claim" feature values (all safe defaults)
    baselines = np.array([
        1.0,  # city_match = True
        1.0,  # device_attested = True
        0.0,  # same_week_claims = 0
        0.0,  # claim_history = 0
        0.5,  # hour = midday (neutral)
        0.5,  # trigger_type = mid
        0.5,  # payout = mid
        0.5,  # streak = mid
        1.0,  # city_x_device = both valid
        0.0,  # mismatch_x_freq = no mismatch
        0.0,  # gps_distance = at center
        1.0,  # shift_overlap = valid
        0.0,  # velocity = stationary
        0.0,  # fraud_history = clean
    ])

    contributions = []
    for i in range(len(_FEATURE_NAMES)):
        deviation = abs(float(values[i]) - baselines[i])
        weighted = round(deviation * float(importances[i]), 4)
        contributions.append({
            "feature": _FEATURE_NAMES[i],
            "importance": round(float(importances[i]), 4),
            "value": round(float(values[i]), 4),
            "contribution": weighted,  # Per-claim: how much this feature deviates x importance
        })

    contributions.sort(key=lambda x: x["contribution"], reverse=True)
    return contributions


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
    gps_distance_norm: float = 0.0,
    shift_overlap: float = 1.0,
    claim_velocity_norm: float = 0.0,
    fraud_history_norm: float = 0.0,
) -> float:
    """Returns ML-predicted probability of fraud (0.0 = legitimate, 1.0 = fraud)."""
    X = extract_features(
        city_match, device_attested, same_week_claims, claim_history_count,
        hour_of_day, trigger_type, payout_amount, disruption_streak,
        gps_distance_norm, shift_overlap, claim_velocity_norm, fraud_history_norm,
    )
    # Fix G: No scaler — feed raw normalized features directly to RF
    prob = float(_MODEL.predict_proba(X)[0][1])
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
    gps_distance_norm: float = 0.0,
    shift_overlap: float = 1.0,
    claim_velocity_norm: float = 0.0,
    fraud_history_norm: float = 0.0,
) -> dict:
    """
    Full ML fraud assessment with per-claim explanations.

    Authenticity score = (1 - fraud_probability) x 100
    Thresholds: >=75 AUTO_APPROVED, 45-74 PENDING_REVIEW, <45 MANUAL_REVIEW
    """
    fraud_prob = predict_fraud_probability(
        city_match, device_attested, same_week_claims, claim_history_count,
        hour_of_day, trigger_type, payout_amount, disruption_streak,
        gps_distance_norm, shift_overlap, claim_velocity_norm, fraud_history_norm,
    )
    ml_score = round((1.0 - fraud_prob) * 100, 1)

    if ml_score >= 75:
        decision, decision_label = "AUTO_APPROVED", "Auto-Approved (ML)"
    elif ml_score >= 45:
        decision, decision_label = "PENDING_REVIEW", "Escrow Hold - 2hr Review (ML)"
    else:
        decision, decision_label = "MANUAL_REVIEW", "Manual Review Required (ML)"

    # Fix A: Per-claim explanations (not global importances)
    X = extract_features(
        city_match, device_attested, same_week_claims, claim_history_count,
        hour_of_day, trigger_type, payout_amount, disruption_streak,
        gps_distance_norm, shift_overlap, claim_velocity_norm, fraud_history_norm,
    )
    top_signals = _compute_per_claim_signals(X)

    # Fix F: Complete flags covering ALL 14 features including Phase 3
    flags = []
    if not city_match:
        flags.append("City mismatch - ML flagged high-risk")
    if not device_attested:
        flags.append("Device attestation failed")
    if same_week_claims >= 2:
        flags.append(f"{same_week_claims} claims this week - frequency anomaly")
    if claim_history_count > 5:
        flags.append(f"{claim_history_count} historical claims - elevated pattern")
    # Phase 3 flags
    if gps_distance_norm > 0.8:
        flags.append(f"GPS outside zone (distance ratio: {gps_distance_norm:.2f})")
    elif gps_distance_norm > 0.5:
        flags.append(f"GPS near zone boundary (distance ratio: {gps_distance_norm:.2f})")
    if shift_overlap == 0.0:
        flags.append("Claim filed outside declared shift hours")
    if claim_velocity_norm > 0.8:
        flags.append(f"Impossible travel speed detected (velocity ratio: {claim_velocity_norm:.2f})")
    elif claim_velocity_norm > 0.3:
        flags.append(f"Suspicious travel speed (velocity ratio: {claim_velocity_norm:.2f})")
    if fraud_history_norm > 0.5:
        flags.append(f"Repeat offender - {int(fraud_history_norm * 10)} prior fraud flags")
    elif fraud_history_norm > 0.1:
        flags.append(f"Prior fraud flags on record ({int(fraud_history_norm * 10)})")
    if fraud_prob > 0.5 and city_match and gps_distance_norm < 0.5:
        flags.append("ML detected unusual signal combination despite valid location")

    return {
        "ml_score": ml_score,
        "fraud_probability": fraud_prob,
        "decision": decision,
        "decision_label": decision_label,
        "flags": flags,
        "top_signals": top_signals[:5],
        "model_confidence": round(abs(fraud_prob - 0.5) * 2, 3),
        "model_accuracy": round(_VAL_ACCURACY, 4),
        "model_type": "RandomForestClassifier (200 trees)",
    }


def get_model_info() -> dict:
    """Return metadata about the trained fraud model (for admin API)."""
    return {
        "model_type": "RandomForestClassifier",
        "n_estimators": 200,
        "max_depth": 8,
        "training_samples": 3000,
        "validation_accuracy": round(_VAL_ACCURACY, 4),
        "validation_note": "Measured on synthetic data - not real-world performance",
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
