# ZYNVARO
## AI-Powered Parametric Income Shield for India's Gig Workers

**Team:** AeroFyta
**Event:** Guidewire DEVTrails 2026 — University Hackathon
**Theme:** Seed · Scale · Soar

---

# EXECUTIVE SUMMARY

India has over **2 million Q-Commerce delivery workers** — the riders who deliver groceries in 10 minutes on Blinkit, Zepto, and Instamart. They earn ₹800–₹1,450 per day and lose 20–30% of their monthly income to events entirely outside their control: monsoon rains that ground their bikes, hazardous AQI days that make outdoor work unsafe, bandhs that seal city streets, and platform outages that kill order flow overnight.

**Zero insurance products exist today that address this.**

**Zynvaro** is a full-stack AI-powered parametric income insurance platform that detects qualifying disruptions in real time, automatically generates claims for every affected worker, scores each claim using a RandomForest ML fraud model, and triggers instant UPI payouts — with no paperwork, no manual filing, and no waiting.

When it rains 65mm in Mumbai, every Blinkit rider with a Zynvaro policy gets their ₹600 within minutes. Automatically. Without touching their phone.

This is parametric insurance done correctly — and we've built it end to end, tested it with 748 automated tests, and wired it to live external APIs from OpenWeatherMap, WAQI, and the GDELT Project.

---

# PART 1 — THE HACKATHON CONTEXT

## 1.1 The Challenge

**Guidewire DEVTrails 2026** challenged university teams to:

> *"Build an AI-enabled parametric insurance platform that safeguards gig workers against income loss caused by external disruptions such as extreme weather or environmental conditions. The solution should provide automated coverage and payouts, incorporate intelligent fraud detection mechanisms, and operate on a simple weekly pricing model aligned with the typical earnings cycle of gig workers."*

## 1.2 The Constraints We Must Honor

| Constraint | Our Compliance |
|---|---|
| **Income loss ONLY** — no health, accident, vehicle | Every trigger and payout maps strictly to lost working hours and income replacement. The word "health" or "accident" appears nowhere in our coverage logic. |
| **Weekly pricing model** — matches gig worker pay cycles | Policies are exactly 7 days. Premiums, payouts, deductibles all structured weekly. |
| **Persona: Q-Commerce delivery workers** | Built for Blinkit, Zepto, Instamart, Swiggy, Zomato riders specifically — income tables, risk profiles, trigger thresholds all calibrated to this persona. |

## 1.3 Hackathon Timeline

| Phase | Dates | Theme | Our Status |
|---|---|---|---|
| **Phase 1** | Mar 4–20, 2026 | Ideate & Know Your Delivery Worker | ✅ Submitted |
| **Phase 2** | Mar 21–Apr 4, 2026 | Protect Your Worker | ✅ Complete |
| **Phase 3** | Apr 5–17, 2026 | Perfect for Your Worker | ✅ Complete |

## 1.4 Judging Criteria (What We're Optimizing For)

Based on the problem statement, judges will evaluate:
- **Innovation** — is the AI genuinely powering the product?
- **Completeness** — does every feature in the spec actually work?
- **Business viability** — can this become a real product?
- **Technical depth** — production-quality code, not demos?
- **Real-world applicability** — does it solve the actual worker's problem?

---

# PART 2 — THE PROBLEM

## 2.1 Who We Are Protecting

**Q-Commerce delivery partners** — 2-wheeler riders working for Blinkit, Zepto, Instamart, Swiggy Instamart, and Zomato in India's tier-1 cities.

**Their reality:**
- Earn ₹800–₹1,450 per day (₹18,000–₹35,000/month) — entirely dependent on completing deliveries
- Work solo, without any employer safety net, PF, ESI, or paid leave
- Are classified as "platform gig workers" — not employees — so no labor protections apply
- Lose income the moment an external disruption stops them from working

## 2.2 The Six Income Killers

These six events erase their daily earnings. None of them are the worker's fault. And today, none of them are insured.

| Disruption | Threshold | Real Impact |
|---|---|---|
| **Heavy Rainfall** | ≥ 64.5 mm/24hr | Bikes cannot safely operate, orders dry up, platforms suspend delivery |
| **Extreme Rain / Flooding** | ≥ 204.5 mm/24hr | City-wide shutdown — roads flooded, zero deliveries possible |
| **Severe Heatwave** | ≥ 45°C | Outdoor riding is a health emergency — workers stop to survive |
| **Hazardous AQI** | ≥ 400 AQI | Government advisory against outdoor work — Delhi winters, Diwali period |
| **Platform Outage** | > 15 min down | Zero orders flow — worker is idle even if conditions are perfect |
| **Civil Disruption** | ≥ 4 hrs restricted | Bandh, Section 144, communal tension — city zones sealed |

## 2.3 The Income Loss Math

A standard Blinkit rider in Bangalore (Standard Guard tier) earns ₹1,150/day.

- **One heavy rain day** = ₹1,150 gone
- **One AQI spike** = ₹1,150 gone
- **One bandh** = ₹1,150 gone

Over a month with 2–3 such events (entirely normal in monsoon season):
- **₹2,300–₹3,450 lost** — 15–25% of monthly income
- **No recourse, no compensation, no safety net**

## 2.4 Why Nothing Exists Today

