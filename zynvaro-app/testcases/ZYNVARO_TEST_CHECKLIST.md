# Zynvaro — Pre-Demo Test Checklist
### Team AeroFyta · Guidewire DEVTrails 2026

> **How to use this:**
> - Work through each section top to bottom
> - Expand a test → run the exact command → check the pass/fail condition
> - Mark `[ ]` → `[x]` once a test passes
> - The **Claude Code prompt** at the bottom of each test is what you paste directly into Claude Code

---

## Progress Tracker

| Section | Tests | Status |
|---|---|---|
| 1. Start the app | T01 · T02 · T03 | `[ ] [ ] [ ]` |
| 2. Worker registration | T04 · T05 · T06 | `[ ] [ ] [ ]` |
| 3. Policy & quotes | T07 · T08 · T09 · T10 | `[ ] [ ] [ ] [ ]` |
| 4. Trigger engine | T11 · T12 · T13 | `[ ] [ ] [ ]` |
| 5. Zero-touch claims | T14 · T15 · T16 · T17 | `[ ] [ ] [ ] [ ]` |
| 6. Fraud detection | T18 · T19 · T20 | `[ ] [ ] [ ]` |
| 7. Analytics dashboard | T21 · T22 · T23 · T24 | `[ ] [ ] [ ] [ ]` |
| 8. ML model info | T25 · T26 | `[ ] [ ]` |
| 9. PWA frontend | T27 · T28 · T29 · T30 | `[ ] [ ] [ ] [ ]` |
| 10. Full test suite | T31 · T32 | `[ ] [ ]` |

> ⭐ **Critical 5 — do these first:** T12, T14, T16, T18, T31

---

## Section 1 — Start the App

<details>
<summary><strong>T01</strong> · Server starts without crashing &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to run:**
```bash
cd zynvaro-app/backend
uvicorn main:app --host 0.0.0.0 --port 9001 --reload
```

**What you should see:**
```
INFO:     Uvicorn running on http://0.0.0.0:9001
INFO:     Application startup complete.
```

**✅ PASS if:** Server is running and NO red error/traceback text appears

**❌ FAIL if:** You see `ModuleNotFoundError`, `Address already in use`, or any Python traceback

---

**Claude Code prompt:**
```
Run the server with: uvicorn main:app --host 0.0.0.0 --port 9001 --reload
Tell me the exact output — especially any error lines. If there are errors, show me the full traceback.
```

</details>

---

<details>
<summary><strong>T02</strong> · Health check returns OK &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to run** *(open a new terminal)*:
```bash
curl http://localhost:9001/health
```

**✅ PASS if:** You see `{"status":"ok"}` or similar JSON

**❌ FAIL if:** `curl: connection refused` or an HTML error page

---

**Claude Code prompt:**
```
Run: curl http://localhost:9001/health
Tell me the exact response.
```

</details>

---

<details>
<summary><strong>T03</strong> · PWA loads in browser &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to do:** Open your browser and go to:
```
http://localhost:9001/app
```

**✅ PASS if:** A dark screen with Zynvaro logo and a login form appears

**❌ FAIL if:** Blank white page, or `Cannot GET /app`

---

**Claude Code prompt:**
```
Open http://localhost:9001/app in the browser. Describe what appears on screen.
```

</details>

---

## Section 2 — Worker Registration

<details>
<summary><strong>T04</strong> · Register a new worker via API &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to run:**
```bash
curl -X POST http://localhost:9001/auth/register \
  -H "Content-Type: application/json" \
  -d '{
    "full_name": "Test Rider",
    "phone": "9999999999",
    "password": "test1234",
    "city": "Bangalore",
    "pincode": "560047",
    "platform": "Blinkit",
    "shift": "Evening Peak (6PM-2AM)",
    "vehicle_type": "2-Wheeler"
  }'
```

**✅ PASS if:** Response contains `"access_token"` — a long string of letters and numbers

