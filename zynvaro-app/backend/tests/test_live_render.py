"""
LIVE RENDER DEPLOYMENT SMOKE TEST

This module is intentionally opt-in under pytest because it makes real network
requests against the deployed Render app. Normal local/unit test runs should
collect this file without executing any HTTP calls.

Pytest:
    RUN_LIVE_RENDER_TESTS=1 python -m pytest tests/test_live_render.py -s

Manual script run:
    python tests/test_live_render.py
"""

from __future__ import annotations

import json
import os
import random
import sys
import time

import httpx
import pytest


BASE = os.getenv("LIVE_RENDER_BASE_URL", "https://devtrails-zynvaro.onrender.com")
RUN_LIVE_RENDER_TESTS = os.getenv("RUN_LIVE_RENDER_TESTS") == "1"


def _run_live_render_suite(base_url: str = BASE) -> dict:
    """Run the live deployment smoke test and return pass/fail counts."""
    passed = 0
    failed = 0

    def chk(name, cond, detail=""):
        nonlocal passed, failed
        if cond:
            passed += 1
            print(f"  PASS  {name}")
        else:
            failed += 1
            print(f"  FAIL  {name} -- {detail}")

    client = httpx.Client(base_url=base_url, timeout=30)

    try:
        print("=" * 60)
        print(f"  LIVE RENDER TEST: {base_url}")
        print("=" * 60)
        print()

        # 1. HEALTH
        print("--- 1. Health & Infrastructure ---")
        r = client.get("/health")
        chk("1.1 Health 200", r.status_code == 200, f"status={r.status_code}")
        h = r.json()
        chk("1.2 Version 3.0.0", h.get("version") == "3.0.0", f"v={h.get('version')}")
        chk("1.3 Phase 3 SOAR", "Phase 3" in h.get("phase", ""), f"phase={h.get('phase')}")
        r = client.get("/app")
        chk("1.4 PWA serves HTML", r.status_code == 200 and "Zynvaro" in r.text, f"status={r.status_code}")

        print()

        # 2. AUTH
        print("--- 2. Authentication ---")
        phone = f"91{random.randint(10000000, 99999999)}"
        r = client.post(
            "/auth/register",
            json={
                "full_name": "LiveTest",
                "phone": phone,
                "password": "test1234",
                "city": "Mumbai",
                "pincode": "400051",
                "platform": "Blinkit",
                "vehicle_type": "2-Wheeler",
                "shift": "Evening Peak (6PM-2AM)",
            },
        )
        chk("2.1 Register", r.status_code == 201, f"status={r.status_code} body={r.text[:80]}")
        token = r.json().get("access_token", "")
        auth = {"Authorization": f"Bearer {token}"}

        r = client.get("/auth/me", headers=auth)
        me = r.json()
        chk("2.2 Profile", r.status_code == 200, "")
        chk("2.3 GPS home_lat", me.get("home_lat") is not None, f"lat={me.get('home_lat')}")
        chk("2.4 Zone risk", me.get("zone_risk_score", 0) > 0, f"risk={me.get('zone_risk_score')}")

        r = client.post("/auth/me/location", json={"lat": 19.076, "lng": 72.878}, headers=auth)
        chk("2.5 Location update", r.status_code == 200, f"status={r.status_code}")

        r = client.get("/auth/me", headers={"Authorization": "Bearer fake"})
        chk("2.6 Bad token = 401", r.status_code == 401, f"status={r.status_code}")

        print()

        # 3. POLICIES
        print("--- 3. Policy Management ---")
        r = client.get("/policies/quote/all", headers=auth)
        chk("3.1 Quotes", r.status_code == 200, f"status={r.status_code}")
        tiers = r.json().get("tiers", [])
        chk("3.2 Three tiers", len(tiers) == 3, f"count={len(tiers)}")

        r = client.post("/policies/", json={"tier": "Basic Shield"}, headers=auth)
        chk("3.3 Activate policy", r.status_code == 201, f"status={r.status_code}")

        r = client.get("/policies/active", headers=auth)
        pol = r.json()
        chk("3.4 Policy active", pol.get("status") == "active", f"status={pol.get('status')}")

        r = client.get("/policies/risk-profile", headers=auth)
        chk("3.5 Risk profile", r.status_code == 200, f"status={r.status_code}")

        r = client.get("/policies/ml-model-info", headers=auth)
        ml = r.json()
        chk("3.6 ML model 14 features", len(ml.get("features", [])) == 14, f"feat={len(ml.get('features', []))}")
        chk("3.7 ML 3000 samples", ml.get("training_samples") == 3000, f"n={ml.get('training_samples')}")

        print()

        # 4. TRIGGERS
        print("--- 4. Trigger System ---")
        r = client.post("/triggers/simulate", json={"trigger_type": "Heavy Rainfall", "city": "Mumbai"}, headers=auth)
        chk("4.1 Simulate Heavy Rainfall Mumbai", r.status_code == 201, f"status={r.status_code}")
        td = r.json()
        chk("4.2 trigger_event_id", td.get("trigger_event_id") is not None, "")
        chk("4.3 No [DEMO] in description", "[DEMO]" not in json.dumps(td), f"data={json.dumps(td)[:100]}")

        r = client.post("/triggers/simulate", json={"trigger_type": "Alien", "city": "Mumbai"}, headers=auth)
        chk("4.4 Invalid trigger = 400", r.status_code == 400, f"status={r.status_code}")

        r = client.post("/triggers/simulate", json={"trigger_type": "Heavy Rainfall", "city": "Mars"}, headers=auth)
        chk("4.5 Invalid city = 400", r.status_code == 400, f"status={r.status_code}")

        time.sleep(4)

        print()

        # 5. CLAIMS + FRAUD DETECTION
        print("--- 5. Claims & Advanced Fraud Detection ---")
        r = client.get("/claims/", headers=auth)
        claims = r.json()
        chk("5.1 Claims list", r.status_code == 200, f"status={r.status_code}")
        chk("5.2 Claim created", len(claims) >= 1, f"count={len(claims)}")

        if claims:
            c = claims[0]
            chk("5.3 Status valid", c["status"] in ("paid", "pending_review", "manual_review"), f"s={c['status']}")
            chk("5.4 Payout > 0", c["payout_amount"] > 0, f"amt={c['payout_amount']}")
            chk("5.5 Auth score 0-100", 0 <= c["authenticity_score"] <= 100, f"score={c['authenticity_score']}")
            chk("5.6 gps_valid field", "gps_valid" in c, "")
            chk("5.7 shift_valid field", "shift_valid" in c, "")
            chk("5.8 weather_cross_valid", "weather_cross_valid" in c, "")
            chk("5.9 velocity_valid", "velocity_valid" in c, "")
            chk(
                "5.10 risk_tier",
                c.get("risk_tier") in ("LOW", "MEDIUM", "HIGH", "CRITICAL", None),
                f"t={c.get('risk_tier')}",
            )
            chk("5.11 gps_distance_km", "gps_distance_km" in c, "")
            chk("5.12 ml_fraud_probability", "ml_fraud_probability" in c, "")
            chk("5.13 payout_gateway", c.get("payout_gateway") in ("razorpay", "mock", None), f"gw={c.get('payout_gateway')}")

            gw = c.get("payout_gateway")
            ref = c.get("payment_ref", "")
            if gw == "razorpay":
                chk("5.14 Razorpay ref RZP-", ref.startswith("RZP-"), f"ref={ref}")
                print(f"         RAZORPAY LIVE: {ref}")
            elif c["status"] == "paid":
                chk("5.14 Mock ref MOCK-UPI", "MOCK" in ref, f"ref={ref}")
                print(f"         MOCK PAYOUT: {ref}")
            else:
                chk("5.14 Not paid yet (pending/manual)", True, "")

        print()

        # 6. WORKER DASHBOARD
        print("--- 6. Worker Dashboard (Earnings Protected) ---")
        r = client.get("/claims/my-weekly-summary", headers=auth)
        ws = r.json()
        chk("6.1 Summary 200", r.status_code == 200, "")
        chk("6.2 active_coverage = true", ws["active_coverage"] is True, f"v={ws['active_coverage']}")
        chk("6.3 max_weekly > 0", ws["max_weekly_payout"] > 0, f"max={ws['max_weekly_payout']}")
        chk("6.4 coverage_remaining >= 0", ws["coverage_remaining_this_week"] >= 0, f"rem={ws['coverage_remaining_this_week']}")
        chk("6.5 earnings_total >= 0", ws["earnings_protected_total"] >= 0, "")
        chk("6.6 claims_this_week >= 0", ws["claims_this_week"] >= 0, "")

        print()

        # 7. ADMIN
        print("--- 7. Admin Dashboard ---")
        r = client.get("/claims/admin/stats", headers=auth)
        chk("7.1 Admin stats", r.status_code == 200, f"status={r.status_code}")
        st = r.json()
        chk("7.2 Workers > 0", st.get("total_workers", 0) > 0, f"w={st.get('total_workers')}")
        chk("7.3 Loss ratio", "loss_ratio_pct" in st, "")
        chk("7.4 Trigger breakdown", "claims_by_trigger" in st, "")

        r = client.get("/claims/admin/all?limit=5", headers=auth)
        chk("7.5 Admin claims list", r.status_code == 200, "")

        r = client.get("/claims/admin/workers", headers=auth)
        chk("7.6 Admin workers", r.status_code == 200, "")

        print()

        # 8. PREDICTIVE FORECAST
        print("--- 8. Predictive Analytics ---")
        r = client.get("/analytics/forecast", headers=auth)
        fc = r.json()
        chk("8.1 Forecast 200", r.status_code == 200, "")
        chk("8.2 predicted_loss_ratio", fc.get("predicted_loss_ratio") is not None, "")
        chk("8.3 predicted_claims", fc.get("predicted_claims") is not None, "")
        chk("8.4 confidence_interval pair", len(fc.get("confidence_interval", [])) == 2, "")
        chk("8.5 7 cities", len(fc.get("city_risk_forecast", [])) == 7, f"n={len(fc.get('city_risk_forecast', []))}")
        chk("8.6 EWMA method", "EWMA" in fc.get("method", ""), "")

        r = client.get("/analytics/cities", headers=auth)
        chk("8.7 City analytics", r.status_code == 200, "")

        print()

        # 9. RAZORPAY WEBHOOK
        print("--- 9. Razorpay Webhook ---")
        r = client.get("/webhooks/razorpay/health")
        wh = r.json()
        chk("9.1 Webhook health", r.status_code == 200, "")
        chk("9.2 razorpay_configured flag", "razorpay_configured" in wh, "")
        print(f"         razorpay_configured = {wh.get('razorpay_configured')}")

        print()

        # 10. CROSS-CITY FRAUD
        print("--- 10. Cross-City Fraud Detection ---")
        r = client.post("/triggers/simulate", json={"trigger_type": "Hazardous AQI", "city": "Delhi"}, headers=auth)
        chk("10.1 Delhi trigger fires", r.status_code == 201, "")
        time.sleep(3)
        r = client.get("/claims/", headers=auth)
        delhi = [c for c in r.json() if c.get("trigger_city") == "Delhi"]
        chk("10.2 Mumbai worker gets no Delhi claim", len(delhi) == 0, f"count={len(delhi)}")

        print()

        # 11. POLICY LIFECYCLE
        print("--- 11. Policy Cancel + Reactivate ---")
        pol_id = pol.get("id")
        if pol_id:
            r = client.delete(f"/policies/{pol_id}", headers=auth)
            chk("11.1 Cancel", r.status_code in (200, 204), f"status={r.status_code}")
            ws2 = client.get("/claims/my-weekly-summary", headers=auth).json()
            chk("11.2 Inactive after cancel", ws2["active_coverage"] is False, f"v={ws2['active_coverage']}")
            r = client.post("/policies/", json={"tier": "Pro Armor"}, headers=auth)
            chk("11.3 Reactivate Pro Armor", r.status_code == 201, f"status={r.status_code}")

        print()

        # 12. VALIDATION
        print("--- 12. Input Validation ---")
        r = client.post("/auth/register", json={"full_name": "X"})
        chk("12.1 Missing fields = 422", r.status_code == 422, f"s={r.status_code}")
        r = client.post(
            "/auth/register",
            json={
                "full_name": "Dup",
                "phone": phone,
                "password": "dup1234",
                "city": "Mumbai",
                "pincode": "400001",
                "platform": "Blinkit",
            },
        )
        chk("12.2 Duplicate phone = 400", r.status_code == 400, f"s={r.status_code}")

        print()
        print("=" * 60)
        print(f"  GRAND TOTAL: {passed} passed, {failed} failed out of {passed + failed}")
        print(f"  Target: {base_url}")
        print("=" * 60)

        return {"passed": passed, "failed": failed}
    finally:
        client.close()


@pytest.mark.skipif(
    not RUN_LIVE_RENDER_TESTS,
    reason="Live Render smoke test is opt-in. Set RUN_LIVE_RENDER_TESTS=1 to execute it.",
)
def test_live_render_deployment():
    """Opt-in live deployment verification against the hosted Render app."""
    result = _run_live_render_suite()
    assert result["failed"] == 0, f"{result['failed']} live deployment checks failed"
    assert result["passed"] > 0


def main() -> int:
    """Manual script entrypoint for the live Render smoke test."""
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")

    result = _run_live_render_suite()
    return 0 if result["failed"] == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