| Existing Product | Why It Doesn't Work |
|---|---|
| Traditional health insurance | Covers medical expenses — not income loss from weather |
| Vehicle insurance | Covers bike damage — not lost earnings from not riding |
| Pradhan Mantri Jeevan Jyoti Bima | Life insurance only — zero relevance to daily income loss |
| Platform accident cover (Blinkit, Zepto) | Only on-duty accidents — not weather, AQI, or bandhs |
| Crop parametric insurance (existing) | For farmers — entirely different persona and triggers |

**The gap is real, the market is enormous, and nobody has built this yet.**

---

# PART 3 — OUR SOLUTION: ZYNVARO

## 3.1 The Name and Brand

**Zynvaro** = *Zynergy* (synergy of protection) + *Varo* (guard/shield in Latin root)

**Tagline:** *"Your Income. Protected. Automatically."*

**Design philosophy:** Dark, minimal, mobile-first — designed for a ₹12,000 Android phone, not a laptop. MetaMask-inspired dark UI (dark blue `#0f172a`, orange `#ff6b35` accents) — because gig workers live on their phones.

## 3.2 The Core Promise

> When a qualifying disruption hits your city, Zynvaro detects it, files your claim, fraud-checks it, and sends you your payout. All while you're waiting out the rain. No forms. No photos. No waiting.

## 3.3 The Product in One Sentence

**Zynvaro is a weekly parametric income insurance PWA for Q-Commerce delivery partners that auto-detects city-level disruptions via live APIs, auto-generates fraud-scored claims for all policyholders in the affected city, and processes instant UPI payouts within seconds of trigger validation.**

---

# PART 4 — THE WORKER JOURNEY (END TO END)

This section shows what Zynvaro looks like for a real worker — Ravi Kumar, a Blinkit rider in Bangalore.

## Step 1: Onboarding (< 60 Seconds)

Ravi opens Zynvaro's PWA on his phone. He registers with:
- Name, phone number, password
- City (Bangalore), pincode (560047), platform (Blinkit)
- Working shift (Evening Peak, 6PM–2AM), vehicle type (2-Wheeler)

**What Zynvaro does instantly:**
- Computes his zone risk score from pincode-level actuarial data (`0.62` for 560047)
- Generates quotes for all three tiers with full premium breakdown
- Issues a JWT auth token — Ravi is immediately authenticated and protected

## Step 2: Choosing a Policy

Ravi sees three options:

| | Basic Shield | Standard Guard | Pro Armor |
|---|---|---|---|
| **Weekly Premium** | ₹36/week | ₹56/week | ₹109/week |
| **Max Daily Payout** | ₹300 | ₹600 | ₹1,000 |
| **Max Weekly Payout** | ₹600 | ₹1,200 | ₹2,000 |
| **Rain Replacement** | 35% of daily income | 55% of daily income | 70% of daily income |

Below each card: a human-readable AI explanation of *why* his premium is what it is:

> *"📍 Bangalore zone has moderate risk profile. ☀️ Low-risk season — no seasonal adjustment. ✅ No claim history — clean record."*

Ravi picks **Basic Shield** — ₹36/week fits his budget. He's now covered for 7 days.

## Step 3: A Disruption Occurs (Ravi Does Nothing)

It's a Tuesday evening. A heavy monsoon downpour hits Bangalore — 71mm in 24 hours, exceeding the 64.5mm threshold.

**Zynvaro's autonomous system:**

```
15-minute scheduler fires → checks OpenWeatherMap for all 7 cities
↓
Bangalore: rain_24h = 71mm > 64.5mm threshold → TRIGGER FIRES
↓
Find all active policies in Bangalore → includes Ravi
↓
Compute payout: ₹950/day (Bangalore Basic Shield) × 35% = ₹332 → rounded to ₹330
↓
Run fraud scoring: Ravi's city = Bangalore = trigger city → 100 score → AUTO_APPROVED
↓
Create claim CLM-XXXXXXXXX, status = AUTO_APPROVED
↓
Set paid_at = NOW, payment_ref = "MOCK-UPI-CLM-XXXXXXXXX"
```

**Time from trigger detection to claim paid: < 1 second.**

Ravi receives a push notification: *"⚡ ₹330 payout processed for Heavy Rainfall in Bangalore. Claim CLM-XXXXXXXXX auto-approved."*

He never opened the app. He never filed anything. He never waited.

## Step 4: Ravi Checks His Dashboard

When the rain stops, Ravi opens the app and sees:
- Active policy banner: Basic Shield, 4 days remaining
- Recent claim card: CLM-XXXXXXXXX, ✅ Auto-Approved, ₹330, paid at 7:43 PM
- Authenticity score: 100/100 — all signals green
- His disruption streak reset to 0 (a new streak begins)

## Step 5: Policy Renewal

After 7 days, his policy expires. One tap → renewed for another week. Premium recalculated at current risk factors (same week = same premium). If he's built a streak by next renewal, the premium drops.

---

# PART 5 — THE COMPLETE ZYNVARO PRODUCT VISION

## 5.1 What We've Built (Current State — Phase 2)

This is not a prototype. It's a production-grade application.

### Backend (FastAPI + Python 3.11 + SQLite)
- **30 API endpoints** across 5 routers
- **5 database models** (Worker, Policy, TriggerEvent, Claim, PayoutTransaction) with full schema
- **6 parametric triggers** with live API integrations
- **Autonomous scheduler** — polls all 7 cities every 15 minutes
- **ML-enhanced fraud scoring** — RandomForestClassifier (200 trees, 85.2% accuracy)
- **748 automated tests** — 100% pass rate