**❌ FAIL if:** `"detail": "Phone already registered"` → use a different phone number. Any other error = fail.

---

**Claude Code prompt:**
```
Send a POST request to http://localhost:9001/auth/register with this JSON body:
{
  "full_name": "Test Rider",
  "phone": "9999999999",
  "password": "test1234",
  "city": "Bangalore",
  "pincode": "560047",
  "platform": "Blinkit",
  "shift": "Evening Peak",
  "vehicle_type": "2-Wheeler"
}
Show me the full response.
```

</details>

---

<details>
<summary><strong>T05</strong> · Login with existing demo worker &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to run:**
```bash
curl -X POST http://localhost:9001/auth/login \
  -d "username=9876543210&password=demo1234"
```

**✅ PASS if:** You get back JSON with `"access_token"` — **save this token, you need it for all following tests**

**❌ FAIL if:** `"Invalid credentials"` or any error

---

**Claude Code prompt:**
```
Login via POST to http://localhost:9001/auth/login
Body: {"phone": "9876543210", "password": "demo1234"}
Save the access_token from the response — I will need it for the next steps.
```

</details>

---

<details>
<summary><strong>T06</strong> · Get logged-in worker profile &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to run** *(replace `TOKEN` with your access_token from T05)*:
```bash
curl http://localhost:9001/auth/me \
  -H "Authorization: Bearer TOKEN"
```

**✅ PASS if:** You see the worker's name, city, platform — should show Ravi Kumar, Bangalore, Blinkit

**❌ FAIL if:** `"Not authenticated"` or 401 error

---

**Claude Code prompt:**
```
Use the access_token from the login step.
Call GET /auth/me with Authorization: Bearer <token>
What worker details does it return?
```

</details>

---

## Section 3 — Policy & Quotes

<details>
<summary><strong>T07</strong> · Get quotes for all 3 tiers &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to run** *(replace `TOKEN`)*:
```bash
curl http://localhost:9001/policies/quote/all \
  -H "Authorization: Bearer TOKEN"
```

**✅ PASS if:** You see 3 plans returned:
- Basic Shield (~₹36/week)
- Standard Guard (~₹56/week)
- Pro Armor (~₹109/week)

**❌ FAIL if:** Empty array `[]` or any error

---

**Claude Code prompt:**
```
Call GET /policies/quote/all with the Bearer token.
Show me all 3 tiers and their weekly premium amounts.
```

</details>

---

<details>
<summary><strong>T08</strong> · Premium quote has SHAP explanation &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to do:** Look at the quote response from T07 above

**✅ PASS if:** Each tier has an `explanation` or `narrative` field — a human-readable sentence like:
> *"📍 Bangalore zone has moderate risk profile. ☀️ Low-risk season. ✅ No claim history."*

**❌ FAIL if:** `explanation` field is `null`, empty `""`, or missing entirely

---

**Claude Code prompt:**
```
In the /policies/quote/all response, check if there is an explanation or narrative field for each tier.
Show me the exact text of that explanation for Basic Shield.
```

</details>

---

<details>
<summary><strong>T09</strong> · Buy a policy &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to run** *(replace `TOKEN`)*:
```bash
curl -X POST http://localhost:9001/policies \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"tier": "Basic Shield"}'
```

**✅ PASS if:** Response has a policy object with:
- `status: "active"`
- `start_date` (today)
- `end_date` (7 days from today)
- `weekly_premium` amount

**❌ FAIL if:** `"Already has active policy"` — use a fresh test worker from T04. Any other error = fail.

---

**Claude Code prompt:**
```
Create a new policy with tier "basic" via POST /policies with the Bearer token.
Show me the full policy object returned — especially status, start_date, end_date, weekly_premium.
```

</details>

---

<details>
<summary><strong>T10</strong> · Active policy shows on dashboard &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to run** *(replace `TOKEN`)*:
```bash
curl http://localhost:9001/policies/active \
  -H "Authorization: Bearer TOKEN"
```

