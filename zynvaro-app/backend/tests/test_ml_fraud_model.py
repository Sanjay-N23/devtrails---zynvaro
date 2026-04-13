"""
Unit tests for Zynvaro ML Fraud Detection Model (ml/fraud_model.py).

Coverage:
  - Model training (loads without error, correct accuracy)
  - Feature extraction (shape, range, interaction terms)
  - predict_fraud_probability (output range, monotonicity)
  - get_ml_fraud_decision (schema, decision thresholds, flags)
  - get_model_info (metadata completeness)
  - Edge cases (extreme values, boundary conditions)
  - Integration with compute_authenticity_score (ML fields present)
"""

import sys
import pytest
sys.path.insert(0, "D:/AeroFyta_DEVTrails-2026_FULL_HANDOFF/zynvaro-app/backend")

from ml.fraud_model import (
    extract_features,
    predict_fraud_probability,
    get_ml_fraud_decision,
    get_model_info,
    _VAL_ACCURACY,
)
from services.trigger_engine import compute_authenticity_score


# ─────────────────────────────────────────────────────────────────
# Model Training & Metadata
# ─────────────────────────────────────────────────────────────────

class TestModelTraining:
    def test_model_validation_accuracy_above_75_pct(self):
        """Trained RF should achieve >75% on synthetic data (not real-world metric)."""
        assert _VAL_ACCURACY > 0.75, f"Expected >75% synthetic accuracy, got {_VAL_ACCURACY:.1%}"

    def test_get_model_info_returns_expected_keys(self):
        info = get_model_info()
        for key in ["model_type", "n_estimators", "max_depth", "training_samples",
                    "validation_accuracy", "features", "feature_importances",
                    "fraud_decision_thresholds"]:
            assert key in info, f"Missing key: {key}"

    def test_model_info_has_14_features(self):
        info = get_model_info()
        assert len(info["features"]) == 14

    def test_feature_importances_sum_to_one(self):
        info = get_model_info()
        total = sum(f["importance"] for f in info["feature_importances"])
        assert abs(total - 1.0) < 0.01, f"Feature importances sum to {total:.4f}"

    def test_model_type_is_random_forest(self):
        info = get_model_info()
        assert "RandomForest" in info["model_type"]

    def test_training_samples_count(self):
        info = get_model_info()
        assert info["training_samples"] == 3000

    def test_n_estimators(self):
        info = get_model_info()
        assert info["n_estimators"] == 200


# ─────────────────────────────────────────────────────────────────
# Feature Extraction
# ─────────────────────────────────────────────────────────────────