### Frontend (Progressive Web App)
- **Installable PWA** — add to home screen, offline shell
- **5 screens** — Auth, Dashboard, Policy, Claims, Triggers
- **Dark MetaMask-style UI** — optimized for ₹12,000 Android phones
- **Zero hardcoded URLs** — works on any port/domain

### AI & ML Layer
| Component | Technology | What It Does |
|---|---|---|
| Premium pricing | Actuarial 5-factor model | Zone × Seasonal × Claims × Streak × Forecast |
| Fraud detection | RandomForestClassifier | 10 features, 85.2% accuracy, probabilistic scoring |
| Risk profiling | Zone risk + city income data | Pincode-level pricing for 30 pincodes |
| Income replacement | Actuarial replacement rates | City × Tier × Trigger → exact payout |
| Risk narrative | Anthropic Claude API (+ template fallback) | Personalized 3-sentence risk explanation |

### Live API Integrations
| Service | Purpose | Status |
|---|---|---|
| OpenWeatherMap | Rain, heatwave triggers | 🟡 Wired, needs free key |
| WAQI (World AQI) | AQI trigger | 🟡 Wired, needs free token |
| GDELT Project v2 | Civil disruption (news) | ✅ Live, no key needed |
| HTTP HEAD probes | Platform outage (Blinkit, Zepto, Zomato) | ✅ Live, tested working |

## 5.2 Phase 3 — What We Built (April 5–17) ✅

### Advanced Fraud Detection (6-Module Engine) ✅
- **GPS geofencing** — haversine distance calculation, 7 city zones with configurable radii (25-40km)
- **Shift-time validation** — 5 shift windows with midnight crossing + 1hr grace period
- **Historical weather cross-validation** — median-based anomaly detection on last 5 triggers
- **Velocity anomaly detector** — impossible travel detection between cities (>200km/h flagged)
- **Behavioral pattern analyzer** — frequency vs platform average, repeat offender escalation
- **Cross-claim deduplicator** — trigger event dedup + UPI fraud ring detection across workers
- **14-feature ML model v2** — RandomForest (3,000 samples, per-claim explanations, 79% synthetic accuracy)

### Instant Payout System (Razorpay Test Mode) ✅
- Razorpay Payment Links API integration — real `rzp.io` URLs created per claim
- PayoutTransaction lifecycle: INITIATED -> PENDING -> SETTLED/FAILED
- Webhook handler (`POST /webhooks/razorpay`) for payment status callbacks
- Graceful mock fallback when Razorpay keys not configured
- 34 automated payout tests + 30 hard edge cases passing

### Intelligent Dashboard ✅
- **Worker view:** "Total Earnings Protected" shield widget, weekly coverage progress bar (green/yellow/red), claims/disruptions/premiums stats
- **Admin view:** EWMA predictive forecast, SVG sparkline chart, per-trigger risk forecast, per-city risk pills with seasonal multipliers, fraud detection analytics (6-module breakdown)

### Premium Engine Hardened (from external code audit) ✅
- Per-claim ML explanations (replaces misleading global importances)
- City-aware seasonal uplift (winter haze only for Delhi/Kolkata, not all cities)
- Local RNG for zone risk (no global np.random.seed mutation)
- Hard ValueError on unknown tier (no silent fallback to Standard Guard)

### Test Suite: 858 automated tests (100% pass rate) ✅

## 5.3 The Complete End Product (Post-Hackathon Vision)

This is what Zynvaro looks like as a real commercial product in 12 months:

### Product Layer
| Component | Current | Full Product |
|---|---|---|
| Frontend | PWA (app.html) | React Native mobile app (iOS + Android) |
| Notifications | Push via PWA | WhatsApp Business API — instant payout alert |
| Language | English | Hindi, Tamil, Telugu, Kannada, Bengali |
| Onboarding | 60-second web form | Aadhaar eKYC + face match (DIGI Locker) |
| Payment | Mock UPI reference | Live Razorpay / PhonePe UPI payout (< 5 seconds) |
| Policy management | Weekly manual renew | Auto-renew with wallet deduction |

### Insurance Layer
| Component | Current | Full Product |
|---|---|---|
| Cities | 7 (Mumbai, Delhi, Bangalore, Hyderabad, Chennai, Pune, Kolkata) | 50+ cities |
| Platforms | 7 (Blinkit, Zepto, Instamart, Zomato, Swiggy, Amazon, Flipkart) | All major platforms including Dunzo, Shadowfax, Borzo |
| Trigger sources | 4 (OWM, WAQI, GDELT, HTTP probe) | 8+ (IMD SACHET, CPCB SAMEER, Bing News, Downdetector) |
| Pricing | 3 tiers | Dynamic micro-tiers (₹15–₹150/week) based on shift, pincode, historical claims |
| Payout | Instant mock UPI | Guaranteed < 5-minute real UPI payout |
| Reinsurance | None | Partnership with Swiss Re or Munich Re parametric desk |

### Business Integration Layer
| Component | Current | Full Product |
|---|---|---|
| Enrollment | Self-service PWA | **Platform-embedded:** Blinkit/Zepto enrolls workers at onboarding |
| Premium collection | Manual | Auto-deducted from weekly platform earnings payout |
| Trigger validation | Live APIs | Additional: IMD station data, NDMA alerts, platform event webhooks |
| Fraud detection | ML model | + GPS device location at claim time, platform activity logs |
| Regulatory | Demo-ready | IRDAI InsurTech Sandbox registration → full license |

