"""
Unit tests for compute_authenticity_score in services/trigger_engine.py.

Coverage:
  - Happy path (perfect to partial signals)
  - Boundary values at score thresholds 75 and 45
  - Failure / fraud signal combinations
  - Edge cases for clamping and threshold logic
  - Response shape and field correctness
  - Real seed-data fraud scenarios (Ravi, Arjun, Priya)
"""

import sys

sys.path.insert(0, "D:/AeroFyta_DEVTrails-2026_FULL_HANDOFF/zynvaro-app/backend")

from services.trigger_engine import compute_authenticity_score


# ---------------------------------------------------------------------------
# Happy Path
# ---------------------------------------------------------------------------


def test_perfect_claim_score_is_100():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["score"] == 100.0


def test_perfect_claim_decision_is_auto_approved():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["decision"] == "AUTO_APPROVED"


def test_perfect_claim_has_no_flags():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["flags"] == []


def test_city_match_one_same_week_claim_score_is_90():
    result = compute_authenticity_score(
        worker_city="Delhi",
        trigger_city="Delhi",
        claim_history=0,
        same_week_claims=1,
        device_attested=True,
    )
    assert result["score"] == 90.0


def test_city_match_one_same_week_claim_is_still_auto_approved():
    result = compute_authenticity_score(
        worker_city="Delhi",
        trigger_city="Delhi",
        claim_history=0,
        same_week_claims=1,
        device_attested=True,
    )
    assert result["decision"] == "AUTO_APPROVED"


def test_city_match_two_same_week_claims_score_is_80():
    result = compute_authenticity_score(
        worker_city="Pune",
        trigger_city="Pune",
        claim_history=0,
        same_week_claims=2,
        device_attested=True,
    )
    assert result["score"] == 80.0


def test_city_match_two_same_week_claims_is_auto_approved():
    result = compute_authenticity_score(
        worker_city="Pune",
        trigger_city="Pune",
        claim_history=0,
        same_week_claims=2,
        device_attested=True,
    )
    assert result["decision"] == "AUTO_APPROVED"


def test_city_match_three_or_more_same_week_claims_score_capped_at_80():
    # same_week_claims=3 → min(20, 30) = 20 deducted; score stays 80
    result = compute_authenticity_score(
        worker_city="Chennai",
        trigger_city="Chennai",
        claim_history=0,
        same_week_claims=3,
        device_attested=True,
    )
    assert result["score"] == 80.0


def test_city_match_three_same_week_claims_is_auto_approved():
    result = compute_authenticity_score(
        worker_city="Chennai",
        trigger_city="Chennai",
        claim_history=0,
        same_week_claims=3,
        device_attested=True,
    )
    assert result["decision"] == "AUTO_APPROVED"


# ---------------------------------------------------------------------------
# Boundary Cases — threshold 75
# ---------------------------------------------------------------------------


def test_score_exactly_75_is_auto_approved():
    # city match (0) + device fail (−20) + no same_week + history <= 5 = 80
    # Need score exactly 75: city match + device fail (−20) + same_week=0 + high history (−10) = 70 — not 75.
    # city mismatch (−40) + device ok + no same_week + no high history = 60 — not 75.
    # Achievable path: city match + device ok + same_week=2 (−20) + no high history = 80.
    # Exact 75 cannot be produced by integer signal deductions from 100 with the current signal set
    # (signals deduct in multiples of 10). The function rounds to 1 decimal but inputs are integers,
    # so 75 is reachable only if score lands exactly there after rounding.
    # city match + device ok + same_week=2 (−20) + history=6 (−10) = 70 → PENDING
    # city match + device fail (−20) + same_week=0 + no high history = 80 → AUTO
    # The smallest AUTO_APPROVED score reachable is 80. 75 is not reachable with these inputs;
    # test the boundary by confirming >= 75 rule via score=80 (AUTO) vs score=70 (PENDING).
    result_80 = compute_authenticity_score(
        worker_city="Kolkata",
        trigger_city="Kolkata",
        claim_history=0,
        same_week_claims=2,
        device_attested=True,
    )
    assert result_80["score"] >= 75
    assert result_80["decision"] == "AUTO_APPROVED"


def test_score_just_below_75_is_pending_review():
    # city match + device fail (−20) + same_week=2 (−20) + history <= 5 = 60 → PENDING
    # city match + device ok + same_week=2 (−20) + high history=6 (−10) = 70 → PENDING
    result = compute_authenticity_score(
        worker_city="Hyderabad",
        trigger_city="Hyderabad",
        claim_history=6,
        same_week_claims=2,
        device_attested=True,
    )
    assert result["score"] == 70.0
    assert result["decision"] == "PENDING_REVIEW"


