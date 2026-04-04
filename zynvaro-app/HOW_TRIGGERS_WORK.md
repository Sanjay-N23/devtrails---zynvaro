# How Zynvaro Triggers Work

## The Core Idea

Zynvaro uses **parametric insurance** — payouts are triggered automatically when a measurable external event crosses a predefined threshold. No claim forms. No phone calls. No waiting.

---

## PRODUCTION MODE (Without Demo Button)

In a real deployment, the trigger system is **fully autonomous**. No human touches anything.

### How It Works

```
Every 15 minutes (APScheduler):
  For each city with active policies:
    1. Call OpenWeatherMap API → get temperature + rainfall
    2. Call WAQI API → get AQI reading
    3. Probe Blinkit/Zepto/Swiggy → check if platform is down
    4. Query GDELT news API → check for bandh/protest articles
    5. Compare each reading against thresholds
    6. If ANY threshold is breached:
       → Create TriggerEvent in DB
       → Find ALL workers in that city with active policies
       → For each worker:
          → Calculate payout (income-proportional)
          → Run fraud scoring (ML + rules)
          → If score >= 75: AUTO-APPROVE → set status=PAID → credit UPI
          → If score 45-74: PENDING_REVIEW → 2-hour escrow hold
          → If score < 45: MANUAL_REVIEW → admin must decide
```

### Real-World Example

**Monday, July 14, 2026 — Mumbai Monsoon**

```
09:00 AM — Scheduler polls Mumbai
           OpenWeatherMap returns: rain_24h = 78.5 mm
           Threshold for Heavy Rainfall: 64.5 mm
           78.5 > 64.5 → TRIGGER FIRES

09:00 AM — System finds 847 workers in Mumbai with active policies
           Creates 847 claims in < 2 seconds

09:00 AM — Fraud scoring runs on each:
           - 812 workers: city match, clean history → score 100 → PAID instantly
           - 28 workers: 3+ claims this week → score 70 → PENDING_REVIEW
           - 7 workers: city mismatch (registered Delhi, claiming Mumbai) → score 40 → MANUAL_REVIEW

09:01 AM — 812 UPI payouts initiated via Razorpay
           Workers receive money BEFORE they even check the weather

09:01 AM — Push notification (WhatsApp Business API):
           "Heavy Rainfall Alert! Mumbai receiving 78.5mm rain.
            ₹600 Auto-Credited to your UPI. No forms. No waiting."

           THE WORKER DID NOTHING. The money just appeared.
```

### The Worker's Experience

```
Worker wakes up → checks phone → sees WhatsApp message → money is already there
                                                         ↑
                                              They didn't file anything.
                                              They didn't call anyone.
                                              They didn't even open the app.
                                              THAT is parametric insurance.
```

### Data Flow Diagram

```
┌──────────────────────────────────────────────────────────┐
│                    EXTERNAL APIS                          │
│                                                          │
│  OpenWeatherMap ──→ Rain/Temp data                       │
│  WAQI API ────────→ AQI readings                         │
│  HTTP Probes ─────→ Platform up/down                     │
│  GDELT News ──────→ Protest/bandh articles                │
└─────────────┬────────────────────────────────────────────┘
              │ Every 15 min (APScheduler)
              ▼
┌─────────────────────────────┐
│   check_all_triggers(city)  │
│   Runs all 4 APIs in        │
│   parallel (asyncio.gather) │
│   Compares vs thresholds    │
└─────────────┬───────────────┘
              │ If threshold breached
              ▼
┌─────────────────────────────┐
│   TriggerEvent created      │
│   Dedup: skip if same       │
│   type+city within 3hrs     │
│   or event hasn't expired   │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│   _auto_generate_claims()   │
│   For EACH active worker    │
│   in the triggered city:    │
│                             │
│   1. Calculate payout       │
│      (city × tier × trigger │
│       → income-proportional)│
│                             │
│   2. Check weekly cap       │
│      (sum of paid claims    │
│       in last 7 days vs     │
│       max_weekly_payout)    │
│                             │
│   3. Fraud scoring          │
│      Rule-based (100 → -40  │
│      city mismatch, -20     │
│      device fail, etc.)     │
│      ML augmentation        │
│      (RandomForest 85.3%)   │
│                             │
│   4. Decision               │
│      ≥75 → PAID instantly   │
│      45-74 → PENDING        │
│      <45 → MANUAL_REVIEW    │
└─────────────┬───────────────┘
              │
              ▼
┌─────────────────────────────┐
│   Payout via UPI            │
│   (Razorpay API in Phase 3) │
│   Currently: MOCK-UPI ref   │
│                             │
│   WhatsApp notification     │
│   (Business API in Phase 3) │
│   Currently: in-app modal   │
└─────────────────────────────┘
```

