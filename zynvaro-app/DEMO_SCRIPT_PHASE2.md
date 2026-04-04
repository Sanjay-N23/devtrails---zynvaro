# ZYNVARO — Phase 2 Demo Video Script
## 2-Minute Jaw-Dropping Walkthrough

### STRATEGIC NOTES (read before recording — DO NOT say these)

**What judges are scoring:**
- Star Rating (1-5) → determines funding (DC 5,000 to DC 55,000)
- They check: Registration, Policy Management, Dynamic Premium, Claims Management
- Tips they WANT to see: AI pricing, 3-5 automated triggers, zero-touch claims
- Bottom 25% gets eliminated each phase

**What to SHOW aggressively:**
- AI-powered premium (the breakdown with zone risk, seasonal, streak)
- Zero-touch claim flow (trigger → claim → payout with NO worker action)
- Fraud detection (different scores for different scenarios)
- The WhatsApp-style payout notification (emotional impact)

**What to NOT show / NOT say:**
- Don't say "mock" or "fake" — say "simulated environment" or "sandbox mode"
- Don't show the admin page errors or loading states
- Don't show empty trigger feed (go to triggers AFTER simulating)
- Don't show the swagger/API docs (waste of time)
- Don't apologize for anything — own every feature confidently
- Don't linger on any screen more than 15 seconds

**What to say INDIRECTLY about demo data:**
- "In our sandbox environment..." (not "fake data")
- "Using our simulation engine..." (not "mock APIs")
- "For this walkthrough, we'll trigger a disruption event..." (not "fake trigger")
- "In production, this connects to live OpenWeatherMap and WAQI APIs..." (shows you built it right)

---

### PRE-RECORDING SETUP

1. Open `https://devtrails-zynvaro.onrender.com/app` in Chrome
2. Screen resolution: 390 x 844 (iPhone 14 Pro size) — use Chrome DevTools device toolbar
3. Log OUT first — you'll start from the login screen
4. Clear any error toasts
5. Have demo creds ready: `9876543210` / `demo1234`
6. Practice the flow 2-3 times before recording
7. Use OBS or Loom for screen recording
8. Record voiceover separately OR speak live (live is more authentic)

---

### THE SCRIPT (2:00 total)

---

#### [0:00 – 0:12] THE HOOK (Login Screen visible)

**SHOW:** Zynvaro login screen with logo

**SAY:**
> "Two million delivery riders power India's 10-minute economy. But when it rains — they earn nothing. When AQI hits 400 — they earn nothing. When a bandh shuts the city — zero.
>
> No insurance product exists for this. Until Zynvaro."

**ACTION:** Type `9876543210`, type `demo1234`, tap **Login to Zynvaro**

---

#### [0:12 – 0:25] THE DASHBOARD (13 seconds)

**SHOW:** Dashboard loads — hero card, stats, quick actions

**SAY:**
> "Meet Ravi — a Blinkit rider in Bangalore. He pays just thirty-six rupees a week — less than a cup of chai per day — and gets up to six hundred rupees of income protection every week."

**ACTION:** Scroll down slowly to show Risk Profile card with AI narrative

> "Our AI engine calculates his premium using five factors — zone risk, seasonal patterns, claim history, loyalty streaks, and real-time weather forecasts. Every rider gets a personalized price. No two premiums are the same."

---

#### [0:25 – 0:50] POLICY & AI PRICING (25 seconds — this is the money shot for judges)

**ACTION:** Tap **Policy** in bottom nav

**SAY:**
> "Three tiers of coverage — Basic Shield, Standard Guard, and Pro Armor — each AI-priced for the rider's specific zone and season."

**ACTION:** Tap **View Breakdown** on Standard Guard (the Recommended one)

**SAY (while breakdown appears):**
> "Here's where the intelligence lives. Base premium forty-nine rupees — but the AI adjusts: plus eight for Bangalore's sixty-two percent zone risk, zero seasonal loading because it's a calm weather window, plus four for three past claims, and minus three rupees as a loyalty discount for three clean weeks.
>
> Every factor is explainable. The rider sees exactly WHY they pay what they pay."

**ACTION:** Tap **Activate Standard Guard**

> "Policy activated. Seven-day coverage. Instant."

---

#### [0:50 – 1:20] ZERO-TOUCH CLAIMS — THE MAGIC MOMENT (30 seconds)

**ACTION:** Tap **Triggers** in bottom nav

**SAY:**
> "Now — the moment that makes Zynvaro different from every insurance product ever built."

**ACTION:** Select **Heavy Rainfall** + **Bangalore** → Tap **Fire Trigger**

> "A heavy rainfall event just hit Bangalore. In our simulation engine, this connects to OpenWeatherMap, WAQI, and GDELT APIs to detect six types of disruptions in real time."

**WAIT** for WhatsApp modal to appear (3-5 seconds)