def test_device_fail_only_produces_80_which_is_auto_approved():
    # 100 − 20 = 80, still >= 75
    result = compute_authenticity_score(
        worker_city="Jaipur",
        trigger_city="Jaipur",
        claim_history=0,
        same_week_claims=0,
        device_attested=False,
    )
    assert result["score"] == 80.0
    assert result["decision"] == "AUTO_APPROVED"


# ---------------------------------------------------------------------------
# Boundary Cases — threshold 45
# ---------------------------------------------------------------------------


def test_score_exactly_45_is_pending_review():
    # Reachable path: city mismatch (−40) + device ok + same_week=1 (−10) + no high history = 50 → PENDING
    # Another: city mismatch (−40) + device ok + no same_week + high history (−10) = 50 → PENDING
    # Exact 45: city mismatch (−40) + device ok + same_week=1 (−10) + high history (−10) = 40 → MANUAL
    # 45 is not directly reachable; smallest gap above 45 with these signals is 50.
    # Test the boundary concept: 50 (city mismatch + 1 same_week) is PENDING, not MANUAL.
    result = compute_authenticity_score(
        worker_city="Nagpur",
        trigger_city="Mumbai",
        claim_history=0,
        same_week_claims=1,
        device_attested=True,
    )
    assert result["score"] == 50.0
    assert result["decision"] == "PENDING_REVIEW"


def test_score_just_below_45_is_manual_review():
    # city mismatch (−40) + same_week=1 (−10) + high history (−10) = 40 → MANUAL_REVIEW
    result = compute_authenticity_score(
        worker_city="Nagpur",
        trigger_city="Mumbai",
        claim_history=6,
        same_week_claims=1,
        device_attested=True,
    )
    assert result["score"] == 40.0
    assert result["decision"] == "MANUAL_REVIEW"


def test_score_40_is_manual_review():
    # city mismatch (−40) + device fail (−20) = 40 → MANUAL_REVIEW
    result = compute_authenticity_score(
        worker_city="Surat",
        trigger_city="Ahmedabad",
        claim_history=0,
        same_week_claims=0,
        device_attested=False,
    )
    assert result["score"] == 40.0
    assert result["decision"] == "MANUAL_REVIEW"


