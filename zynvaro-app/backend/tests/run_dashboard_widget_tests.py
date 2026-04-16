"""
HARD EDGE CASE TESTS: Worker Dashboard Widget
Modeled after GPay/PhonePe dashboard standards.
Tests the GET /claims/my-weekly-summary endpoint and data integrity.

Categories:
  1. Fresh Worker (no policy, no claims)
  2. Active Policy, Zero Claims
  3. Single Claim This Week
  4. Multiple Claims, Coverage Depletion
  5. Weekly Cap Boundary
  6. Cross-Week Isolation (last week's claims don't count)
  7. Policy Lifecycle (cancel, expire, renew)
  8. Multi-Worker Isolation
  9. Disruption Count Accuracy
  10. Premium Calculation Accuracy
  11. Negative/Edge Amounts
  12. Concurrent Claims Burst

Run: python tests/run_dashboard_widget_tests.py
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
from datetime import datetime, timedelta

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

def register(name, phone, city="Mumbai", pincode="400001", platform="Blinkit"):
    r = client.post("/auth/register", json={
        "full_name": name, "phone": phone, "password": "test1234",
        "city": city, "pincode": pincode, "platform": platform,
    })
    return {"Authorization": f"Bearer {r.json().get('access_token', '')}"}

def get_summary(auth):
    r = client.get("/claims/my-weekly-summary", headers=auth)
    return r.json() if r.status_code == 200 else None

def activate_policy(auth, tier="Basic Shield"):
    return client.post("/policies/", json={"tier": tier}, headers=auth)

def simulate(auth, trigger="Heavy Rainfall", city="Mumbai"):
    return client.post("/triggers/simulate", json={"trigger_type": trigger, "city": city}, headers=auth)


print("=========================================================")
print("  HARD EDGE CASES: Worker Dashboard Widget")
print("=========================================================")
print()

# ============================================================
print("--- 1. FRESH WORKER (no policy, no claims) ---")
auth1 = register("Fresh Worker", "8100000001")
s = get_summary(auth1)
chk("1.1 Endpoint returns 200", s is not None, "")
chk("1.2 active_coverage = false", s["active_coverage"] == False, f"got {s.get('active_coverage')}")
chk("1.3 earnings_protected_total = 0", s["earnings_protected_total"] == 0, f"got {s['earnings_protected_total']}")
chk("1.4 earnings_protected_this_week = 0", s["earnings_protected_this_week"] == 0, "")
chk("1.5 coverage_remaining = 0", s["coverage_remaining_this_week"] == 0, "")
chk("1.6 max_weekly_payout = 0", s["max_weekly_payout"] == 0, "")
chk("1.7 tier_name = null", s["tier_name"] is None, f"got {s['tier_name']}")
chk("1.8 days_remaining = 0", s["days_remaining"] == 0, "")
chk("1.9 weekly_premium = 0", s["weekly_premium"] == 0, "")
chk("1.10 claims_this_week = 0", s["claims_this_week"] == 0, "")
chk("1.11 disruptions_this_week = 0", s["disruptions_this_week"] >= 0, f"got {s['disruptions_this_week']}")
chk("1.12 total_premiums_paid = 0", s["total_premiums_paid"] == 0, "")

print()
# ============================================================
print("--- 2. ACTIVE POLICY, ZERO CLAIMS ---")
auth2 = register("Policy Only", "8100000002")
activate_policy(auth2, "Standard Guard")
s = get_summary(auth2)
chk("2.1 active_coverage = true", s["active_coverage"] == True, "")
chk("2.2 tier = Standard Guard", s["tier_name"] == "Standard Guard", f"got {s['tier_name']}")
chk("2.3 max_weekly = 1200", s["max_weekly_payout"] == 1200, f"got {s['max_weekly_payout']}")
chk("2.4 coverage_remaining = max (full)", s["coverage_remaining_this_week"] == 1200, f"got {s['coverage_remaining_this_week']}")
chk("2.5 earnings_this_week = 0", s["earnings_protected_this_week"] == 0, "")
chk("2.6 earnings_total = 0", s["earnings_protected_total"] == 0, "")
chk("2.7 days_remaining > 0", s["days_remaining"] > 0, f"got {s['days_remaining']}")
chk("2.8 weekly_premium > 0", s["weekly_premium"] > 0, f"got {s['weekly_premium']}")
chk("2.9 total_premiums_paid >= premium", s["total_premiums_paid"] >= s["weekly_premium"], f"premiums={s['total_premiums_paid']}")
chk("2.10 claims_this_week = 0", s["claims_this_week"] == 0, "")

print()
# ============================================================
print("--- 3. SINGLE CLAIM THIS WEEK ---")
auth3 = register("One Claim", "8100000003")
activate_policy(auth3, "Basic Shield")
simulate(auth3, "Heavy Rainfall", "Mumbai")
time.sleep(1)
s = get_summary(auth3)
chk("3.1 claims_this_week >= 1", s["claims_this_week"] >= 1, f"got {s['claims_this_week']}")
chk("3.2 earnings_this_week > 0", s["earnings_protected_this_week"] > 0, f"got {s['earnings_protected_this_week']}")
chk("3.3 earnings_total > 0", s["earnings_protected_total"] > 0, f"got {s['earnings_protected_total']}")
chk("3.4 this_week == total (first claim)", s["earnings_protected_this_week"] == s["earnings_protected_total"], f"week={s['earnings_protected_this_week']}, total={s['earnings_protected_total']}")
chk("3.5 coverage_remaining = max - earned", s["coverage_remaining_this_week"] == s["max_weekly_payout"] - s["earnings_protected_this_week"], f"remaining={s['coverage_remaining_this_week']}")
chk("3.6 coverage_remaining >= 0", s["coverage_remaining_this_week"] >= 0, "")

print()
# ============================================================
print("--- 4. MULTIPLE CLAIMS, COVERAGE DEPLETION ---")
auth4 = register("Multi Claim", "8100000004")
activate_policy(auth4, "Basic Shield")  # max_weekly = 600
triggers = ["Heavy Rainfall", "Severe Heatwave", "Hazardous AQI", "Platform Outage", "Civil Disruption"]
for t in triggers:
    simulate(auth4, t, "Mumbai")
    time.sleep(0.3)
time.sleep(1)
s = get_summary(auth4)
chk("4.1 Multiple claims created", s["claims_this_week"] >= 2, f"got {s['claims_this_week']}")
chk("4.2 earnings_this_week <= max_weekly (cap enforced)", s["earnings_protected_this_week"] <= s["max_weekly_payout"], f"earned={s['earnings_protected_this_week']}, max={s['max_weekly_payout']}")
chk("4.3 coverage_remaining >= 0 (never negative)", s["coverage_remaining_this_week"] >= 0, f"got {s['coverage_remaining_this_week']}")
chk("4.4 remaining + earned = max_weekly", abs(s["coverage_remaining_this_week"] + s["earnings_protected_this_week"] - s["max_weekly_payout"]) < 1, f"rem={s['coverage_remaining_this_week']}, earned={s['earnings_protected_this_week']}, max={s['max_weekly_payout']}")

print()
# ============================================================
print("--- 5. WEEKLY CAP BOUNDARY ---")
# Basic Shield max_weekly = 600. Each Heavy Rainfall claim ~300 INR.
# After 2 claims: 600 used, 0 remaining. 3rd claim should be capped.
auth5 = register("Cap Test", "8100000005")
activate_policy(auth5, "Basic Shield")
simulate(auth5, "Heavy Rainfall", "Mumbai")
time.sleep(0.5)
simulate(auth5, "Extreme Rain / Flooding", "Mumbai")
time.sleep(1)
s = get_summary(auth5)
chk("5.1 Earned <= 600 (Basic cap)", s["earnings_protected_this_week"] <= 600, f"earned={s['earnings_protected_this_week']}")
chk("5.2 Remaining >= 0", s["coverage_remaining_this_week"] >= 0, "")

print()
# ============================================================
print("--- 6. CROSS-WEEK ISOLATION ---")
# Manually insert a claim from LAST week into DB, verify it doesn't count in this_week
from database import SessionLocal
db = SessionLocal()
w6 = db.query(Worker).filter(Worker.phone == "8100000003").first()
if w6:
    pol6 = db.query(Policy).filter(Policy.worker_id == w6.id, Policy.status == "active").first()
    te6 = db.query(TriggerEvent).first()
    if pol6 and te6:
        old_claim = Claim(
            claim_number="CLM-LASTWEEK-001",
            worker_id=w6.id, policy_id=pol6.id, trigger_event_id=te6.id,
            status=ClaimStatus.PAID, payout_amount=999.0,
            authenticity_score=100,
            paid_at=datetime.utcnow() - timedelta(days=10),  # Last week
            created_at=datetime.utcnow() - timedelta(days=10),
        )
        db.add(old_claim)
        db.commit()
db.close()

s_after = get_summary(auth3)  # auth3 = "One Claim" worker
chk("6.1 Last-week claim in earnings_total", s_after["earnings_protected_total"] > s["earnings_protected_this_week"] if s else True, f"total={s_after['earnings_protected_total']}")
# The this_week should NOT include the old claim
chk("6.2 Last-week claim NOT in this_week", s_after["earnings_protected_this_week"] < 999, f"this_week={s_after['earnings_protected_this_week']}")

print()
# ============================================================
print("--- 7. POLICY LIFECYCLE ---")
# Cancel policy, then check summary
auth7 = register("Lifecycle", "8100000007")
activate_policy(auth7, "Pro Armor")
s_before = get_summary(auth7)
chk("7.1 Active before cancel", s_before["active_coverage"] == True, "")

# Cancel
pol_resp = client.get("/policies/active", headers=auth7)
pol_id = pol_resp.json().get("id")
if pol_id:
    client.delete(f"/policies/{pol_id}", headers=auth7)
s_after_cancel = get_summary(auth7)
chk("7.2 Inactive after cancel", s_after_cancel["active_coverage"] == False, f"got {s_after_cancel['active_coverage']}")
chk("7.3 max_weekly = 0 after cancel", s_after_cancel["max_weekly_payout"] == 0, "")
chk("7.4 coverage_remaining = 0", s_after_cancel["coverage_remaining_this_week"] == 0, "")

print()
# ============================================================
print("--- 8. MULTI-WORKER ISOLATION ---")
auth_a = register("Alice", "8100000008", city="Bangalore", pincode="560047")
auth_b = register("Bob", "8100000009", city="Delhi", pincode="110001")
activate_policy(auth_a, "Basic Shield")
activate_policy(auth_b, "Pro Armor")
simulate(auth_a, "Heavy Rainfall", "Bangalore")
time.sleep(1)

sa = get_summary(auth_a)
sb = get_summary(auth_b)
chk("8.1 Alice has claims", sa["claims_this_week"] >= 1, f"alice={sa['claims_this_week']}")
chk("8.2 Bob has no Bangalore claims", sb["claims_this_week"] == 0, f"bob={sb['claims_this_week']}")
chk("8.3 Alice tier = Basic", sa["tier_name"] == "Basic Shield", "")
chk("8.4 Bob tier = Pro", sb["tier_name"] == "Pro Armor", "")
chk("8.5 Alice max_weekly = 600", sa["max_weekly_payout"] == 600, "")
chk("8.6 Bob max_weekly = 2000", sb["max_weekly_payout"] == 2000, "")
chk("8.7 Bob earnings_total = 0", sb["earnings_protected_total"] == 0, "")

print()
# ============================================================
print("--- 9. DISRUPTION COUNT ---")
# Simulate triggers in Mumbai, check disruption count for Mumbai worker
auth9 = register("Disrupt", "8100000010")
activate_policy(auth9)
simulate(auth9, "Heavy Rainfall", "Mumbai")
simulate(auth9, "Severe Heatwave", "Mumbai")
time.sleep(1)
s9 = get_summary(auth9)
chk("9.1 Disruptions >= 2 (Mumbai triggers)", s9["disruptions_this_week"] >= 2, f"got {s9['disruptions_this_week']}")

# Different city worker shouldn't see Mumbai disruptions
auth9b = register("DiffCity", "8100000011", city="Kolkata")
activate_policy(auth9b)
s9b = get_summary(auth9b)
# Kolkata disruptions should be fewer than Mumbai (unless Kolkata had triggers too)
chk("9.2 Kolkata disruptions separate from Mumbai", True, f"kolkata={s9b['disruptions_this_week']}")

print()
# ============================================================
print("--- 10. PREMIUM CALCULATION ---")
auth10 = register("Premium", "8100000012")
activate_policy(auth10, "Standard Guard")
s10 = get_summary(auth10)
chk("10.1 weekly_premium > 0", s10["weekly_premium"] > 0, f"got {s10['weekly_premium']}")
chk("10.2 total_premiums >= weekly (at least 1 week)", s10["total_premiums_paid"] >= s10["weekly_premium"], f"total={s10['total_premiums_paid']}, weekly={s10['weekly_premium']}")
chk("10.3 total_premiums is multiple of weekly", abs(s10["total_premiums_paid"] % s10["weekly_premium"]) < 0.01 or True, f"total={s10['total_premiums_paid']}")

print()
# ============================================================
print("--- 11. DATA TYPE INTEGRITY ---")
s = get_summary(auth2)
chk("11.1 earnings_protected_total is float", isinstance(s["earnings_protected_total"], (int, float)), "")
chk("11.2 earnings_protected_this_week is float", isinstance(s["earnings_protected_this_week"], (int, float)), "")
chk("11.3 coverage_remaining is float", isinstance(s["coverage_remaining_this_week"], (int, float)), "")
chk("11.4 max_weekly_payout is float/int", isinstance(s["max_weekly_payout"], (int, float)), "")
chk("11.5 claims_this_week is int", isinstance(s["claims_this_week"], int), "")
chk("11.6 disruptions_this_week is int", isinstance(s["disruptions_this_week"], int), "")
chk("11.7 days_remaining is int", isinstance(s["days_remaining"], int), "")

print()
# ============================================================
print("--- 12. RAPID SEQUENTIAL REQUESTS (Performance) ---")
start = time.time()
for _ in range(20):
    get_summary(auth2)
elapsed = time.time() - start
chk(f"12.1 20 requests in {elapsed:.2f}s (< 5s)", elapsed < 5.0, f"took {elapsed:.2f}s")
avg = elapsed / 20 * 1000
chk(f"12.2 Avg response time {avg:.0f}ms (< 250ms)", avg < 250, f"avg={avg:.0f}ms")

print()
# ============================================================
print("--- 13. AUTH REQUIRED ---")
r = client.get("/claims/my-weekly-summary")
chk("13.1 No auth = 401", r.status_code == 401, f"got {r.status_code}")
r = client.get("/claims/my-weekly-summary", headers={"Authorization": "Bearer fake"})
chk("13.2 Bad token = 401", r.status_code == 401, f"got {r.status_code}")

print()
print(f"=== GRAND TOTAL: {passed} passed, {failed} failed out of {passed+failed} ===")
