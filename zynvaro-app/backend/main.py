"""
Zynvaro — Backend API
FastAPI + SQLite | Guidewire DEVTrails 2026
"""

import sys
import os
from datetime import datetime, timedelta
from dotenv import load_dotenv

# Load .env file if present (no-op if missing — env vars already set take precedence)
load_dotenv(os.path.join(os.path.dirname(__file__), ".env"), override=False)

# Fix Windows console encoding for emoji/unicode
if sys.platform == "win32":
    sys.stdout.reconfigure(encoding='utf-8', errors='replace')
    sys.stderr.reconfigure(encoding='utf-8', errors='replace')

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from database import engine, Base, run_sqlite_startup_migrations
from routers import auth, policies, triggers, claims, analytics

# ─────────────────────────────────────────────────────────────────
# CREATE TABLES
# ─────────────────────────────────────────────────────────────────
Base.metadata.create_all(bind=engine)
run_sqlite_startup_migrations()

# ─────────────────────────────────────────────────────────────────
# APP INIT
# ─────────────────────────────────────────────────────────────────
app = FastAPI(
    title="Zynvaro API",
    description="AI-Powered Parametric Income Shield for Q-Commerce Gig Workers — Guidewire DEVTrails 2026",
    version="2.0.0",
    docs_url="/api/docs",
    redoc_url="/api/redoc",
)

# ─────────────────────────────────────────────────────────────────
# CORS (allow frontend on any port during dev)
# NOTE [HACKATHON DEMO]: allow_origins=["*"] is intentional for
# local demo use. Production would restrict to specific domain.
# ─────────────────────────────────────────────────────────────────
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ─────────────────────────────────────────────────────────────────
# ROUTERS
# ─────────────────────────────────────────────────────────────────
from routers import webhooks
from routers import cases as cases_router
from routers import admin_cases as admin_cases_router
app.include_router(auth.router)
app.include_router(policies.router)
app.include_router(triggers.router)
# cases_router must be before claims.router — both have /claims/{id}/appeal
# and FastAPI resolves in registration order
app.include_router(cases_router.router)
app.include_router(claims.router)
app.include_router(analytics.router)
app.include_router(webhooks.router)
app.include_router(admin_cases_router.router)

# ─────────────────────────────────────────────────────────────────
# SERVE FRONTEND (static files)
# ─────────────────────────────────────────────────────────────────
FRONTEND_DIR = os.path.join(os.path.dirname(__file__), "..", "frontend")
if os.path.exists(FRONTEND_DIR):
    app.mount("/static", StaticFiles(directory=FRONTEND_DIR), name="static")

    @app.get("/app", include_in_schema=False)
    def serve_app():
        return FileResponse(os.path.join(FRONTEND_DIR, "app.html"))


# ─────────────────────────────────────────────────────────────────
# HEALTH CHECK
# ─────────────────────────────────────────────────────────────────
@app.get("/health", tags=["System"])
def health_check():
    rzp_id = os.getenv("RAZORPAY_KEY_ID", "")
    return {
        "status": "healthy",
        "service": "Zynvaro API",
        "version": "3.0.0",
        "phase": "DEVTrails 2026 — Phase 3: SOAR",
        "razorpay": bool(rzp_id),
        "razorpay_key_prefix": rzp_id[:12] + "..." if rzp_id else "not set",
    }


@app.get("/", include_in_schema=False)
def root():
    return {
        "message": "⚡ Zynvaro API is running",
        "docs": "/api/docs",
        "app": "/app",
    }


# ─────────────────────────────────────────────────────────────────
# AUTONOMOUS TRIGGER SCHEDULER (15-min polling)
# ─────────────────────────────────────────────────────────────────
@app.on_event("startup")
async def start_trigger_scheduler():
    """Start APScheduler to autonomously poll all cities every 15 minutes."""
    try:
        from apscheduler.schedulers.asyncio import AsyncIOScheduler
        from services.orchestrator import poll_all_cities_for_triggers
        scheduler = AsyncIOScheduler(timezone="Asia/Kolkata")
        scheduler.add_job(
            poll_all_cities_for_triggers,
            trigger="interval",
            minutes=15,
            id="trigger_poll",
            next_run_time=datetime.utcnow() + timedelta(seconds=30),  # first run 30s after boot
        )
        # Policy auto-expiry — run every hour
        from database import SessionLocal
        from routers.policies import expire_stale_policies

        def _expire_policies_job():
            db = SessionLocal()
            try:
                expire_stale_policies(db)
            finally:
                db.close()

        scheduler.add_job(
            _expire_policies_job,
            trigger="interval",
            hours=1,
            id="policy_expiry",
            next_run_time=datetime.utcnow() + timedelta(seconds=45),
        )

        scheduler.start()
        app.state.scheduler = scheduler
        print("✅ Trigger scheduler started — polling every 15 minutes")
        print("✅ Policy expiry scheduler started — checking every hour")
    except Exception as e:
        print(f"⚠️  Scheduler failed to start: {e}")