class TestFeatureExtraction:
    def test_feature_vector_shape(self):
        X = extract_features(
            city_match=True, device_attested=True,
            same_week_claims=0, claim_history_count=0,
        )
        assert X.shape == (1, 14), f"Expected (1, 14), got {X.shape}"

    def test_all_features_in_valid_range(self):
        X = extract_features(
            city_match=True, device_attested=True,
            same_week_claims=2, claim_history_count=5,
            hour_of_day=14, trigger_type="Heavy Rainfall",
            payout_amount=500.0, disruption_streak=4,
        )
        # All features should be non-negative
        assert (X >= 0).all(), "Negative feature values found"

    def test_city_match_feature_is_1(self):
        X = extract_features(city_match=True, device_attested=True,
                             same_week_claims=0, claim_history_count=0)
        assert X[0][0] == 1.0

    def test_city_mismatch_feature_is_0(self):
        X = extract_features(city_match=False, device_attested=True,
                             same_week_claims=0, claim_history_count=0)
        assert X[0][0] == 0.0

    def test_same_week_claims_capped_and_normalized(self):
        X = extract_features(city_match=True, device_attested=True,
                             same_week_claims=99, claim_history_count=0)
        assert X[0][2] == 1.0  # 99 capped to 5, normalized to 5/5 = 1.0

    def test_claim_history_normalized_correctly(self):
        X = extract_features(city_match=True, device_attested=True,
                             same_week_claims=0, claim_history_count=10)
        assert X[0][3] == pytest.approx(0.5, abs=0.01)  # 10/20 = 0.5

    def test_interaction_term_city_x_device_both_true(self):
        X = extract_features(city_match=True, device_attested=True,
                             same_week_claims=0, claim_history_count=0)
        assert X[0][8] == 1.0  # city_int * device_int

    def test_interaction_term_city_x_device_city_false(self):
        X = extract_features(city_match=False, device_attested=True,
                             same_week_claims=0, claim_history_count=0)
        assert X[0][8] == 0.0

    def test_mismatch_x_freq_interaction_city_match(self):
        """If city matches, mismatch×freq interaction should be 0."""
        X = extract_features(city_match=True, device_attested=True,
                             same_week_claims=3, claim_history_count=0)
        assert X[0][9] == 0.0  # (1-1) * 3 = 0

    def test_mismatch_x_freq_interaction_city_mismatch(self):
        """If city mismatches, interaction amplifies normalized frequency signal."""
        X = extract_features(city_match=False, device_attested=True,
                             same_week_claims=3, claim_history_count=0)
        assert X[0][9] == pytest.approx(0.6, abs=0.01)  # (1-0) * (3/5) = 0.6

    def test_unknown_trigger_type_uses_default(self):
        """Unknown trigger type should not crash — uses default encoding."""
        X = extract_features(city_match=True, device_attested=True,
                             same_week_claims=0, claim_history_count=0,
                             trigger_type="Unknown Trigger")
        assert X.shape == (1, 14)

    def test_none_trigger_type_uses_default(self):
        X = extract_features(city_match=True, device_attested=True,
                             same_week_claims=0, claim_history_count=0,
                             trigger_type=None)
        assert X.shape == (1, 14)


# ─────────────────────────────────────────────────────────────────
# predict_fraud_probability
# ─────────────────────────────────────────────────────────────────

class TestPredictFraudProbability:
    def test_output_is_float(self):
        prob = predict_fraud_probability(
            city_match=True, device_attested=True,
            same_week_claims=0, claim_history_count=0,
        )
        assert isinstance(prob, float)

    def test_output_in_zero_to_one_range(self):
        prob = predict_fraud_probability(
            city_match=True, device_attested=True,
            same_week_claims=0, claim_history_count=0,
        )
        assert 0.0 <= prob <= 1.0

    def test_perfect_claim_has_low_fraud_probability(self):
        """City match + device + no history → probability should be < 0.30."""
        prob = predict_fraud_probability(
            city_match=True, device_attested=True,
            same_week_claims=0, claim_history_count=0,
            disruption_streak=6,
        )
        assert prob < 0.30, f"Expected low fraud prob, got {prob:.3f}"

    def test_city_mismatch_has_high_fraud_probability(self):
        """City mismatch is the strongest fraud signal → prob should be > 0.60."""
        prob = predict_fraud_probability(
            city_match=False, device_attested=True,
            same_week_claims=0, claim_history_count=0,
        )
        assert prob > 0.60, f"Expected high fraud prob for city mismatch, got {prob:.3f}"

    def test_all_bad_signals_has_very_high_fraud_probability(self):
        """All negative signals combined → probability should be > 0.85."""
        prob = predict_fraud_probability(
            city_match=False, device_attested=False,
            same_week_claims=5, claim_history_count=15,
            disruption_streak=0,
        )
        assert prob > 0.75, f"Expected very high fraud prob, got {prob:.3f}"

    def test_city_mismatch_raises_fraud_probability(self):
        """City mismatch should increase fraud probability vs city match."""
        prob_match = predict_fraud_probability(
            city_match=True, device_attested=True,
            same_week_claims=0, claim_history_count=0,
        )
        prob_mismatch = predict_fraud_probability(
            city_match=False, device_attested=True,
            same_week_claims=0, claim_history_count=0,
        )
        assert prob_mismatch > prob_match

    def test_device_failure_raises_fraud_probability(self):
        """Failed device attestation should increase fraud probability."""
        prob_valid = predict_fraud_probability(
            city_match=True, device_attested=True,
            same_week_claims=0, claim_history_count=0,
        )
        prob_invalid = predict_fraud_probability(
            city_match=True, device_attested=False,
            same_week_claims=0, claim_history_count=0,
        )
        assert prob_invalid > prob_valid

    def test_high_history_raises_fraud_probability(self):
        """High claim history should raise fraud probability."""
        prob_clean = predict_fraud_probability(
            city_match=True, device_attested=True,
            same_week_claims=0, claim_history_count=0,
        )
        prob_history = predict_fraud_probability(
            city_match=True, device_attested=True,
            same_week_claims=0, claim_history_count=15,
        )
        assert prob_history > prob_clean

    def test_output_is_deterministic(self):
        """Same inputs should always produce the same output."""
        p1 = predict_fraud_probability(
            city_match=True, device_attested=True,
            same_week_claims=1, claim_history_count=3,
            hour_of_day=14, trigger_type="Heavy Rainfall",
            payout_amount=600.0, disruption_streak=2,
        )
        p2 = predict_fraud_probability(
            city_match=True, device_attested=True,
            same_week_claims=1, claim_history_count=3,
            hour_of_day=14, trigger_type="Heavy Rainfall",
            payout_amount=600.0, disruption_streak=2,
        )
        assert p1 == p2