**✅ PASS if:** Returns the policy you created in T09 with `status: "active"`

**❌ FAIL if:** Returns `null`, empty `{}`, or `"no active policy"`

---

**Claude Code prompt:**
```
Call GET /policies/active with the Bearer token.
Does it return the active policy? Show me the status field.
```

</details>

---

## Section 4 — Trigger Engine

<details>
<summary><strong>T11</strong> · List all 6 trigger types &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to run:**
```bash
curl http://localhost:9001/triggers/types
```

**✅ PASS if:** You see all 6 types:
1. Heavy Rainfall
2. Extreme Flooding
3. Severe Heatwave
4. Hazardous AQI
5. Platform Outage
6. Civil Disruption

**❌ FAIL if:** Fewer than 6 types or empty response

---

**Claude Code prompt:**
```
Call GET /triggers/types — no auth needed.
List all trigger types returned. How many are there?
```

</details>

---

<details>
<summary>⭐ <strong>T12</strong> · Simulate a rainfall trigger — <em>CRITICAL TEST</em> &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

> This is the most important test. If this fails, your demo fails.

**What to run** *(replace `TOKEN`)*:
```bash
curl -X POST http://localhost:9001/triggers/simulate \
  -H "Authorization: Bearer TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"trigger_type": "Heavy Rainfall", "city": "Bangalore"}'
```

**✅ PASS if:** Returns a trigger event object with:
- `trigger_type: "heavy_rainfall"`
- `city: "Bangalore"`
- A `triggered_at` timestamp
- A trigger `id`

**❌ FAIL if:** Any error, empty response, or missing fields

---

**Claude Code prompt:**
```
Simulate a heavy_rainfall trigger for Bangalore via POST /triggers/simulate with Bearer token.
Body: {"trigger_type": "Heavy Rainfall", "city": "Bangalore"}
Show me the full trigger event returned including its ID.
```

</details>

---

<details>
<summary><strong>T13</strong> · Trigger feed shows the fired event &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to run:**
```bash
curl http://localhost:9001/triggers/?limit=10
```

**✅ PASS if:** The Bangalore heavy_rainfall trigger from T12 appears at the top of the list

**❌ FAIL if:** Empty list or trigger not appearing

---

**Claude Code prompt:**
```
Call GET /triggers/?limit=10.
Does the Bangalore heavy_rainfall trigger appear? Show me the first 3 items.
```

</details>

---

## Section 5 — Zero-Touch Claims

<details>
<summary>⭐ <strong>T14</strong> · Claims auto-generated after trigger — <em>CRITICAL TEST</em> &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

> This proves your entire USP. No manual filing = zero-touch parametric insurance.

**What to run** *(replace `TOKEN` — run this AFTER T12)*:
```bash
curl http://localhost:9001/claims/ \
  -H "Authorization: Bearer TOKEN"
```

**✅ PASS if:** At least 1 claim exists — created automatically without you filing anything

**❌ FAIL if:** Empty claims list `[]` after a trigger was fired

---

**Claude Code prompt:**
```
After simulating the heavy_rainfall trigger, call GET /claims/ with Bearer token.
How many claims exist? Were they created automatically without manual filing?
```

</details>

---

<details>
<summary><strong>T15</strong> · Claim has correct payout amount &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to do:** Look at the claim object from T14

**✅ PASS if:** `payout_amount` is greater than 0 — should be approximately ₹300–330 for Basic Shield in Bangalore for heavy rainfall

**❌ FAIL if:** `payout_amount` is `0` or `null`

---

**Claude Code prompt:**
```
Show me the full claim object including payout_amount and status fields.
Is the payout_amount greater than zero?
```

</details>

---

<details>
<summary>⭐ <strong>T16</strong> · Clean worker claim is AUTO_APPROVED — <em>CRITICAL TEST</em> &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

> This is what you show judges. One trigger → instant payout. No forms. No waiting.

