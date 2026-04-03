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
    is_admin: bool = False
    created_at: datetime

    class Config:
        from_attributes = True


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
    token = create_access_token({"sub": str(worker.id)})
    return TokenResponse(
        access_token=token,
        token_type="bearer",
        worker_id=worker.id,
        full_name=worker.full_name,
    )


@router.get("/me", response_model=WorkerProfile)
def get_profile(worker: Worker = Depends(get_current_worker)):
    return worker
