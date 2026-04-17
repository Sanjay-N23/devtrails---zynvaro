from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from sqlalchemy.orm import Session
from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime, timedelta
from jose import JWTError, jwt
from passlib.context import CryptContext
import os
import secrets

from database import get_db
from models import Worker
from ml.premium_engine import get_zone_risk

router = APIRouter(prefix="/auth", tags=["Authentication"])

SECRET_KEY = os.getenv("SECRET_KEY")
if not SECRET_KEY:
    SECRET_KEY = secrets.token_urlsafe(32)
    print("[WARNING] SECRET_KEY env var not set — using random key. Sessions will not survive restarts.")
ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = 60 * 24  # 24 hours

pwd_context = CryptContext(schemes=["pbkdf2_sha256"], deprecated="auto")
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")


# ─── Schemas ────────────────────────────────────────────────────
class RegisterRequest(BaseModel):
    full_name: str = Field(..., min_length=2, max_length=100)
    phone: str = Field(..., min_length=10, max_length=15, pattern=r"^[6-9]\d{9,13}$")
    email: Optional[str] = None
    password: str = Field(..., min_length=6, max_length=128)
    city: str = Field(..., min_length=2)
    pincode: str = Field(..., min_length=5, max_length=10)
    platform: str
    vehicle_type: Optional[str] = "2-Wheeler"
    shift: Optional[str] = "Evening Peak (6PM-2AM)"

class TokenResponse(BaseModel):
    access_token: str
    token_type: str
    worker_id: int
    full_name: str

class WorkerProfile(BaseModel):
    id: int
    full_name: str
    phone: str
    email: Optional[str]
    city: str
    pincode: str
    platform: str
    vehicle_type: str
    shift: str
    zone_risk_score: float
    claim_history_count: int
    disruption_streak: int
    # GPS (Phase 3)
    home_lat: Optional[float] = None
    home_lng: Optional[float] = None
    last_known_lat: Optional[float] = None
    last_known_lng: Optional[float] = None
    last_location_at: Optional[datetime] = None
    last_activity_source: Optional[str] = None
    effective_city: Optional[str] = None
    location_source: Optional[str] = None
    location_fresh: bool = False
    location_age_minutes: Optional[int] = None
    # Behavioral (Phase 3)
    fraud_flag_count: int = 0
    is_admin: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


def _serialize_worker_profile(worker: Worker) -> WorkerProfile:
    from services.fraud_engine import get_worker_location_context

    base = WorkerProfile.model_validate(worker, from_attributes=True).model_dump()
    location = get_worker_location_context(worker)
    base.update({
        "effective_city": location.get("effective_city"),
        "location_source": location.get("source"),
        "location_fresh": bool(location.get("location_fresh")),
        "location_age_minutes": location.get("location_age_minutes"),
        "last_location_at": location.get("last_location_at"),
    })
    return WorkerProfile(**base)


def _touch_session_activity(worker: Worker, db: Optional[Session] = None) -> Worker:
    worker.last_location_at = datetime.utcnow()
    if worker.last_activity_source == "gps_ping":
        pass
    elif (
        worker.last_activity_source is None
        and worker.last_known_lat is not None
        and worker.last_known_lng is not None
    ):
        # Backward compatibility for older rows/tests that have a real GPS fix
        # but predate the explicit activity-source column.
        pass
    else:
        worker.last_activity_source = "session_ping"
    if db is not None:
        db.add(worker)
        db.commit()
        db.refresh(worker)
    return worker


# ─── Helpers ────────────────────────────────────────────────────
def hash_password(password: str) -> str:
    return pwd_context.hash(password)

def verify_password(plain: str, hashed: str) -> bool:
    return pwd_context.verify(plain, hashed)

def create_access_token(data: dict) -> str:
    to_encode = data.copy()
    expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode.update({"exp": expire})
    return jwt.encode(to_encode, SECRET_KEY, algorithm=ALGORITHM)

