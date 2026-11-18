from datetime import datetime, timedelta, timezone

import bcrypt
import jwt
from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.models import User

PUBLISHED_JWT_SECRET = "dev-only-change-me"


def require_jwt_secret() -> str:
    secret = settings.jwt_secret.strip()
    if len(secret) < 16 or secret == PUBLISHED_JWT_SECRET:
        raise RuntimeError(
            "Set JWT_SECRET to a private value of at least 16 characters before starting the API. "
            "A missing or published signing key cannot be used."
        )
    return secret


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except ValueError:
        return False


def create_token(user: User) -> str:
    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.jwt_exp_minutes)
    payload = {"sub": str(user.id), "role": user.role, "exp": expire}
    return jwt.encode(payload, require_jwt_secret(), algorithm="HS256")


def get_current_user(
    db: Session = Depends(get_db),
    authorization: str | None = Header(default=None),
) -> User:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise HTTPException(status_code=401, detail="Not authenticated")
    token = authorization.split(" ", 1)[1].strip()
    try:
        payload = jwt.decode(token, require_jwt_secret(), algorithms=["HS256"])
        user_id = int(payload["sub"])
    except (jwt.PyJWTError, ValueError, KeyError):
        raise HTTPException(status_code=401, detail="Not authenticated") from None
    user = db.get(User, user_id)
    if user is None or not user.active:
        raise HTTPException(status_code=401, detail="Not authenticated")
    return user


def require_judge(user: User = Depends(get_current_user)) -> User:
    if user.role != "judge":
        raise HTTPException(status_code=403, detail="Judge access required")
    return user


def require_organizer(user: User = Depends(get_current_user)) -> User:
    if user.role != "organizer":
        raise HTTPException(status_code=403, detail="Organizer access required")
    return user


def find_user_by_email(db: Session, email: str) -> User | None:
    return db.scalar(select(User).where(User.email == email.strip().lower()))