### The Platform Integration Story (The Real Scale Driver)

The breakthrough model: **Zynvaro as a white-label B2B2C platform.**

> Instead of acquiring 2 million workers individually, Zynvaro signs one contract with Blinkit/Zepto. The platform collects premiums as a weekly deduction from rider payouts. Claims auto-fire city-wide with zero worker action. Payouts credited to the same bank account where earnings land.

This makes Zynvaro **invisible infrastructure** — workers are protected without ever thinking about insurance. This is the future of gig worker social protection.

---

# PART 6 — THE AI & ML LAYER (DEEP DIVE)

This is what makes Zynvaro genuinely AI-powered — not AI as a buzzword, but AI doing work that a rule book cannot.

## 6.1 Dynamic Premium Engine (Actuarial AI)

**5-factor multiplicative pricing model:**

```
weekly_premium = base × zone_factor × seasonal_factor × claim_factor × forecast_factor − streak_discount
```

**What's intelligent about this:**
- **Zone factor** is derived from pincode-level actuarial risk data — not city-average guesses. Pincode 400051 (Worli, Mumbai) scores 0.88 vs 400070 (Chembur) at 0.79 — same city, different risk.
- **Seasonal factor** peaks at 1.6× during peak monsoon (week 32 = early August) — this is calendar-aware pricing that no static rate table can replicate.
- **Streak discount** means loyal, low-claim workers pay less over time — a real behavioral incentive no traditional insurer applies to gig workers.
- **Forecast adjustment** — when live weather APIs report high forecast risk, premiums tick up at next renewal. Real-time risk-responsive pricing.

**SHAP-style explainability** — every quote explains itself in plain language a delivery rider can understand.

## 6.2 ML Fraud Detection (RandomForestClassifier)

**The problem:** Hard-coded rules like `if city_match: score -= 40` cannot capture complex fraud patterns. A fraudulent claim from a loyal 12-week streak worker with city match should be treated differently from a new worker with city match. Rules don't understand this. ML does.

**Our model:**
- **Algorithm:** RandomForestClassifier (200 trees, max_depth=8, class_weight=balanced)
- **Training data:** 2,000 synthetic gig-worker claim scenarios with realistic fraud distribution
- **Features (10-dimensional):**

| Feature | What It Captures |
|---|---|
| City match | GPS fraud — is worker in the trigger zone? |
| Device attestation | Device integrity check |
| Same-week claims | Frequency anomaly — claiming multiple events in one week |
| Claim history (normalized) | Pattern-based risk loading |
| Hour of day | Late-night submissions are statistically more suspicious |
| Trigger type encoded | Some trigger types have higher fraud rates (civil disruption > rainfall) |
| Payout amount | Higher-payout claims attract more fraud attempts |
| Disruption streak | Long-loyal workers de-risked — 12+ week streaks have near-zero fraud probability |
| City × Device (interaction) | Non-linear: both valid = strong legitimacy signal |
| Mismatch × Frequency (interaction) | Non-linear: city mismatch + high frequency = extreme fraud risk |

**Validation accuracy: 85.2%** on held-out test set

**Fraud probability examples:**
- Perfect claim (city match, device valid, clean history, loyal) → **2.7% fraud probability**
- All bad signals (mismatch, no device, 4 same-week, 15 history) → **88.7% fraud probability**

**Architecture decision:** Rule-based score drives the decision (insurance compliance, auditability). ML score is surfaced as an augmentation signal for the admin panel — showing `ml_fraud_probability`, `ml_confidence`, and `ml_top_signals` for every claim. This hybrid approach is correct for regulated insurance: deterministic rules for compliance, ML for deeper pattern recognition.

## 6.3 Income-Replacement Payout Engine

**The innovation:** Payouts are not flat amounts. They are **proportional to actual estimated daily income loss**.

```
payout = city_daily_income[worker.city][policy.tier] × replacement_rate[trigger_type][policy.tier]
```

This means:
- A Mumbai Pro Armor worker (estimated ₹1,400/day) gets ₹980 for a flood day (70% replacement)
- A Delhi Basic Shield worker (estimated ₹850/day) gets ₹300 for the same flood day (35% replacement, capped)

**Why this matters:** Flat payouts are unfair. A ₹300 payout means much more to a Kolkata Basic Shield rider (₹750/day) than to a Bangalore Pro Armor rider (₹1,450/day). Income-proportional payouts align the product with the actual income loss suffered.

## 6.4 LLM-Powered Risk Profiler (Anthropic Claude)

When a worker logs in, `GET /policies/risk-profile` generates:
- A personalized 3-sentence risk narrative (Claude if API key set, rule-based template if not)
- Seasonal alert specific to current week
- Top 2 risks for their city and peak months
- Tier upgrade tip with specific rupee savings/upgrade cost

Example (Delhi, Blinkit, Evening Peak, Standard Guard):
> *"As a Blinkit delivery partner in Delhi's moderate-high risk zone (risk score 0.72), your primary exposure is Hazardous AQI — which peaks between November and February when Delhi's air quality regularly crosses the 400 AQI threshold that triggers your income protection. During your Evening Peak shift, late-night AQI spikes are a particular concern, but your Standard Guard plan covers 55% of your daily income (₹577/day) the moment the threshold is crossed, with zero paperwork. Your 5-week disruption-free streak has already earned you a 10% loyalty discount — 1 more clean week and a second tier of discount unlocks."*