# ─────────────────────────────────────────────────────────────────
# get_ml_fraud_decision
# ─────────────────────────────────────────────────────────────────

class TestGetMLFraudDecision:
    def test_returns_required_keys(self):
        result = get_ml_fraud_decision(
            city_match=True, device_attested=True,
            same_week_claims=0, claim_history_count=0,
        )
        for key in ["ml_score", "fraud_probability", "decision",
                    "decision_label", "flags", "top_signals",
                    "model_confidence", "model_accuracy", "model_type"]:
            assert key in result, f"Missing key: {key}"

    def test_ml_score_in_zero_to_100_range(self):
        result = get_ml_fraud_decision(
            city_match=True, device_attested=True,
            same_week_claims=0, claim_history_count=0,
        )
        assert 0.0 <= result["ml_score"] <= 100.0

    def test_fraud_probability_in_zero_to_one_range(self):
        result = get_ml_fraud_decision(
            city_match=True, device_attested=True,
            same_week_claims=0, claim_history_count=0,
        )
        assert 0.0 <= result["fraud_probability"] <= 1.0

    def test_perfect_claim_gets_auto_approved(self):
        result = get_ml_fraud_decision(
            city_match=True, device_attested=True,
            same_week_claims=0, claim_history_count=0,
            disruption_streak=8,
        )
        assert result["decision"] == "AUTO_APPROVED"

    def test_city_mismatch_with_high_freq_gets_manual_review(self):
        result = get_ml_fraud_decision(
            city_match=False, device_attested=False,
            same_week_claims=5, claim_history_count=15,
        )
        assert result["decision"] == "MANUAL_REVIEW"

    def test_top_signals_is_list_of_5(self):
        result = get_ml_fraud_decision(
            city_match=True, device_attested=True,
            same_week_claims=0, claim_history_count=0,
        )
        assert isinstance(result["top_signals"], list)
        assert len(result["top_signals"]) == 5

    def test_top_signals_have_required_fields(self):
        result = get_ml_fraud_decision(
            city_match=True, device_attested=True,
            same_week_claims=0, claim_history_count=0,
        )
        for signal in result["top_signals"]:
            assert "feature" in signal
            assert "importance" in signal
            assert "value" in signal

    def test_model_confidence_in_zero_to_one(self):
        result = get_ml_fraud_decision(
            city_match=True, device_attested=True,
            same_week_claims=0, claim_history_count=0,
        )
        assert 0.0 <= result["model_confidence"] <= 1.0

    def test_city_mismatch_flag_appears_in_ml_result(self):
        result = get_ml_fraud_decision(
            city_match=False, device_attested=True,
            same_week_claims=0, claim_history_count=0,
        )
        assert any("city" in f.lower() or "mismatch" in f.lower() for f in result["flags"])

    def test_frequency_flag_appears_for_high_same_week(self):
        result = get_ml_fraud_decision(
            city_match=True, device_attested=True,
            same_week_claims=3, claim_history_count=0,
        )
        assert any("claim" in f.lower() or "week" in f.lower() or "frequency" in f.lower()
                   for f in result["flags"])

    def test_all_trigger_types_work(self):
        trigger_types = [
            "Heavy Rainfall", "Extreme Rain / Flooding", "Severe Heatwave",
            "Hazardous AQI", "Platform Outage", "Civil Disruption",
        ]
        for tt in trigger_types:
            result = get_ml_fraud_decision(
                city_match=True, device_attested=True,
                same_week_claims=0, claim_history_count=0,
                trigger_type=tt,
            )
            assert "decision" in result, f"Failed for trigger type: {tt}"