---

## DEMO MODE (With Simulate Button)

Since we can't control the weather, the app has a **Simulate Trigger** button for demos and judges.

### How It Works

```
Judge clicks "Fire Trigger & Auto-Generate Claims"
  │
  ├── Frontend sends: POST /triggers/simulate
  │   Body: { "trigger_type": "Heavy Rainfall", "city": "Bangalore" }
  │
  ├── Backend validates trigger_type (must be one of 6 valid types)
  ├── Backend validates city (must be one of 7 supported cities)
  │
  ├── Creates a TriggerEvent with demo values:
  │   Heavy Rainfall → measured_value: 72.5 mm (above 64.5 threshold)
  │   Extreme Rain   → measured_value: 210.0 mm
  │   Heatwave       → measured_value: 46.2°C
  │   Hazardous AQI  → measured_value: 485 AQI
  │   Platform Outage→ measured_value: 20.0 min
  │   Civil Disruption→ measured_value: 6.0 hours
  │
  ├── Background task: _auto_generate_claims()
  │   Same EXACT pipeline as production:
  │   - Finds all active workers in that city
  │   - Calculates income-proportional payout
  │   - Runs fraud scoring (ML + rules)
  │   - Creates claims with appropriate status
  │
  ├── Frontend waits 1.5s → 3.5s → 6.5s (retry polling)
  │   Fetches GET /claims/ looking for newest claim
  │
  └── WhatsApp-style modal appears:
      "Heavy Rainfall Alert! Bangalore receiving heavy rain.
       ₹300 Auto-Credited. CLM-XXXXXXXX.
       No forms. No calls. No waiting."
```

### Demo vs Production — What's Different?

| Aspect | Demo (Simulate) | Production (Autonomous) |
|---|---|---|
| **Who triggers it** | Judge clicks a button | APScheduler every 15 min |
| **Data source** | Hardcoded demo values | Live API data (OpenWeatherMap, WAQI, GDELT, HTTP probes) |
| **Claim pipeline** | IDENTICAL — same fraud scoring, same payout calc | IDENTICAL |
| **Payout** | Mock UPI reference | Real Razorpay UPI payout (Phase 3) |
| **Notification** | In-app WhatsApp-style modal | WhatsApp Business API (Phase 3) |
| **Dedup** | 24-hour same-trigger protection | 3-hour event dedup + 24-hour claim dedup |

### What's IDENTICAL in Both Modes

The simulate button is NOT a shortcut or a fake. It runs the EXACT same code path:

1. Same `_auto_generate_claims()` function
2. Same `compute_authenticity_score()` fraud scorer
3. Same `get_payout_amount()` income-proportional calculator
4. Same weekly payout cap enforcement
5. Same ML RandomForest augmentation
6. Same claim status transitions (PAID / PENDING / MANUAL_REVIEW)

The ONLY difference is the data source: demo uses hardcoded values, production uses live APIs.

---

## The 6 Triggers We Monitor

| # | Trigger | Threshold | Real API | What Happens |
|---|---|---|---|---|
| 1 | Heavy Rainfall | 64.5 mm/24hr | OpenWeatherMap | Bikes can't safely operate |
| 2 | Extreme Rain / Flooding | 204.5 mm/24hr | OpenWeatherMap | City-wide shutdown |
| 3 | Severe Heatwave | 45°C | OpenWeatherMap | Outdoor work is dangerous |
| 4 | Hazardous AQI | 400 AQI | WAQI API | Government advisory against outdoor work |
| 5 | Platform Outage | 15 min down | HTTP HEAD probe | Zero orders flow |
| 6 | Civil Disruption | 4 hours | GDELT Project | Bandh/protest/curfew |

---

## Why This Matters for Judges

**The simulate button proves the ENTIRE pipeline works end-to-end:**

1. Click one button
2. Trigger event created in DB
3. ALL workers in that city get claims auto-generated
4. Each claim is individually fraud-scored
5. Clean workers get paid INSTANTLY
6. Suspicious workers get flagged for review
7. Weekly payout caps are enforced
8. The WhatsApp modal shows the exact payout amount

**In production, this same pipeline runs every 15 minutes with zero human intervention.** The simulate button just lets you see it happen in real-time during a 5-minute demo.