---

# PART 7 — PARAMETRIC TRIGGER SYSTEM (TECHNICAL DEEP DIVE)

## 7.1 Why Parametric?

Traditional insurance: Worker must prove they couldn't work, submit evidence, wait for adjuster, fight rejection. Takes weeks. Worker needs money in hours.

**Parametric insurance:** Agreed threshold crossed → payout fires automatically. No proof required. No dispute possible. Contract is mathematical, not subjective.

This is why parametric is the only viable model for gig worker income insurance — and why it requires live external data feeds, not manual processes.

## 7.2 Trigger Architecture

```
Every 15 minutes (APScheduler):
│
├── For each of 7 cities with active policies:
│   ├── OpenWeatherMap API → rain_24h, temp
│   ├── WAQI API → AQI reading
│   ├── GDELT API → civil disruption article count
│   └── HTTP HEAD → platform reachability
│
├── Compare each reading to threshold
├── If threshold crossed: create TriggerEvent, call _auto_generate_claims()
├── 3-hour deduplication: same trigger+city won't fire twice in 3 hours
└── Exception isolation: one city failing doesn't stop others
```

## 7.3 Dual-Source Design

Every trigger stores both a primary and secondary data source — mirroring the dual-oracle architecture used in enterprise parametric insurance to prevent single-source manipulation:

| Trigger | Primary Source | Secondary Source |
|---|---|---|
| Heavy Rainfall | OpenWeatherMap (live) | IMD API (mock / planned) |
| Extreme Flooding | OpenWeatherMap (live) | NDMA SACHET (mock / planned) |
| Severe Heatwave | OpenWeatherMap (live) | IMD Bulletins (mock / planned) |
| Hazardous AQI | WAQI API (live) | CPCB SAMEER (mock / planned) |
| Platform Outage | HTTP HEAD probe (live) | Downdetector (planned) |
| Civil Disruption | GDELT v2 (live) | NewsAPI (planned) |

## 7.4 The Zero-Touch Claim Pipeline

```
TRIGGER VALIDATED
       │
       ▼
Find workers: active policy + city match + policy not expired
       │
       ▼
For each worker:
  ├── Get payout = income[city][tier] × rate[trigger][tier]
  ├── Skip if payout = 0 (trigger not covered by this tier)
  ├── Skip if duplicate: same trigger type claimed in last 24h
  ├── Count same-week claims → fraud frequency signal
  ├── compute_authenticity_score()
  │     ├── Rule-based: city, device, frequency, history → 0-100
  │     └── ML-augmented: RandomForest fraud probability surfaced
  ├── Decision:
  │     ├── Score ≥ 75 → AUTO_APPROVED + paid_at = NOW + payment_ref set
  │     ├── Score 45–74 → PENDING_REVIEW (2-hour escrow)
  │     └── Score < 45 → MANUAL_REVIEW (24-hour human review)
  ├── Create Claim record with all signals stored
  └── Update: claim_history_count++, disruption_streak = 0
       │
       ▼
WORKER NOTIFIED (push / WhatsApp)
PAYOUT PROCESSED (mock UPI / Razorpay test mode in Phase 3)
```

---

# PART 8 — BUSINESS MODEL

## 8.1 Revenue Model

Zynvaro earns revenue through **premium collection** on a weekly cycle.

| Tier | Weekly Premium | Monthly Revenue per Worker | Annual Revenue per Worker |
|---|---|---|---|
| Basic Shield | ₹36/week | ₹144/month | ₹1,728/year |
| Standard Guard | ₹49–72/week | ₹200–288/month | ₹2,400–₹3,456/year |
| Pro Armor | ₹89–143/week | ₹356–572/month | ₹4,272–₹6,864/year |

**Target:** 50,000 workers in Year 1 (2.5% of addressable market)

**Revenue at scale:**
- 50,000 workers × ₹200/month average = **₹10 crore ARR (Year 1)**
- 500,000 workers × ₹250/month average = **₹150 crore ARR (Year 3)**

## 8.2 Loss Ratio Management

**Expected loss ratio:** 35–50% (industry benchmark for parametric products: 40–55%)

Why parametric insurance has better loss ratios than traditional:
- No claims adjusters — zero processing cost
- No fraud ambiguity — threshold is objective
- No moral hazard — workers cannot make it rain
- ML fraud detection — reduces fraudulent claims before payout

**Unit economics (Standard Guard, average worker):**
- Premium collected: ₹200/month
- Expected payout: ₹70–100/month (2 disruption events × ₹550 payout)
- Gross margin per worker: ₹100–130/month before operational cost

## 8.3 Go-to-Market Strategy

**Phase 1 (Pilot):** Direct-to-worker PWA. Target 1,000 workers in Bangalore and Mumbai through delivery worker communities and WhatsApp groups.

**Phase 2 (Platform Partnership):** Sign B2B agreements with 1–2 platforms (Blinkit/Zepto). Premium auto-deducted from weekly earnings. Target: 50,000 enrolled workers.

**Phase 3 (Scale):** White-label API for any gig platform in India to embed income protection. Zynvaro becomes the insurance infrastructure layer, not just an app.

## 8.4 Regulatory Path