def get_current_worker(token: str = Depends(oauth2_scheme), db: Session = Depends(get_db)) -> Worker:
    credentials_exception = HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Could not validate credentials",
        headers={"WWW-Authenticate": "Bearer"},
    )
    try:
        payload = jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM], options={"require_exp": True})
        worker_id_str = payload.get("sub")
        if worker_id_str is None:
            raise credentials_exception
        worker_id: int = int(worker_id_str)
        if worker_id is None:
            raise credentials_exception
    except (JWTError, ValueError):
        raise credentials_exception

    worker = db.query(Worker).filter(Worker.id == worker_id).first()
    if worker is None:
        raise credentials_exception
    return worker


# ─── Endpoints ──────────────────────────────────────────────────
@router.post("/register", response_model=TokenResponse, status_code=201)
def register(req: RegisterRequest, db: Session = Depends(get_db)):
    # Check duplicate phone
    if db.query(Worker).filter(Worker.phone == req.phone).first():
        raise HTTPException(status_code=400, detail="Phone number already registered")

    # Auto-compute zone risk
    zone_risk = get_zone_risk(req.pincode, req.city)

    # Auto-assign GPS coordinates from pincode (Phase 3: Advanced Fraud Detection)
    from services.fraud_engine import get_pincode_gps
    home_lat, home_lng = get_pincode_gps(req.pincode, req.city)

    worker = Worker(
        full_name=req.full_name,
        phone=req.phone,
        email=req.email,
        password_hash=hash_password(req.password),
        city=req.city,
        pincode=req.pincode,
        platform=req.platform,
        vehicle_type=req.vehicle_type or "2-Wheeler",
        shift=req.shift or "Evening Peak (6PM-2AM)",
        zone_risk_score=zone_risk,
        home_lat=home_lat,
        home_lng=home_lng,
        last_known_lat=home_lat,
        last_known_lng=home_lng,
        # Registration itself is a real user-originated app session.
        # Keep the pincode-derived home coordinates, but do not classify a
        # newly registered worker as signup-seeded/inactive for payout gating.
        last_location_at=datetime.utcnow(),
        last_activity_source="session_ping",
    )
    db.add(worker)
    db.commit()
    db.refresh(worker)

    token = create_access_token({"sub": str(worker.id)})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        worker_id=worker.id,
        full_name=worker.full_name,
    )


@router.post("/login", response_model=TokenResponse)
def login(form: OAuth2PasswordRequestForm = Depends(), db: Session = Depends(get_db)):
    worker = db.query(Worker).filter(Worker.phone == form.username).first()
    if not worker or not verify_password(form.password, worker.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid phone or password",
        )
    worker = _touch_session_activity(worker, db)
    token = create_access_token({"sub": str(worker.id)})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        worker_id=worker.id,
        full_name=worker.full_name,
    )


@router.get("/me", response_model=WorkerProfile)
def get_profile(worker: Worker = Depends(get_current_worker), db: Session = Depends(get_db)):
    worker = _touch_session_activity(worker, db)
    return _serialize_worker_profile(worker)


# ─── Location Update (Phase 3: GPS Fraud Detection) ──────────
class LocationUpdate(BaseModel):
    lat: float
    lng: float

@router.post("/me/location")
def update_location(loc: LocationUpdate, worker: Worker = Depends(get_current_worker), db: Session = Depends(get_db)):
    """Update worker's current GPS coordinates (for GPS-based fraud detection)."""
    worker.last_known_lat = loc.lat
    worker.last_known_lng = loc.lng
    worker.last_location_at = datetime.utcnow()
    worker.last_activity_source = "gps_ping"
    db.commit()
    profile = _serialize_worker_profile(worker)
    return {
        "status": "ok",
        "lat": loc.lat,
        "lng": loc.lng,
        "effective_city": profile.effective_city,
        "location_source": profile.location_source,
        "location_fresh": profile.location_fresh,
        "location_age_minutes": profile.location_age_minutes,
        "last_location_at": profile.last_location_at.isoformat() if profile.last_location_at else None,
    }