def test_score_60_from_city_mismatch_only_is_pending_review():
    # city mismatch (−40) only = 60 → PENDING_REVIEW
    result = compute_authenticity_score(
        worker_city="Indore",
        trigger_city="Bhopal",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["score"] == 60.0
    assert result["decision"] == "PENDING_REVIEW"


# ---------------------------------------------------------------------------
# Failure Cases
# ---------------------------------------------------------------------------


def test_city_mismatch_only_score_is_60():
    result = compute_authenticity_score(
        worker_city="Lucknow",
        trigger_city="Kanpur",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["score"] == 60.0


def test_city_mismatch_only_decision_is_pending_review():
    result = compute_authenticity_score(
        worker_city="Lucknow",
        trigger_city="Kanpur",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["decision"] == "PENDING_REVIEW"


def test_city_mismatch_plus_device_fail_score_is_40():
    # 100 − 40 − 20 = 40
    result = compute_authenticity_score(
        worker_city="Patna",
        trigger_city="Ranchi",
        claim_history=0,
        same_week_claims=0,
        device_attested=False,
    )
    assert result["score"] == 40.0


def test_city_mismatch_plus_device_fail_decision_is_manual_review():
    result = compute_authenticity_score(
        worker_city="Patna",
        trigger_city="Ranchi",
        claim_history=0,
        same_week_claims=0,
        device_attested=False,
    )
    assert result["decision"] == "MANUAL_REVIEW"


def test_city_mismatch_device_fail_same_week_2_score_is_20():
    # 100 − 40 − 20 − 20 = 20
    result = compute_authenticity_score(
        worker_city="Agra",
        trigger_city="Mathura",
        claim_history=0,
        same_week_claims=2,
        device_attested=False,
    )
    assert result["score"] == 20.0


def test_city_mismatch_device_fail_same_week_2_decision_is_manual_review():
    result = compute_authenticity_score(
        worker_city="Agra",
        trigger_city="Mathura",
        claim_history=0,
        same_week_claims=2,
        device_attested=False,
    )
    assert result["decision"] == "MANUAL_REVIEW"


def test_all_signals_fail_score_is_never_negative():
    # 100 − 40 − 20 − 20 − 10 = 10, well above 0
    result_all_bad = compute_authenticity_score(
        worker_city="CityA",
        trigger_city="CityB",
        claim_history=100,
        same_week_claims=100,
        device_attested=False,
    )
    assert result_all_bad["score"] >= 0


def test_all_signals_fail_score_floor_is_zero():
    # Theoretical maximum deduction: 40 + 20 + 20 + 10 = 90 → floor is 10 with these signals.
    # max(0, ...) ensures no negative score regardless of input.
    result = compute_authenticity_score(
        worker_city="CityA",
        trigger_city="CityB",
        claim_history=9999,
        same_week_claims=9999,
        device_attested=False,
    )
    assert result["score"] >= 0


# ---------------------------------------------------------------------------
# Edge Cases
# ---------------------------------------------------------------------------


def test_city_comparison_is_case_insensitive_lowercase():
    result = compute_authenticity_score(
        worker_city="bangalore",
        trigger_city="Bangalore",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["score"] == 100.0


def test_city_comparison_is_case_insensitive_all_upper():
    result = compute_authenticity_score(
        worker_city="BANGALORE",
        trigger_city="bangalore",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["gps_valid"] is True


def test_city_comparison_case_insensitive_no_location_flag():
    result = compute_authenticity_score(
        worker_city="bangalore",
        trigger_city="BANGALORE",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    location_flags = [f for f in result["flags"] if "city" in f.lower()]
    assert location_flags == []


def test_same_week_claims_zero_no_flag():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    week_flags = [f for f in result["flags"] if "claim(s) this week" in f]
    assert week_flags == []


def test_same_week_claims_zero_no_deduction():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["score"] == 100.0


def test_same_week_claims_3_deducts_exactly_20_not_30():
    # min(20, 3 * 10) = min(20, 30) = 20
    result = compute_authenticity_score(
        worker_city="Delhi",
        trigger_city="Delhi",
        claim_history=0,
        same_week_claims=3,
        device_attested=True,
    )
    assert result["score"] == 80.0


def test_claim_history_5_causes_no_deduction():
    # Threshold is > 5, so exactly 5 should NOT deduct
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        claim_history=5,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["score"] == 100.0


def test_claim_history_5_has_no_high_history_flag():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        claim_history=5,
        same_week_claims=0,
        device_attested=True,
    )
    history_flags = [f for f in result["flags"] if "claim history" in f.lower()]
    assert history_flags == []


def test_claim_history_6_deducts_exactly_10():
    # Exactly at boundary: > 5 is True for 6 → deduct 10
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        claim_history=6,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["score"] == 90.0


def test_claim_history_100_deducts_only_10():
    # No additional deduction beyond the single −10 for high history
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        claim_history=100,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["score"] == 90.0


def test_claim_history_100_same_deduction_as_history_6():
    result_100 = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        claim_history=100,
        same_week_claims=0,
        device_attested=True,
    )
    result_6 = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        claim_history=6,
        same_week_claims=0,
        device_attested=True,
    )
    assert result_100["score"] == result_6["score"]


# ---------------------------------------------------------------------------
# Response Shape Tests
# ---------------------------------------------------------------------------


def test_return_value_is_dict():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
    )
    assert isinstance(result, dict)


def test_return_value_has_all_required_keys():
    required_keys = {
        "score",
        "decision",
        "decision_label",
        "flags",
        "gps_valid",
        "activity_valid",
        "device_valid",
        "cross_source_valid",
    }
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
    )
    assert required_keys.issubset(result.keys())


def test_cross_source_valid_is_always_true():
    for city_pair, history, week, attested in [
        ("Mumbai", 0, 0, True),
        ("Other", 10, 3, False),
    ]:
        result = compute_authenticity_score(
            worker_city=city_pair,
            trigger_city="Mumbai",
            claim_history=history,
            same_week_claims=week,
            device_attested=attested,
        )
        assert result["cross_source_valid"] is True, (
            f"cross_source_valid should be True but got False for worker_city={city_pair}"
        )


def test_gps_valid_true_when_cities_match():
    result = compute_authenticity_score(
        worker_city="Chennai",
        trigger_city="Chennai",
    )
    assert result["gps_valid"] is True


def test_gps_valid_false_when_cities_differ():
    result = compute_authenticity_score(
        worker_city="Chennai",
        trigger_city="Coimbatore",
    )
    assert result["gps_valid"] is False


def test_gps_valid_true_case_insensitive():
    result = compute_authenticity_score(
        worker_city="chennai",
        trigger_city="CHENNAI",
    )
    assert result["gps_valid"] is True


def test_activity_valid_true_when_no_same_week_claims():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        same_week_claims=0,
    )
    assert result["activity_valid"] is True


def test_activity_valid_false_when_same_week_claims_nonzero():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        same_week_claims=1,
    )
    assert result["activity_valid"] is False


def test_device_valid_true_when_attested():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        device_attested=True,
    )
    assert result["device_valid"] is True


def test_device_valid_false_when_not_attested():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        device_attested=False,
    )
    assert result["device_valid"] is False


def test_flags_is_a_list():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
    )
    assert isinstance(result["flags"], list)


def test_flags_is_empty_list_for_perfect_claim():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["flags"] == []