- **Year 1:** Operate under IRDAI's InsurTech Sandbox framework (allows limited pilot with real policies)
- **Year 2:** Full IRDAI registration as a micro-insurance provider
- **Year 3:** Tie-up with existing licensed insurer (ICICI Lombard / New India Assurance) for underwriting — Zynvaro is the tech + distribution layer

---

# PART 9 — COMPETITIVE LANDSCAPE

## 9.1 Why No One Has Built This

| Competitor Type | Example | Why They Don't Cover Gig Income Loss |
|---|---|---|
| Traditional health insurer | Star Health, Niva Bupa | Medical expenses only — income loss not their product |
| Government schemes | PMJJBY, PMSBY | Life + accident only. No parametric component. |
| Fintech lenders | KreditBee, MoneyTap | Lend against income — don't insure it |
| Crop parametric insurance | Agriculture Insurance Co. | Completely different persona and trigger set |
| Platform accident insurance | Blinkit/Zepto embedded | On-duty accidents only — not weather/AQI/bandh |
| International | Parametrix (US), Arbol (US) | Business interruption focused, not gig worker income |

**The gap is entirely unserved.** No company in India has built parametric income insurance for gig workers.

## 9.2 Zynvaro's Moats

| Moat | Description |
|---|---|
| **Data moat** | First-mover accumulates city × trigger × claim data — future ML models get better every month |
| **Platform lock-in** | Once embedded in Blinkit/Zepto onboarding, switching cost is high |
| **Actuary-grade pricing** | City × pincode × shift × platform income tables are expensive to replicate |
| **Network effects** | More policyholders per city → better trigger validation (wisdom-of-crowd location data) |
| **Regulatory head start** | IRDAI sandbox registration takes 6 months — early movers have 1-year advantage |

---

# PART 10 — TECHNICAL ARCHITECTURE

## 10.1 Full Stack Overview

```
┌─────────────────────────────────────────────────────────┐
│                    ZYNVARO PLATFORM                     │
├─────────────────────────────────────────────────────────┤
│  FRONTEND — Progressive Web App                         │
│  ├── app.html (1,943 lines) — Vanilla JS SPA            │
│  ├── sw.js — Service Worker (offline shell)             │
│  └── manifest.json — PWA installable                    │
├─────────────────────────────────────────────────────────┤
│  BACKEND — FastAPI (Python 3.11)                        │
│  ├── 30 REST API endpoints                              │
│  ├── JWT authentication (HS256, 7-day tokens)           │
│  ├── APScheduler (15-min autonomous polling)            │
│  ├── Auto-seed demo data on first boot                  │
│  └── Graceful shutdown with scheduler cleanup           │
├─────────────────────────────────────────────────────────┤
│  AI / ML LAYER                                          │
│  ├── ml/premium_engine.py — 5-factor actuarial model    │
│  ├── ml/fraud_model.py — RandomForestClassifier         │
│  └── services/risk_explainer.py — LLM risk narratives   │
├─────────────────────────────────────────────────────────┤
│  TRIGGER ENGINE                                         │
│  ├── services/trigger_engine.py — 6 triggers + fraud    │
│  └── services/orchestrator.py — city polling loop       │
├─────────────────────────────────────────────────────────┤
│  DATA LAYER                                             │
│  └── SQLite (zynvaro.db) — 5 tables, FK-safe schema     │
├─────────────────────────────────────────────────────────┤
│  EXTERNAL APIs                                          │
│  ├── OpenWeatherMap — rain, temperature                 │
│  ├── WAQI — air quality index                           │
│  ├── GDELT Project v2 — civil disruption news           │
│  └── HTTP HEAD — platform reachability                  │
└─────────────────────────────────────────────────────────┘
```

## 10.2 Technology Choices (and Why)

| Choice | Technology | Why |
|---|---|---|
| Backend | FastAPI | Async-native, auto-OpenAPI docs, Python 3.11 type hints |
| Database | SQLite (dev) / PostgreSQL (prod) | Zero-config for demo; schema is production-ready |
| ML | scikit-learn | Pre-installed, deterministic, explainable — correct for regulated insurance |
| Auth | JWT + pbkdf2_sha256 | Industry standard; no sessions = stateless = horizontally scalable |
| Scheduler | APScheduler AsyncIOScheduler | Non-blocking, integrates with FastAPI event loop |
| Frontend | Vanilla JS PWA | Zero build step, works on any phone, 6MB total bundle |
| HTTP Client | httpx | Async-native, timeout control, cleaner than requests for API calls |
| LLM | Anthropic Claude (haiku) | Cheapest frontier model, template fallback = zero cost in demo |

## 10.3 Database Schema

**5 tables, full relational integrity:**

```
workers ─────────────────────────────── one Worker
   │                                      has many Policies, Claims
   │
policies ────────────────────────────── one Policy
   │                                      has many Claims
   │
trigger_events ──────────────────────── one TriggerEvent
   │                                      spawns many Claims
   │
claims ──────────────────────────────── one Claim
   │                                      has many PayoutTransactions
   │
payout_transactions ─────────────────── full UPI lifecycle
   └── 6 states: INITIATED → PENDING → SETTLED → FAILED → REVERSED → RETRYING
```

## 10.4 API Endpoints (30 Total)

