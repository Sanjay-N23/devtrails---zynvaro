"""
Tests for GET /policies/risk-profile endpoint.

Coverage (8 tests):
  1. Returns 200 with non-empty narrative string
  2. key_risks is a list (not dict)
  3. key_risks has exactly 2 items
  4. llm_powered is False (no ANTHROPIC_API_KEY in test env)
  5. weekly_premium > 0
  6. income_replacement > 0
  7. seasonal_alert is a string
  8. Works for worker without active policy (uses default tier)
"""

import sys
import os

# Ensure no ANTHROPIC_API_KEY leaks into test env
os.environ.pop("ANTHROPIC_API_KEY", None)

sys.path.insert(0, "D:/AeroFyta_DEVTrails-2026_FULL_HANDOFF/zynvaro-app/backend")

from models import PolicyTier, PolicyStatus


class TestRiskProfile:

    def test_returns_200_with_narrative(self, authed_client, make_policy):
        """Endpoint returns 200 and a non-empty narrative string."""
        make_policy(worker=authed_client.worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)
        resp = authed_client.get("/policies/risk-profile")
        assert resp.status_code == 200
        body = resp.json()
        assert "narrative" in body
        assert isinstance(body["narrative"], str)
        assert len(body["narrative"]) > 0

    def test_key_risks_is_list(self, authed_client, make_policy):
        """key_risks must be a list so the frontend can .map() over it."""
        make_policy(worker=authed_client.worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)
        resp = authed_client.get("/policies/risk-profile")
        body = resp.json()
        assert isinstance(body["key_risks"], list)

    def test_key_risks_has_exactly_2_items(self, authed_client, make_policy):
        """key_risks should contain exactly 2 items (primary + secondary risk)."""
        make_policy(worker=authed_client.worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)
        resp = authed_client.get("/policies/risk-profile")
        body = resp.json()
        assert len(body["key_risks"]) == 2

    def test_llm_powered_is_false(self, authed_client, make_policy):
        """Without ANTHROPIC_API_KEY, llm_powered must be False."""
        make_policy(worker=authed_client.worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)
        resp = authed_client.get("/policies/risk-profile")
        body = resp.json()
        assert body["llm_powered"] is False

    def test_weekly_premium_positive(self, authed_client, make_policy):
        """weekly_premium must be greater than zero."""
        make_policy(worker=authed_client.worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)
        resp = authed_client.get("/policies/risk-profile")
        body = resp.json()
        assert body["weekly_premium"] > 0

    def test_income_replacement_positive(self, authed_client, make_policy):
        """income_replacement percentage must be greater than zero."""
        make_policy(worker=authed_client.worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)
        resp = authed_client.get("/policies/risk-profile")
        body = resp.json()
        assert body["income_replacement"] > 0

    def test_seasonal_alert_is_string(self, authed_client, make_policy):
        """seasonal_alert must be a string (may be empty in off-season)."""
        make_policy(worker=authed_client.worker, tier=PolicyTier.BASIC, status=PolicyStatus.ACTIVE)
        resp = authed_client.get("/policies/risk-profile")
        body = resp.json()
        assert isinstance(body["seasonal_alert"], str)

    def test_works_without_active_policy_uses_default_tier(self, authed_client):
        """Worker with no active policy should still get a profile using default (Basic Shield) tier."""
        resp = authed_client.get("/policies/risk-profile")
        assert resp.status_code == 200
        body = resp.json()
        assert isinstance(body["narrative"], str)
        assert len(body["narrative"]) > 0
        assert body["weekly_premium"] > 0