def test_score_is_within_0_to_100_range_for_normal_inputs():
    result = compute_authenticity_score(
        worker_city="CityA",
        trigger_city="CityB",
        claim_history=50,
        same_week_claims=5,
        device_attested=False,
    )
    assert 0 <= result["score"] <= 100


def test_score_is_within_0_to_100_range_for_perfect_inputs():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    assert 0 <= result["score"] <= 100


def test_decision_label_matches_auto_approved():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["decision"] == "AUTO_APPROVED"
    assert "Auto-Approved" in result["decision_label"]


def test_decision_label_matches_pending_review():
    # city mismatch only → 60 → PENDING_REVIEW
    result = compute_authenticity_score(
        worker_city="Delhi",
        trigger_city="Mumbai",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["decision"] == "PENDING_REVIEW"
    assert "Escrow" in result["decision_label"] or "review" in result["decision_label"].lower()


def test_decision_label_matches_manual_review():
    # city mismatch + device fail → 40 → MANUAL_REVIEW
    result = compute_authenticity_score(
        worker_city="Delhi",
        trigger_city="Mumbai",
        claim_history=0,
        same_week_claims=0,
        device_attested=False,
    )
    assert result["decision"] == "MANUAL_REVIEW"
    assert "Manual" in result["decision_label"] or "manual" in result["decision_label"].lower()


# ---------------------------------------------------------------------------
# Flags Content Tests
# ---------------------------------------------------------------------------


def test_city_mismatch_produces_location_flag():
    result = compute_authenticity_score(
        worker_city="Delhi",
        trigger_city="Mumbai",
    )
    assert any("city" in f.lower() for f in result["flags"])


def test_device_fail_produces_attestation_flag():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        device_attested=False,
    )
    assert any("attestation" in f.lower() for f in result["flags"])


def test_same_week_claims_produces_frequency_flag():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        same_week_claims=2,
    )
    assert any("claim(s) this week" in f for f in result["flags"])


def test_high_claim_history_produces_history_flag():
    result = compute_authenticity_score(
        worker_city="Mumbai",
        trigger_city="Mumbai",
        claim_history=10,
    )
    assert any("claim history" in f.lower() for f in result["flags"])


def test_multiple_failing_signals_produce_multiple_flags():
    result = compute_authenticity_score(
        worker_city="Delhi",
        trigger_city="Mumbai",
        claim_history=10,
        same_week_claims=2,
        device_attested=False,
    )
    assert len(result["flags"]) == 4


# ---------------------------------------------------------------------------
# Security / Fraud Scenarios (seed-data cases)
# ---------------------------------------------------------------------------


def test_ravi_fraud_scenario_score():
    # Ravi: city mismatch + same_week=2, no device issue, low history
    # 100 − 40 − 20 = 40
    result = compute_authenticity_score(
        worker_city="Delhi",
        trigger_city="Mumbai",
        claim_history=0,
        same_week_claims=2,
        device_attested=True,
    )
    assert result["score"] == 40.0


def test_ravi_fraud_scenario_decision():
    result = compute_authenticity_score(
        worker_city="Delhi",
        trigger_city="Mumbai",
        claim_history=0,
        same_week_claims=2,
        device_attested=True,
    )
    assert result["decision"] == "MANUAL_REVIEW"


def test_arjun_review_scenario_score():
    # Arjun: city match + same_week=3 (capped at −20) + high history=7 (−10)
    # 100 − 20 − 10 = 70
    result = compute_authenticity_score(
        worker_city="Bangalore",
        trigger_city="Bangalore",
        claim_history=7,
        same_week_claims=3,
        device_attested=True,
    )
    assert result["score"] == 70.0


def test_arjun_review_scenario_decision():
    result = compute_authenticity_score(
        worker_city="Bangalore",
        trigger_city="Bangalore",
        claim_history=7,
        same_week_claims=3,
        device_attested=True,
    )
    assert result["decision"] == "PENDING_REVIEW"


def test_priya_clean_scenario_score():
    # Priya: all clean — city match, no same_week, history <= 5
    result = compute_authenticity_score(
        worker_city="Chennai",
        trigger_city="Chennai",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["score"] == 100.0


def test_priya_clean_scenario_decision():
    result = compute_authenticity_score(
        worker_city="Chennai",
        trigger_city="Chennai",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["decision"] == "AUTO_APPROVED"


def test_priya_clean_scenario_all_valid_fields():
    result = compute_authenticity_score(
        worker_city="Chennai",
        trigger_city="Chennai",
        claim_history=0,
        same_week_claims=0,
        device_attested=True,
    )
    assert result["gps_valid"] is True
    assert result["activity_valid"] is True
    assert result["device_valid"] is True
    assert result["cross_source_valid"] is True
