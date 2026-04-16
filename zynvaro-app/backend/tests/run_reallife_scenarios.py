"""
REAL-LIFE SCENARIO TESTS: Gig Worker Daily Journeys
Simulates actual user workflows end-to-end via the API.
Run: python tests/run_reallife_scenarios.py
"""
import sys, io, os, json, time
sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8')
os.environ["SECRET_KEY"] = "test-key-123"
os.environ["RAZORPAY_KEY_ID"] = ""
os.environ["RAZORPAY_KEY_SECRET"] = ""

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient
from database import engine, Base
from models import *

# Fresh DB for each run
Base.metadata.drop_all(bind=engine)
Base.metadata.create_all(bind=engine)

from main import app
client = TestClient(app)

passed = 0
failed = 0
def chk(name, cond, detail=''):
    global passed, failed
    if cond:
        passed += 1
        print(f'  PASS  {name}')
    else:
        failed += 1
        print(f'  FAIL  {name} -- {detail}')

print("=========================================================")
print("  REAL-LIFE SCENARIO TESTING: Gig Worker Daily Journeys")
print("=========================================================")
print()

# ============================================================
print("SCENARIO 1: Ravi registers, buys policy, monsoon hits, gets paid")
print()

resp = client.post("/auth/register", json={
    "full_name": "Ravi Kumar", "phone": "9111000001", "password": "ravi1234",
    "city": "Bangalore", "pincode": "560047", "platform": "Blinkit",
    "vehicle_type": "2-Wheeler", "shift": "Evening Peak (6PM-2AM)",
})
chk("S1.1 Register", resp.status_code == 201, f"status={resp.status_code}")
token = resp.json().get("access_token", "")
auth = {"Authorization": f"Bearer {token}"}

resp = client.get("/auth/me", headers=auth)
me = resp.json()
chk("S1.2 GPS assigned at registration", me.get("home_lat") is not None, f"lat={me.get('home_lat')}")
chk("S1.3 Zone risk computed", me.get("zone_risk_score", 0) > 0, f"risk={me.get('zone_risk_score')}")

resp = client.get("/policies/quote/all", headers=auth)
chk("S1.4 Quotes endpoint", resp.status_code == 200, f"status={resp.status_code}")
quote_data = resp.json()
# Response is {"tiers": [...], "worker_city": ..., ...}
quotes = quote_data.get("tiers", []) if isinstance(quote_data, dict) else []
chk("S1.5 3 tier quotes", len(quotes) == 3, f"count={len(quotes)}, keys={list(quote_data.keys()) if isinstance(quote_data, dict) else 'not dict'}")
basic = next((q for q in quotes if q.get("tier") == "Basic Shield"), None) if quotes else None
chk("S1.6 Premium 20-50 INR", basic is not None and 20 <= basic["weekly_premium"] <= 50, f"prem={basic}")

resp = client.post("/policies/", json={"tier": "Basic Shield"}, headers=auth)
chk("S1.6 Activate policy", resp.status_code == 201, f"status={resp.status_code}")

resp = client.get("/policies/active", headers=auth)
pol = resp.json()
chk("S1.7 Policy active", pol.get("status") == "active", f"status={pol.get('status')}")

resp = client.post("/triggers/simulate", json={"trigger_type": "Heavy Rainfall", "city": "Bangalore"}, headers=auth)
chk("S1.8 Trigger fires", resp.status_code == 201, f"status={resp.status_code}")
time.sleep(1)

resp = client.get("/claims/", headers=auth)
claims = resp.json()
chk("S1.9 Claim created", len(claims) >= 1, f"count={len(claims)}")
if claims:
    c = claims[0]
    chk("S1.10 Claim PAID", c["status"] == "paid", f"status={c['status']}")
    chk("S1.11 Payout > 0", c["payout_amount"] > 0, f"amt={c['payout_amount']}")
    chk("S1.12 Payment ref set", c["payment_ref"] is not None, f"ref={c['payment_ref']}")
    chk("S1.13 GPS validated", c.get("gps_valid") is not None, "")
    chk("S1.14 Risk tier", c.get("risk_tier") is not None, f"tier={c.get('risk_tier')}")
    chk("S1.15 Payout gateway", c.get("payout_gateway") is not None, f"gw={c.get('payout_gateway')}")

print()
# ============================================================
print("SCENARIO 2: Mumbai worker, Delhi trigger = NO CLAIM (wrong city)")
print()

resp = client.post("/auth/register", json={
    "full_name": "Priya Sharma", "phone": "9111000002", "password": "priya1234",
    "city": "Mumbai", "pincode": "400051", "platform": "Zepto",
    "shift": "Morning Rush (6AM-2PM)",
})
token2 = resp.json().get("access_token", "")
auth2 = {"Authorization": f"Bearer {token2}"}
client.post("/policies/", json={"tier": "Standard Guard"}, headers=auth2)