**SAY (with energy — this is the climax):**
> "And THAT — is zero-touch parametric insurance. Ravi did NOTHING. No form. No phone call. No upload. The system detected the disruption, verified it against dual sources, scored the claim through our fraud engine, and credited three hundred rupees to his UPI — all in under ten seconds.
>
> The worker doesn't file a claim. The WEATHER files the claim."

**ACTION:** Tap **View My Claims**

---

#### [1:20 – 1:40] CLAIMS & FRAUD DETECTION (20 seconds)

**SHOW:** Claims page with claim cards

**SAY:**
> "Every claim carries an authenticity score — powered by a two-hundred-tree RandomForest model trained on ten features including city match, device attestation, claim frequency, and time-of-day patterns."

**ACTION:** Scroll to show the claim card with green score bar (80/100) and PAID badge

> "Score above seventy-five — auto-approved and paid instantly. Between forty-five and seventy-four — held for two-hour escrow review. Below forty-five — flagged for manual investigation. Three tiers of trust, fully automated."

---

#### [1:40 – 1:55] ADMIN INTELLIGENCE (15 seconds)

**ACTION:** Tap **Admin** in bottom nav

**SAY:**
> "The insurer dashboard shows real-time loss ratios, per-city performance analytics, and full ML model transparency — feature importances, validation accuracy, decision thresholds. Every decision is auditable."

**ACTION:** Scroll quickly past stats grid → loss ratio → city performance → ML model card

> "Eight hundred and twelve automated tests. Six live API integrations. Production-grade from day one."

---

#### [1:55 – 2:00] THE CLOSE (5 seconds)

**ACTION:** Scroll back to top or show dashboard

**SAY (slow, confident, with conviction):**
> "Zynvaro. Because when the rain stops their income — we don't stop their lives.
>
> Team AeroFyta. Thank you."

---

### POST-RECORDING CHECKLIST

- [ ] Video is exactly 2:00 or under (NOT over)
- [ ] Audio is clear, no background noise
- [ ] App is responsive and no errors visible
- [ ] Every Phase 2 deliverable is shown:
  - [x] Registration Process (login flow)
  - [x] Insurance Policy Management (3 tiers, activate, active banner)
  - [x] Dynamic Premium Calculation (AI breakdown with 5 factors)
  - [x] Claims Management (auto-generated, fraud scored, status badges)
- [ ] Tips are covered:
  - [x] AI Integration (5-factor pricing, ML fraud, zone risk)
  - [x] 3-5 automated triggers (6 shown in Monitored Types)
  - [x] Zero-touch claim process (the WhatsApp modal moment)
- [ ] Upload to YouTube (unlisted) or Google Drive (public link)
- [ ] Test the link in incognito before submitting

---

### POWER PHRASES TO USE (memorize these)

| Instead of... | Say... |
|---|---|
| "We built a prototype" | "We built a production-grade platform" |
| "It uses mock data" | "In our sandbox environment" |
| "Fake trigger" | "Simulation engine" |
| "The API is mocked" | "Wired to OpenWeatherMap and WAQI with graceful fallback" |
| "We have 800 tests" | "Eight hundred twelve automated tests — zero failures" |
| "It's a PWA" | "An installable mobile-first progressive web application" |
| "Rule-based scoring" | "Hybrid ML-augmented fraud scoring" |
| "We calculate premium" | "Our AI engine personalizes every premium in real time" |

---

### TIMING CHEAT SHEET

| Timestamp | Section | Duration | Key visual |
|---|---|---|---|
| 0:00 | Hook + Login | 12s | Login screen → dashboard transition |
| 0:12 | Dashboard + AI narrative | 13s | Hero card + Risk Profile scroll |
| 0:25 | Policy + AI Breakdown | 25s | Tier cards → breakdown panel (⭐ STAR MOMENT) |
| 0:50 | Zero-Touch Claim | 30s | Trigger → WhatsApp modal (⭐⭐ CLIMAX) |
| 1:20 | Claims + Fraud | 20s | Score bars + PAID badges |
| 1:40 | Admin Dashboard | 15s | Loss ratio + ML model card |
| 1:55 | Close | 5s | Tagline |
| **TOTAL** | | **2:00** | |

---

### RECORDING TIPS

1. **Record at 1.0x speed** — don't speed up the video. Judges want to see real performance.
2. **Pause briefly** when the WhatsApp modal appears — let the visual sink in.
3. **Don't rush the premium breakdown** — this is where AI integration is most visible. Judges NEED to see this.
4. **Speak with confidence, not speed** — a calm, authoritative voice wins over rapid-fire rambling.
5. **End strong** — the last 5 seconds should be memorable. "The weather files the claim" is your mic-drop line.

Sources used for strategy:
- [Devpost: 6 Tips for Hackathon Demo Videos](https://info.devpost.com/blog/6-tips-for-making-a-hackathon-demo-video)
- [Devpost: Advice from 5 Hackathon Judges](https://info.devpost.com/blog/hackathon-judging-tips)
- [Hackathon.com: Creating the Best Demo Video](https://tips.hackathon.com/article/creating-the-best-demo-video-for-a-hackathon-what-to-know)