| Router | Endpoints | Purpose |
|---|---|---|
| `/auth` | register, login, me | JWT auth, worker profile |
| `/policies` | quote/all, quote, create, active, list, renew, cancel, risk-profile, ml-model-info | Full policy lifecycle + AI features |
| `/triggers` | list, live-check, simulate, types | Trigger feed + demo simulation |
| `/claims` | list, get, stats, admin/all, admin/claim | Claim history + admin panel |
| `/analytics` | weekly, time-series, cities | KPI dashboard + heatmap |

## 10.5 Test Coverage

| Metric | Value |
|---|---|
| Total tests | 748 |
| Pass rate | 100% |
| Test files | 19 |
| Code coverage | ~86% |
| ML model tests | 49 (accuracy, features, fraud thresholds) |
| Security tests | 29 (JWT, auth, injection, ownership) |
| Pipeline tests | 35 (zero-touch claim generation) |

---

# PART 11 — PHASE 3 COMPLETION PLAN

## Phase 3 Delivery Status

| Deliverable | Status | Delivered |
|---|---|---|
| Advanced GPS fraud detection (6 modules) | ✅ Complete | April 13 |
| Razorpay test mode payout integration | ✅ Complete | April 13 |
| Worker dashboard (earnings protected) | ✅ Complete | April 14 |
| Admin predictive analytics (EWMA forecast) | ✅ Complete | April 14 |
| Premium engine hardening (12 fixes from audit) | ✅ Complete | April 14 |
| 858 automated tests (100% pass) | ✅ Complete | April 14 |
| Demo video (5 minutes, screen capture) | Pending | April 16 |
| Pitch deck (10 slides, PDF export) | Pending | April 16 |
| Submission | Pending | April 17 |

## Phase 3 Technical Additions — All Delivered

### 1. Advanced Fraud Detection (6-Module Engine)
- **GPS geofencing** — haversine distance, 7 cities with radius zones (25-40km), IN_ZONE/EDGE_ZONE/OUTSIDE_ZONE
- **Shift-time validation** — 5 windows with midnight crossing, +-1hr grace, off-hours = -20 score
- **Historical weather cross-check** — median anomaly (>3x or <0.3x historical triggers)
- **Velocity anomaly** — inter-city travel speed (>200km/h = impossible, >80km/h = suspicious)
- **Behavioral patterns** — frequency vs platform avg, repeat offender (3+ flags), escalation detection
- **Cross-claim dedup** — trigger event dedup + UPI fraud ring detection
- **ML v2** — 14-feature RandomForest, 3000 samples, per-claim explanations (not global importances)

### 2. Razorpay Test Mode Integration
- Payment Links API (real `rzp.io` URLs, real Razorpay dashboard entries)
- PayoutTransaction lifecycle: INITIATED -> PENDING -> SETTLED/FAILED
- Webhook handler at POST /webhooks/razorpay (signature verification)
- Graceful mock fallback when keys not configured (all tests pass without Razorpay)

### 3. Predictive Analytics (EWMA + Seasonal)
- 8-week EWMA on loss ratio, claim counts, payout totals
- City-aware seasonal adjustment (monsoon/haze/heat factors)
- Per-trigger risk forecast + per-city risk breakdown
- SVG sparkline chart with historical trend + forecast point
- Confidence interval visualization

---

# PART 12 — DEMO PLAN (5-MINUTE VIDEO)

## Narrative Structure

**0:00–0:30 — The Hook**
*"Ravi earns ₹1,150 a day on Blinkit. It's monsoon season. Today, he doesn't know that it's going to rain 72mm in Bangalore. He doesn't know that Zynvaro is about to send him ₹330. He doesn't know because he doesn't need to do anything."*

**0:30–1:30 — Worker Onboarding**
- Live app demo: register as new Blinkit rider in Bangalore
- Show zone risk computed instantly from pincode
- Show 3-tier quote with SHAP explanation: *"Your ₹36 premium is because..."*
- Buy Basic Shield policy — 7-day active policy confirmed

**1:30–2:30 — Trigger Simulation**
- Click "Simulate Trigger" → Heavy Rainfall → Bangalore → Fire
- Show the trigger appearing in the live feed
- Switch to admin panel — watch ALL active Bangalore workers get claims auto-generated
- Show Ravi's claim: CLM-XXXXXXXX, score 100, AUTO_APPROVED, paid_at timestamp

**2:30–3:30 — Fraud Detection Demo**
- Show 3 pre-seeded claims with different outcomes:
  - AUTO_APPROVED (score 100, city match, clean)
  - PENDING_REVIEW (score 70, high frequency)
  - MANUAL_REVIEW (score 40, city mismatch)
- Open each claim — show ML fraud probability alongside rule-based score
- Explain: "This is our RandomForest fraud model — 85.2% accuracy"

**3:30–4:30 — Analytics Dashboard**
- Weekly KPIs: loss ratio 0.42, auto-approval rate 75%, ₹1,840 premiums, ₹772 payouts
- Time-series 8-week chart
- City heatmap: Mumbai highest loss ratio, Bangalore moderate
- ML model info: feature importances (City Match = 27%)

**4:30–5:00 — Closing**
*"2 million Q-Commerce workers. Zero parametric income insurance products. Zynvaro is ready. The backend is running. The ML is trained. The tests pass. All we need is a Blinkit partnership and a Razorpay key."*

---

# PART 13 — WHY ZYNVARO WINS

## 1. Every Requirement Is Hit — Not 80% of It