**What to run** *(replace `TOKEN` — use Ravi Kumar's token)*:
```bash
curl http://localhost:9001/claims/ \
  -H "Authorization: Bearer TOKEN"
```

**✅ PASS if:** The clean worker's claim shows:
- `status: "paid"` (auto-approved claims go straight to PAID)
- `paid_at` — a real timestamp
- `payment_ref` — something like `"MOCK-UPI-CLM-XXXXXXXX"`

**❌ FAIL if:** Status is `PENDING_REVIEW` or `MANUAL_REVIEW` for a clean demo worker

---

**Claude Code prompt:**
```
For Ravi Kumar's claim after the Bangalore rainfall trigger,
what is the exact status, paid_at timestamp, and payment_ref value?
```

</details>

---

<details>
<summary><strong>T17</strong> · Claim has authenticity score &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to do:** Look at the claim object from T14

**✅ PASS if:** `authenticity_score` field is present with a number between 0–100. A clean worker (Ravi) should score 75 or above.

**❌ FAIL if:** `authenticity_score` is `null` or missing

---

**Claude Code prompt:**
```
What is the authenticity_score on Ravi Kumar's claim?
What other fraud-related fields are present on the claim object?
```

</details>

---

## Section 6 — Fraud Detection

<details>
<summary>⭐ <strong>T18</strong> · All 3 demo fraud scenarios exist — <em>CRITICAL TEST</em> &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

> This is your fraud detection demo moment. Three claims, three different outcomes.

**What to run** *(replace `TOKEN`)*:
```bash
curl http://localhost:9001/claims/admin/all \
  -H "Authorization: Bearer TOKEN"
```

**✅ PASS if:** You see 3 pre-seeded demo claims with different statuses:
| Worker | Expected Status | Expected Score |
|---|---|---|
| Priya Sharma (Mumbai) | `paid` (auto-approved → instant payout) | ~100 |
| Arjun Mehta (Delhi) | `pending_review` (escrow hold) | ~70 |
| Ravi Kumar (Bangalore) | `manual_review` (city mismatch flagged) | ~40 |

**❌ FAIL if:** Fewer than 3 demo claims, or all have the same status. NOTE: Start with a fresh DB (`delete zynvaro.db` + restart server) to see the original seed scenarios.

---

**Claude Code prompt:**
```
Call GET /claims/admin/all with Bearer token.
List all claims with their worker name, status, and authenticity_score.
Do we have all 3 fraud scenarios (AUTO_APPROVED, PENDING_REVIEW, MANUAL_REVIEW)?
```

</details>

---

<details>
<summary><strong>T19</strong> · ML fraud probability is present on claims &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to do:** Look at any claim from the admin/all response in T18

**✅ PASS if:** Each claim has `ml_fraud_probability` — a decimal between 0 and 1:
- Clean claim → should be near `0.027`
- Fraudulent claim → should be near `0.887`

**❌ FAIL if:** `ml_fraud_probability` is `null` or missing on all claims

---

**Claude Code prompt:**
```
Check each of the 3 demo claims for an ml_fraud_probability field.
What are the exact values for AUTO_APPROVED, PENDING_REVIEW, and MANUAL_REVIEW claims?
```

</details>

---

<details>
<summary><strong>T20</strong> · City mismatch claim is correctly flagged &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to do:** Find the `MANUAL_REVIEW` claim from T18

**✅ PASS if:**
- Score is below 45
- Fraud signals show `city_match: false` or similar flag

**❌ FAIL if:** A city-mismatch worker gets `AUTO_APPROVED`

---

**Claude Code prompt:**
```
For the MANUAL_REVIEW claim, what fraud signals are listed?
Is city_match false? What is the ml_fraud_probability?
```

</details>

---

## Section 7 — Analytics Dashboard

<details>
<summary><strong>T21</strong> · Weekly KPIs load correctly &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to run** *(replace `TOKEN`)*:
```bash
curl http://localhost:9001/analytics/weekly \
  -H "Authorization: Bearer TOKEN"
```

**✅ PASS if:** Returns all of these with real numbers (not all zeros):
- `total_premium_collected`
- `total_payouts`
- `loss_ratio`
- `auto_approval_rate`

**❌ FAIL if:** All values are `0` or fields are missing

---

**Claude Code prompt:**
```
Call GET /analytics/weekly with Bearer token.
Show me all KPI values returned.
```

</details>

---

<details>
<summary><strong>T22</strong> · Loss ratio is a valid decimal &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to do:** Look at the `loss_ratio` from T21

**✅ PASS if:** `loss_ratio` is a decimal like `0.42` — meaning 42% of premiums were paid out as claims. Should be between `0.30` and `0.70`.

**❌ FAIL if:** `loss_ratio` is `0`, `null`, or greater than `1.0`

---

**Claude Code prompt:**
```
What is the exact loss_ratio value from GET /analytics/weekly?
```

</details>

---

<details>
<summary><strong>T23</strong> · Time series data for chart works &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to run** *(replace `TOKEN`)*:
```bash
curl http://localhost:9001/analytics/time-series \
  -H "Authorization: Bearer TOKEN"
```

**✅ PASS if:** Returns an array of weekly data points — each with a week label, premium amount, and payout amount

**❌ FAIL if:** Empty array `[]` or 404 error

---

**Claude Code prompt:**
```
Call GET /analytics/time-series with Bearer token.
How many data points are returned? Show me the first 2 items.
```

</details>

---

<details>
<summary><strong>T24</strong> · City heatmap data works &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to run** *(replace `TOKEN`)*:
```bash
curl http://localhost:9001/analytics/cities \
  -H "Authorization: Bearer TOKEN"
```

**✅ PASS if:** Returns data for multiple cities (Mumbai, Delhi, Bangalore, etc.) each with their own loss ratio

**❌ FAIL if:** Empty response or only 1 city returned

---

**Claude Code prompt:**
```
Call GET /analytics/cities with Bearer token.
How many cities are returned and what are their individual loss ratios?
```

</details>

---

## Section 8 — ML Model Info

<details>
<summary><strong>T25</strong> · ML model info endpoint works &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to run** *(replace `TOKEN`)*:
```bash
curl http://localhost:9001/policies/ml-model-info \
  -H "Authorization: Bearer TOKEN"
```

**✅ PASS if:** Returns all of:
- Model type: `RandomForestClassifier`
- Accuracy: `85.2%`
- Number of trees: `200`
- A `feature_importances` list

**❌ FAIL if:** 404 error, or accuracy / feature data is missing

---

**Claude Code prompt:**
```
Call GET /policies/ml-model-info with Bearer token.
Show me the full response — especially model accuracy and the feature importances list.
```

</details>

---

<details>
<summary><strong>T26</strong> · Risk profile narrative generates &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to run** *(replace `TOKEN`)*:
```bash
curl http://localhost:9001/policies/risk-profile \
  -H "Authorization: Bearer TOKEN"
```

**✅ PASS if:** Returns a paragraph of text explaining the worker's personal risk — mentions their city, shift, and top 2 risks. Even a rule-based template fallback counts as a pass.

**❌ FAIL if:** Empty string `""` or `null` narrative

---

**Claude Code prompt:**
```
Call GET /policies/risk-profile with the Bearer token.
Show me the exact risk narrative text returned.
```

</details>

---

## Section 9 — PWA Frontend

<details>
<summary><strong>T27</strong> · PWA login screen works &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to do:**
1. Open browser → go to `http://localhost:9001/app`
2. Enter: Phone `9876543210` · Password `demo1234`
3. Click login

**✅ PASS if:** You land on a dashboard screen showing the worker's name and active policy

**❌ FAIL if:** Error message, blank screen, or page stays on login

---

**Claude Code prompt:**
```
Open the PWA at http://localhost:9001/app and login with:
Phone: 9876543210 | Password: demo1234
Describe what appears on screen after login.
```

</details>

---

<details>
<summary><strong>T28</strong> · Dashboard shows active policy details &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to do:** After logging in from T27, look at the main screen

**✅ PASS if:** You can see:
- Policy tier (Basic Shield)
- Days remaining on the policy
- A claim or payout summary card

**❌ FAIL if:** Dashboard is blank or shows errors

---

**Claude Code prompt:**
```
After login, what does the dashboard screen show?
Is there an active policy banner? What information is visible on screen?
```

</details>

---

<details>
<summary>⭐ <strong>T29</strong> · Simulate trigger from frontend — <em>CRITICAL TEST</em> &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

> This is the moment you show judges. Everything happening automatically in front of their eyes.

**What to do:**
1. In the PWA → go to Triggers screen
2. Click "Simulate Trigger"
3. Select: `Heavy Rainfall` + `Bangalore`
4. Click Fire

**✅ PASS if:**
- New trigger appears in the trigger feed immediately
- A new claim auto-appears in the Claims screen — **without you doing anything else**

**❌ FAIL if:** Nothing happens, or trigger fires but no claim appears in Claims

---

**Claude Code prompt:**
```
In the PWA triggers screen, simulate a Heavy Rainfall trigger for Bangalore.
After firing, go to the Claims screen.
Does a new claim appear automatically? What is its status and payout amount?
```

</details>

---

<details>
<summary><strong>T30</strong> · PWA is installable (add to home screen) &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to do:**
1. Open Chrome → go to `http://localhost:9001/app`
2. Look for install icon in the address bar **OR** open browser menu (⋮)

**✅ PASS if:** Browser shows "Install Zynvaro" or "Add to Home Screen" option — you don't need to actually install it, just confirm the option appears

**❌ FAIL if:** No install option appears at all — means `manifest.json` or service worker is broken

---

**Claude Code prompt:**
```
Go to http://localhost:9001/app in Chrome.
Is there an install or add-to-homescreen option visible in the browser?
This confirms the PWA manifest is working.
```

</details>

---

## Section 10 — Full Test Suite

<details>
<summary>⭐ <strong>T31</strong> · All 811 tests pass — <em>CRITICAL TEST</em> &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

> This is what you show judges as proof of production-grade engineering.

**What to run:**
```bash
cd zynvaro-app/backend
pytest -q
```

> ⏱ This takes approximately 75 seconds.

**✅ PASS if:** Final line shows:
```
811 passed in 75.XX seconds
```

**❌ FAIL if:** Any `FAILED` or `ERROR` count appears — note exactly which test files fail

---

**Claude Code prompt:**
```
Run pytest -q in the backend directory.
Show me the final summary line and any failures.
How many passed vs failed?
```

</details>

---

<details>
<summary><strong>T32</strong> · No import errors in any module &nbsp;·&nbsp; <code>[ ] todo</code></summary>

<br>

**What to run:**
```bash
cd zynvaro-app/backend
python -c "import main; print('OK')"
```

**✅ PASS if:** Prints `OK` with no warnings or errors

**❌ FAIL if:** Any `ImportError`, `ModuleNotFoundError`, or Python traceback

---

**Claude Code prompt:**
```
Run: python -c "import main; print('OK')" in the backend folder.
What is the exact output?
```

</details>

---

## Final Sign-Off

Once all 32 tests are marked passed, fill this in:

| Item | Status |
|---|---|
| All 32 tests passing | `[ ]` |
| Live trigger simulation works end-to-end | `[ ]` |
| 3 fraud scenarios visible in admin panel | `[ ]` |
| `pytest -q` → 811 passed | `[ ]` |
| PWA loads and is installable | `[ ]` |
| Demo video recorded | `[ ]` |
| Pitch deck complete | `[ ]` |

> **You are ready to submit when every box above is checked.**

---

*Zynvaro Test Checklist · Team AeroFyta · Guidewire DEVTrails 2026*
