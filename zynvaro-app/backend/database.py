from sqlalchemy import create_engine
from sqlalchemy.ext.declarative import declarative_base
from sqlalchemy.orm import sessionmaker
import os

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./zynvaro.db")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if "sqlite" in DATABASE_URL else {}
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def run_sqlite_startup_migrations():
    """
    Apply additive SQLite migrations for local/demo databases.

    `Base.metadata.create_all()` creates missing tables but does not add columns
    to existing ones, so we patch in new non-destructive fields here.
    """
    if "sqlite" not in DATABASE_URL:
        return

    migrations = {
        "workers": {
            "last_activity_source": "ALTER TABLE workers ADD COLUMN last_activity_source VARCHAR(30)",
        },
        "trigger_events": {
            "confidence_score": "ALTER TABLE trigger_events ADD COLUMN confidence_score FLOAT DEFAULT 100.0",
            "source_log": "ALTER TABLE trigger_events ADD COLUMN source_log TEXT",
        },
        "claims": {
            "trigger_confidence_score": "ALTER TABLE claims ADD COLUMN trigger_confidence_score FLOAT DEFAULT 100.0",
            "appeal_status": "ALTER TABLE claims ADD COLUMN appeal_status VARCHAR(30) DEFAULT 'none'",
            "appeal_reason": "ALTER TABLE claims ADD COLUMN appeal_reason TEXT",
            "appealed_at": "ALTER TABLE claims ADD COLUMN appealed_at DATETIME",
            "recent_activity_valid": "ALTER TABLE claims ADD COLUMN recent_activity_valid BOOLEAN DEFAULT 1",
            "recent_activity_at": "ALTER TABLE claims ADD COLUMN recent_activity_at DATETIME",
            "recent_activity_age_hours": "ALTER TABLE claims ADD COLUMN recent_activity_age_hours FLOAT",
            "recent_activity_reason": "ALTER TABLE claims ADD COLUMN recent_activity_reason TEXT",
        },
        # Demo Payment Bypass audit columns (added in session fc46eca3)
        "payout_transactions": {
            "is_demo_bypass": "ALTER TABLE payout_transactions ADD COLUMN is_demo_bypass BOOLEAN DEFAULT 0",
            "bypass_source_screen": "ALTER TABLE payout_transactions ADD COLUMN bypass_source_screen VARCHAR(50)",
            "original_provider_error": "ALTER TABLE payout_transactions ADD COLUMN original_provider_error TEXT",
            "environment_at_bypass": "ALTER TABLE payout_transactions ADD COLUMN environment_at_bypass VARCHAR(20)",
        },
        # Simulate Trigger / What-If scenario audit columns (added in session fc46eca3)
        "trigger_events": {
            "confidence_score": "ALTER TABLE trigger_events ADD COLUMN confidence_score FLOAT DEFAULT 100.0",
            "source_log": "ALTER TABLE trigger_events ADD COLUMN source_log TEXT",
            "source_type": "ALTER TABLE trigger_events ADD COLUMN source_type VARCHAR(30) DEFAULT 'LIVE'",
            "scenario_id": "ALTER TABLE trigger_events ADD COLUMN scenario_id VARCHAR(40)",
            "scenario_name": "ALTER TABLE trigger_events ADD COLUMN scenario_name VARCHAR(100)",
            "scenario_created_by": "ALTER TABLE trigger_events ADD COLUMN scenario_created_by INTEGER",
            "scenario_created_by_role": "ALTER TABLE trigger_events ADD COLUMN scenario_created_by_role VARCHAR(30)",
            "pipeline_run_id": "ALTER TABLE trigger_events ADD COLUMN pipeline_run_id VARCHAR(40)",
            "original_environment": "ALTER TABLE trigger_events ADD COLUMN original_environment VARCHAR(20)",
        },
    }

    with engine.begin() as conn:
        for table_name, columns in migrations.items():
            existing = {
                row[1]
                for row in conn.exec_driver_sql(f"PRAGMA table_info({table_name})").fetchall()
            }
            for column_name, ddl in columns.items():
                if column_name not in existing:
                    try:
                        conn.exec_driver_sql(ddl)
                    except Exception as exc:
                        if "duplicate column name" not in str(exc).lower():
                            raise

def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