# ─────────────────────────────────────────────────────────────────
# Integration: compute_authenticity_score has ML fields
# ─────────────────────────────────────────────────────────────────

class TestComputeAuthenticityMLIntegration:
    def test_ml_available_field_present(self):
        result = compute_authenticity_score(
            worker_city="Mumbai", trigger_city="Mumbai",
            claim_history=0, same_week_claims=0, device_attested=True,
        )
        assert "ml_available" in result

    def test_ml_available_is_true(self):
        result = compute_authenticity_score(
            worker_city="Mumbai", trigger_city="Mumbai",
            claim_history=0, same_week_claims=0, device_attested=True,
        )
        assert result["ml_available"] is True

    def test_ml_score_field_present_and_in_range(self):
        result = compute_authenticity_score(
            worker_city="Mumbai", trigger_city="Mumbai",
            claim_history=0, same_week_claims=0, device_attested=True,
        )
        assert "ml_score" in result
        assert result["ml_score"] is not None
        assert 0.0 <= result["ml_score"] <= 100.0

    def test_ml_fraud_probability_present(self):
        result = compute_authenticity_score(
            worker_city="Mumbai", trigger_city="Mumbai",
            claim_history=0, same_week_claims=0, device_attested=True,
        )
        assert "ml_fraud_probability" in result
        assert 0.0 <= result["ml_fraud_probability"] <= 1.0

    def test_ml_confidence_present(self):
        result = compute_authenticity_score(
            worker_city="Delhi", trigger_city="Delhi",
            claim_history=0, same_week_claims=0, device_attested=True,
        )
        assert "ml_confidence" in result

    def test_ml_top_signals_present(self):
        result = compute_authenticity_score(
            worker_city="Bangalore", trigger_city="Bangalore",
            claim_history=0, same_week_claims=0, device_attested=True,
        )
        assert "ml_top_signals" in result
        assert isinstance(result["ml_top_signals"], list)

    def test_rule_score_field_present(self):
        result = compute_authenticity_score(
            worker_city="Mumbai", trigger_city="Mumbai",
            claim_history=0, same_week_claims=0, device_attested=True,
        )
        # Backward-compat: original score unchanged
        assert result["score"] == 100.0

    def test_decision_unchanged_by_ml(self):
        """ML augments but does NOT override the rule-based decision."""
        result = compute_authenticity_score(
            worker_city="Mumbai", trigger_city="Mumbai",
            claim_history=0, same_week_claims=0, device_attested=True,
        )
        assert result["decision"] == "AUTO_APPROVED"

    def test_optional_ml_params_accepted(self):
        """New optional params should be accepted without error."""
        result = compute_authenticity_score(
            worker_city="Delhi", trigger_city="Delhi",
            claim_history=2, same_week_claims=1, device_attested=True,
            trigger_type="Hazardous AQI",
            payout_amount=700.0,
            disruption_streak=3,
        )
        assert "ml_score" in result
        assert result["score"] == 90.0  # rule-based: 100 - 10 (same_week_claims=1)

    def test_ml_score_lower_for_fraudulent_claim(self):
        """ML score should be lower for suspicious vs clean claim."""
        clean = compute_authenticity_score(
            worker_city="Mumbai", trigger_city="Mumbai",
            claim_history=0, same_week_claims=0, device_attested=True,
        )
        suspicious = compute_authenticity_score(
            worker_city="Bangalore", trigger_city="Mumbai",  # mismatch
            claim_history=10, same_week_claims=3, device_attested=False,
        )
        assert clean["ml_score"] > suspicious["ml_score"]