@app.on_event("shutdown")
async def stop_trigger_scheduler():
    """Gracefully stop APScheduler on server shutdown to prevent orphaned threads."""
    scheduler = getattr(app.state, "scheduler", None)
    if scheduler and scheduler.running:
        scheduler.shutdown(wait=False)
        print("✅ Trigger scheduler stopped")


# ─────────────────────────────────────────────────────────────────
# SEED DEMO DATA (runs once on startup)
# ─────────────────────────────────────────────────────────────────
@app.on_event("startup")
def seed_demo_data():
    from database import SessionLocal
    from models import Worker, Policy, TriggerEvent, Claim, PolicyStatus, ClaimStatus
    from routers.auth import hash_password
    from ml.premium_engine import calculate_premium, get_payout_amount, TIER_CONFIG
    from services.trigger_engine import compute_authenticity_score
    from datetime import datetime, timedelta
    import random, string

    db = SessionLocal()
    try:
        # Hackathon demo: promote ALL existing workers to admin on every startup
        non_admins = db.query(Worker).filter(Worker.is_admin == False).all()
        if non_admins:
            for w in non_admins:
                w.is_admin = True
            db.commit()
            print(f"✅ Promoted {len(non_admins)} worker(s) to admin")
        # Only seed if DB is empty
        if db.query(Worker).count() > 0:
            return

        print("🌱 Seeding demo data...")

        demo_workers = [
            {"full_name": "Ravi Kumar",     "phone": "9876543210", "city": "Bangalore", "pincode": "560047", "platform": "Blinkit",   "shift": "Evening Peak (6PM-2AM)"},
            {"full_name": "Priya Sharma",   "phone": "9876543211", "city": "Mumbai",    "pincode": "400051", "platform": "Zepto",     "shift": "Morning Peak (8AM-2PM)"},
            {"full_name": "Arjun Mehta",    "phone": "9876543212", "city": "Delhi",     "pincode": "110019", "platform": "Instamart", "shift": "Full Day (10AM-10PM)"},
            {"full_name": "Sneha Rao",      "phone": "9876543213", "city": "Hyderabad", "pincode": "500072", "platform": "Blinkit",   "shift": "Evening Peak (6PM-2AM)"},
            {"full_name": "Kiran Patel",    "phone": "9876543214", "city": "Chennai",   "pincode": "600041", "platform": "Zepto",     "shift": "Evening Peak (6PM-2AM)"},
        ]

        # Tiers to assign round-robin
        tiers = ["Basic Shield", "Standard Guard", "Pro Armor", "Standard Guard", "Pro Armor"]

        workers_created = []
        for i, wd in enumerate(demo_workers):
            from ml.premium_engine import get_zone_risk
            from services.fraud_engine import get_pincode_gps
            zone_risk = get_zone_risk(wd["pincode"], wd["city"])
            home_lat, home_lng = get_pincode_gps(wd["pincode"], wd["city"])
            w = Worker(
                full_name=wd["full_name"],
                phone=wd["phone"],
                email=f"{wd['full_name'].lower().replace(' ', '.')}@demo.zynvaro.in",
                password_hash=hash_password("demo1234"),
                city=wd["city"],
                pincode=wd["pincode"],
                platform=wd["platform"],
                vehicle_type="2-Wheeler",
                shift=wd["shift"],
                zone_risk_score=zone_risk,
                claim_history_count=random.randint(0, 3),
                disruption_streak=random.randint(0, 5),
                is_admin=(i == 0),  # First worker (Ravi Kumar) is admin
                home_lat=home_lat,
                home_lng=home_lng,
                last_known_lat=home_lat,
                last_known_lng=home_lng,
            )
            db.add(w)
            db.flush()

            # Create active policy
            tier = tiers[i]
            pricing = calculate_premium(tier, wd["pincode"], wd["city"],
                                        w.claim_history_count, w.disruption_streak)
            cfg = TIER_CONFIG[tier]
            bkd = pricing["breakdown"]

            policy = Policy(
                worker_id=w.id,
                policy_number="ZYN-DEMO-" + str(1000 + i),
                tier=tier,
                status=PolicyStatus.ACTIVE,
                weekly_premium=pricing["weekly_premium"],
                base_premium=pricing["base_premium"],
                max_daily_payout=cfg["max_daily"],
                max_weekly_payout=cfg["max_weekly"],
                zone_loading=bkd["zone_loading_inr"],
                seasonal_loading=bkd["seasonal_loading_inr"],
                claim_loading=bkd["claim_loading_inr"],
                streak_discount=abs(bkd["streak_discount_inr"]),
                start_date=datetime.utcnow() - timedelta(days=2),
                end_date=datetime.utcnow() + timedelta(days=30),
            )
            db.add(policy)
            db.flush()
            workers_created.append((w, policy))

        # Seed 3 historical trigger events (city-matched to demo workers)
        trigger_data = [
            {"type": "Heavy Rainfall",  "city": "Mumbai",    "value": 78.5,  "unit": "mm/24hr", "src1": "OpenWeatherMap", "src2": "IMD API",        "threshold": 64.5},
            {"type": "Hazardous AQI",   "city": "Delhi",     "value": 485.0, "unit": "AQI",     "src1": "WAQI API",       "src2": "CPCB Stations",   "threshold": 400.0},
            {"type": "Civil Disruption","city": "Bangalore", "value": 6.0,   "unit": "hours",   "src1": "GDELT (mock)",   "src2": "NewsAPI (mock)",  "threshold": 4.0},
        ]

        trigger_events = []
        for td in trigger_data:
            te = TriggerEvent(
                trigger_type=td["type"],
                city=td["city"],
                measured_value=td["value"],
                threshold_value=td["threshold"],
                unit=td["unit"],
                source_primary=td["src1"],
                source_secondary=td["src2"],
                is_validated=True,
                severity="high",
                description=f"{td['type']} in {td['city']}: {td['value']} {td['unit']}",
                detected_at=datetime.utcnow() - timedelta(hours=random.randint(1, 24)),
                expires_at=datetime.utcnow() + timedelta(hours=4),
            )
            db.add(te)
            db.flush()
            trigger_events.append(te)

        # ── Demo claim scenarios (3 distinct fraud outcomes for admin panel showcase)
        # workers_created: [0]=Ravi/Bangalore, [1]=Priya/Mumbai, [2]=Arjun/Delhi
        # triggers:        [0]=Mumbai Rain,    [1]=Delhi AQI,    [2]=Bangalore Civil

        demo_claims = [
            # AUTO_APPROVED: Priya (Mumbai) + Mumbai trigger → city match, clean history
            {"worker_idx": 1, "trigger_idx": 0, "history": 0, "same_week": 0},
            # PENDING_REVIEW: Arjun (Delhi) + Delhi trigger → city match but 3 same-week + high history
            #   Score: 100 - 20 (same_week=3 capped) - 10 (history>5) = 70 → PENDING
            {"worker_idx": 2, "trigger_idx": 1, "history": 7, "same_week": 3},
            # MANUAL_REVIEW: Ravi (Bangalore) + Delhi trigger → city mismatch (-40) → score 60
            #   Score: 100 - 40 (city mismatch) = 60 → PENDING... add same_week=2 → 40 → MANUAL
            {"worker_idx": 0, "trigger_idx": 1, "history": 0, "same_week": 2},
        ]

        for i, dc in enumerate(demo_claims):
            w, policy = workers_created[dc["worker_idx"]]
            te = trigger_events[dc["trigger_idx"]]

            payout = get_payout_amount(te.trigger_type, policy.tier, w.city)
            if payout <= 0:
                payout = 300.0  # fallback so all 3 demo claims have a payout amount

            fraud = compute_authenticity_score(w.city, te.city, dc["history"], dc["same_week"], True)
            claim_num = f"CLM-DEMO-{1000+i}"

            # AUTO_APPROVED → PAID immediately (payment ref + timestamp set together)
            is_auto = fraud["decision"] == "AUTO_APPROVED"
            status_map = {
                "AUTO_APPROVED": ClaimStatus.PAID,
                "PENDING_REVIEW": ClaimStatus.PENDING_REVIEW,
                "MANUAL_REVIEW": ClaimStatus.MANUAL_REVIEW,
            }

            claim = Claim(
                claim_number=claim_num,
                worker_id=w.id,
                policy_id=policy.id,
                trigger_event_id=te.id,
                status=status_map.get(fraud["decision"], ClaimStatus.PENDING_REVIEW),
                payout_amount=payout,
                authenticity_score=fraud["score"],
                gps_valid=fraud["gps_valid"],
                activity_valid=fraud["activity_valid"],
                device_valid=fraud["device_valid"],
                cross_source_valid=fraud["cross_source_valid"],
                fraud_flags="; ".join(fraud["flags"]) if fraud["flags"] else None,
                auto_processed=True,
                paid_at=datetime.utcnow() - timedelta(minutes=random.randint(5, 60)) if is_auto else None,
                payment_ref=f"MOCK-UPI-{claim_num}" if is_auto else None,
            )
            db.add(claim)

        db.commit()
        print(f"✅ Demo data seeded: {len(demo_workers)} workers, {len(trigger_events)} triggers, 3 claims")

    except Exception as e:
        print(f"⚠️  Seed error (non-fatal): {e}")
        db.rollback()
    finally:
        db.close()