Most hackathon submissions cover 60–70% of the spec. Zynvaro implements every bullet point in the problem statement's must-have features and deliverable expectations, with code that actually runs and tests that prove it.

## 2. Zero-Touch is the Correct Implementation of Parametric Insurance

The problem statement asks for "automated coverage and payouts." Most teams will build a "file a claim" button. Zynvaro eliminates the filing step entirely. Workers don't interact with the product when they need it most — the product acts for them. This is the right answer.

## 3. Real ML — Not Fake ML

The RandomForestClassifier in `ml/fraud_model.py` is trained, tested (85.2% accuracy), and producing real probability scores in production. Most teams will put "AI-powered" in their slides and have an if-statement in their code. Ours has 200 decision trees.

## 4. Real Data — Not 100% Mocks

The problem statement says mocks are acceptable. We went further: live OpenWeatherMap for rain/heatwave, live WAQI for AQI, live GDELT for civil disruption, live HTTP probes for platform outages. We built the real pipeline, not a simulation of one.

## 5. Production-Grade Engineering

748 automated tests. 100% pass rate. Graceful API fallbacks. Proper auth (JWT + pbkdf2). Database FK constraints. Scheduler with shutdown hook. Service worker with correct path-based API bypass. This is not a hackathon prototype — it's an architecture you could deploy.

## 6. The Business Model is Real

₹36–₹143/week premiums. 35–50% target loss ratio. B2B2C platform partnership model. IRDAI regulatory path identified. This isn't a student project with no commercial path — it's a ₹150 crore ARR opportunity with a clear route to market.

## 7. We Picked the Right Persona

Q-Commerce is the fastest-growing segment of the gig economy. Blinkit grew 90% in 2024. Zepto hit $5B valuation. These platforms need worker welfare solutions to retain riders. Zynvaro solves their retention problem while solving the worker's income risk problem. Perfect alignment.

## 8. Guidewire Alignment

Guidewire's InsuranceSuite powers parametric products at enterprise scale. Zynvaro is exactly the kind of product that lives on top of Guidewire's infrastructure — we're demonstrating that university-level developers can build the application layer for the same kind of parametric architecture Guidewire enables for enterprise insurers. We're not competing with Guidewire — we're showing what their platform enables.

---

# PART 14 — PROJECT STATUS SUMMARY

## What's Done ✅

| Category | Count / Detail |
|---|---|
| API endpoints | 33 (fully functional) |
| Database models | 5 (Worker, Policy, TriggerEvent, Claim, PayoutTransaction) |
| Parametric triggers | 6 (all with live API fallback) |
| Fraud detection modules | 6 (GPS, shift, weather, velocity, pattern, dedup) |
| ML models | 2 (14-feature RandomForest fraud v2 + actuarial premium) |
| Payment integration | Razorpay test mode (Payment Links API + webhooks) |
| LLM integration | 1 (Anthropic Claude risk narrator) |
| Live API integrations | 5 (OWM, WAQI, GDELT, HTTP probe, Razorpay) |
| Automated test cases | 858 (100% pass rate) |
| Frontend screens | 6 (Auth, Dashboard, Policy, Claims, Triggers, Admin) |
| Cities covered | 7 |
| Demo workers seeded | 5 (with 3 distinct fraud scenarios) |
| Lines of code | ~18,000+ |

## Remaining for Submission

- 5-minute demo video (screen capture walkthrough)
- Pitch deck PDF (10 slides)

## What's Planned (Phase 3 completion) 📋

- Final QA pass across all 30 endpoints
- Complete demo video recording
- Submit pitch deck PDF

---

# APPENDIX A — DEMO CREDENTIALS

| Worker | Phone | Password | City | Platform | Tier | Pre-Seeded Claim |
|---|---|---|---|---|---|---|
| Priya Sharma | 9876543211 | demo1234 | Mumbai | Zepto | Standard Guard | AUTO_APPROVED (score 100) |
| Arjun Mehta | 9876543212 | demo1234 | Delhi | Instamart | Standard Guard | PENDING_REVIEW (score 70) |
| Ravi Kumar | 9876543210 | demo1234 | Bangalore | Blinkit | Basic Shield | MANUAL_REVIEW (score 40) |
| Sneha Rao | 9876543213 | demo1234 | Hyderabad | Blinkit | Pro Armor | — |
| Kiran Patel | 9876543214 | demo1234 | Chennai | Zepto | Pro Armor | — |

---

# APPENDIX B — HOW TO RUN

```bash
# 1. Install dependencies
cd zynvaro-app/backend
pip install -r requirements.txt

# 2. (Optional) Set up live API keys
cp .env.example .env
# Add OPENWEATHER_API_KEY, WAQI_API_TOKEN, ANTHROPIC_API_KEY

# 3. Start server
uvicorn main:app --host 0.0.0.0 --port 9001 --reload

# 4. Access
# App:      http://localhost:9001/app
# API Docs: http://localhost:9001/api/docs
# Health:   http://localhost:9001/health

# 5. Run tests
pytest -q   # All 748 tests, ~75 seconds
```

The server auto-seeds all 5 demo workers, 3 triggers, and 3 demo claims on first boot. No database setup required.

---

*Zynvaro — AI-Powered Parametric Income Shield*
*Team AeroFyta | Guidewire DEVTrails 2026*
*Built with FastAPI · scikit-learn · Anthropic Claude · OpenWeatherMap · WAQI · GDELT*
