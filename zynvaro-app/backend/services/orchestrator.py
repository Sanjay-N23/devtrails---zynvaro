"""
Zynvaro — Autonomous Trigger Orchestrator
Polls all active cities every 15 minutes for live trigger conditions.
Saves fired events to DB and auto-generates claims (zero-touch parametric).
"""

from datetime import datetime, timedelta
from sqlalchemy import or_
from database import SessionLocal
from models import Worker, Policy, TriggerEvent, PolicyStatus
from services.trigger_engine import check_all_triggers


async def poll_all_cities_for_triggers():
    """
    Autonomous 15-minute poller.
    1. Queries all cities with at least one active policy.
    2. Runs check_all_triggers() for each city.
    3. Persists fired events to DB (with deduplication).
    4. Calls _auto_generate_claims() for each new event.
    """
    from routers.triggers import _auto_generate_claims

    db = SessionLocal()
    try:
        # Get all cities that have at least one active policy
        active_cities = (
            db.query(Worker.city)
            .join(Worker.policies)
            .filter(Policy.status == PolicyStatus.ACTIVE)
            .distinct()
            .all()
        )
        cities = [row[0] for row in active_cities]

        if not cities:
            return

        print(f"[Scheduler] Polling {len(cities)} cities at {datetime.utcnow().isoformat()}")

        for city in cities:
            fired = await check_all_triggers(city)

            for t in fired:
                # Deduplication: skip if same trigger type + city fired within the last 3 hours or hasn't expired
                recent = (
                    db.query(TriggerEvent)
                    .filter(
                        TriggerEvent.trigger_type == t["trigger_type"],
                        TriggerEvent.city == city,
                        or_(
                            TriggerEvent.detected_at >= (datetime.utcnow() - timedelta(hours=3)),
                            TriggerEvent.expires_at >= datetime.utcnow(),
                        ),
                    )
                    .first()
                )
                if recent:
                    continue

                event = TriggerEvent(
                    trigger_type=t["trigger_type"],
                    city=t["city"],
                    measured_value=t["measured_value"],
                    threshold_value=t["threshold_value"],
                    unit=t["unit"],
                    source_primary=t["source_primary"],
                    source_secondary=t["source_secondary"],
                    is_validated=t["is_validated"],
                    severity=t["severity"],
                    description=t["description"],
                    detected_at=datetime.fromisoformat(t["detected_at"]),
                    expires_at=datetime.fromisoformat(t["expires_at"]),
                )
                db.add(event)
                db.commit()
                db.refresh(event)

                # Zero-touch: auto-generate claims for all eligible workers in city
                _auto_generate_claims(event.id, city, t["trigger_type"], db)
                print(f"[Scheduler] Fired: {t['trigger_type']} in {city} → claims queued")

    except Exception as e:
        print(f"[Scheduler] Poll error: {e}")
        db.rollback()
    finally:
        db.close()