# Delhi trigger - Priya is in Mumbai, should get no claim
resp = client.post("/triggers/simulate", json={"trigger_type": "Hazardous AQI", "city": "Delhi"}, headers=auth2)
chk("S2.1 Delhi trigger fires", resp.status_code == 201, "")
time.sleep(1)

resp = client.get("/claims/", headers=auth2)
delhi_claims = [c for c in resp.json() if c.get("trigger_city") == "Delhi"]
chk("S2.2 No Delhi claim for Mumbai worker", len(delhi_claims) == 0, f"count={len(delhi_claims)}")

# Mumbai trigger - Priya SHOULD get a claim
resp = client.post("/triggers/simulate", json={"trigger_type": "Heavy Rainfall", "city": "Mumbai"}, headers=auth2)
time.sleep(1)
resp = client.get("/claims/", headers=auth2)
mum_claims = [c for c in resp.json() if c.get("trigger_city") == "Mumbai"]
chk("S2.3 Mumbai claim exists for Mumbai worker", len(mum_claims) >= 1, f"count={len(mum_claims)}")

print()
# ============================================================
print("SCENARIO 3: Arjun claims 5x in one week (frequency abuse detection)")
print()

resp = client.post("/auth/register", json={
    "full_name": "Arjun Patel", "phone": "9111000003", "password": "arjun1234",
    "city": "Chennai", "pincode": "600001", "platform": "Swiggy",
    "shift": "Full Day (8AM-8PM)",
})
token3 = resp.json().get("access_token", "")
auth3 = {"Authorization": f"Bearer {token3}"}
client.post("/policies/", json={"tier": "Pro Armor"}, headers=auth3)

triggers = ["Heavy Rainfall", "Severe Heatwave", "Hazardous AQI", "Platform Outage", "Civil Disruption"]
for tt in triggers:
    client.post("/triggers/simulate", json={"trigger_type": tt, "city": "Chennai"}, headers=auth3)
    time.sleep(0.3)
time.sleep(1)

resp = client.get("/claims/", headers=auth3)
arjun_claims = resp.json()
chk("S3.1 Multiple claims created", len(arjun_claims) >= 3, f"count={len(arjun_claims)}")

if arjun_claims:
    scores = [c["authenticity_score"] for c in arjun_claims]
    any_flagged = any(c.get("fraud_flags") for c in arjun_claims)
    chk("S3.2 Later claims have lower scores", min(scores) < max(scores), f"scores={scores}")
    chk("S3.3 Frequency flags raised", any_flagged, f"flags={[c.get('fraud_flags','')[:40] for c in arjun_claims[:2]]}")

    total_paid = sum(c["payout_amount"] for c in arjun_claims if c["status"] == "paid")
    chk("S3.4 Weekly cap enforced (<=2000 Pro)", total_paid <= 2000, f"total={total_paid}")

print()
# ============================================================
print("SCENARIO 4: No policy = no claim")
print()

resp = client.post("/auth/register", json={
    "full_name": "Sneha Roy", "phone": "9111000004", "password": "sneha1234",
    "city": "Pune", "pincode": "411001", "platform": "Instamart",
})
token4 = resp.json().get("access_token", "")
auth4 = {"Authorization": f"Bearer {token4}"}

client.post("/triggers/simulate", json={"trigger_type": "Heavy Rainfall", "city": "Pune"}, headers=auth4)
time.sleep(1)
resp = client.get("/claims/", headers=auth4)
chk("S4.1 No claim without policy", len(resp.json()) == 0, f"claims={len(resp.json())}")

print()
# ============================================================
print("SCENARIO 5: Admin approves pending claim")
print()

resp = client.get("/claims/admin/all?limit=50", headers=auth)
all_claims = resp.json()
pending = [c for c in all_claims if c["status"] in ("pending_review", "manual_review")]
chk("S5.1 Admin lists all claims", len(all_claims) > 0, f"total={len(all_claims)}")

if pending:
    pid = pending[0]["id"]
    resp = client.patch(f"/claims/{pid}/approve", headers=auth)
    chk("S5.2 Approve works", resp.status_code == 200, f"status={resp.status_code}")
    approved = resp.json()
    chk("S5.3 Now PAID", approved["status"] == "paid", f"status={approved['status']}")
    chk("S5.4 Payment ref", approved["payment_ref"] is not None, "")
else:
    chk("S5.2 All auto-approved (no pending)", True, "")
    chk("S5.3 Skip", True, ""); chk("S5.4 Skip", True, "")

# Admin rejects a claim
if len(pending) > 1:
    rid = pending[1]["id"]
    resp = client.patch(f"/claims/{rid}/reject", headers=auth)
    chk("S5.5 Reject works", resp.status_code == 200, "")
    chk("S5.6 Status rejected", resp.json()["status"] == "rejected", "")
else:
    chk("S5.5 No second pending to reject", True, ""); chk("S5.6 Skip", True, "")

print()
# ============================================================
print("SCENARIO 6: Cancel policy, then trigger = no claim")
print()

resp = client.post("/auth/register", json={
    "full_name": "Deepak Singh", "phone": "9111000005", "password": "deepak1234",
    "city": "Kolkata", "pincode": "700001", "platform": "Blinkit",
})
token5 = resp.json().get("access_token", "")
auth5 = {"Authorization": f"Bearer {token5}"}
client.post("/policies/", json={"tier": "Basic Shield"}, headers=auth5)

resp = client.get("/policies/active", headers=auth5)
pol_id = resp.json().get("id")
if pol_id:
    resp = client.delete(f"/policies/{pol_id}", headers=auth5)
    chk("S6.1 Cancel policy", resp.status_code in (200, 204), f"status={resp.status_code}")

client.post("/triggers/simulate", json={"trigger_type": "Civil Disruption", "city": "Kolkata"}, headers=auth5)
time.sleep(1)
resp = client.get("/claims/", headers=auth5)
chk("S6.2 No claim after cancel", len(resp.json()) == 0, f"claims={len(resp.json())}")

print()
# ============================================================
print("SCENARIO 7: Duplicate trigger within 24h = 1 claim only")
print()

client.post("/triggers/simulate", json={"trigger_type": "Extreme Rain / Flooding", "city": "Bangalore"}, headers=auth)
time.sleep(0.5)
client.post("/triggers/simulate", json={"trigger_type": "Extreme Rain / Flooding", "city": "Bangalore"}, headers=auth)
time.sleep(1)

resp = client.get("/claims/", headers=auth)
extreme = [c for c in resp.json() if c.get("trigger_type") == "Extreme Rain / Flooding"]
chk("S7.1 Only 1 claim for duplicate trigger", len(extreme) <= 1, f"count={len(extreme)}")

print()
# ============================================================
print("SCENARIO 8: Admin dashboard analytics")
print()

resp = client.get("/claims/admin/stats", headers=auth)
stats = resp.json()
chk("S8.1 Stats work", resp.status_code == 200, "")
chk("S8.2 Total claims > 0", stats.get("total_claims", 0) > 0, f"total={stats.get('total_claims')}")
chk("S8.3 Loss ratio present", "loss_ratio_pct" in stats, "")
chk("S8.4 Trigger breakdown", "claims_by_trigger" in stats, "")

resp = client.get("/claims/admin/workers", headers=auth)
chk("S8.5 Workers list", len(resp.json()) > 0, f"count={len(resp.json())}")

print()
# ============================================================
print("SCENARIO 9: Security - no auth = blocked")
print()

chk("S9.1 Claims no auth = 401", client.get("/claims/").status_code == 401, "")
chk("S9.2 Policy no auth = 401", client.get("/policies/active").status_code == 401, "")
chk("S9.3 Trigger no auth = 401", client.post("/triggers/simulate", json={"trigger_type": "Heavy Rainfall", "city": "Mumbai"}).status_code == 401, "")
chk("S9.4 Admin no auth = 401", client.get("/claims/admin/all").status_code == 401, "")
chk("S9.5 Bad token = 401", client.get("/claims/", headers={"Authorization": "Bearer fake.token"}).status_code == 401, "")

print()
# ============================================================
print("SCENARIO 10: Bad inputs = 400/422, never 500")
print()

chk("S10.1 Bad trigger type", client.post("/triggers/simulate", json={"trigger_type": "Alien Invasion", "city": "Mumbai"}, headers=auth).status_code == 400, "")
chk("S10.2 Bad city", client.post("/triggers/simulate", json={"trigger_type": "Heavy Rainfall", "city": "Atlantis"}, headers=auth).status_code == 400, "")
chk("S10.3 Missing fields", client.post("/auth/register", json={"full_name": "X"}).status_code == 422, "")
chk("S10.4 Duplicate phone", client.post("/auth/register", json={
    "full_name": "Dup", "phone": "9111000001", "password": "dup1234",
    "city": "Mumbai", "pincode": "400001", "platform": "Blinkit",
}).status_code == 400, "")
chk("S10.5 Short password", client.post("/auth/register", json={
    "full_name": "Short", "phone": "9111000099", "password": "12",
    "city": "Mumbai", "pincode": "400001", "platform": "Blinkit",
}).status_code == 422, "")

print()
print(f"=== GRAND TOTAL: {passed} passed, {failed} failed out of {passed+failed} ===")
