from __future__ import annotations
import os
from datetime import datetime, timedelta, timezone
from typing import Optional

import jwt
from fastapi import Cookie, Depends, HTTPException
from passlib.context import CryptContext
from sqlalchemy.orm import Session

from database import get_db
from models import User

SECRET_KEY = os.getenv("AUTH_SECRET_KEY", "dev-secret-please-change-in-production")
ALGORITHM = "HS256"
TOKEN_EXPIRE_DAYS = 7
COOKIE_NAME = "lake_token"
TOKEN_MAX_AGE = TOKEN_EXPIRE_DAYS * 24 * 3600

_pwd = CryptContext(schemes=["bcrypt"], deprecated="auto")


def hash_password(password: str) -> str:
    return _pwd.hash(password)


def verify_password(plain: str, hashed: str) -> bool:
    return _pwd.verify(plain, hashed)


def create_token(data: dict) -> str:
    payload = {**data, "exp": datetime.now(timezone.utc) + timedelta(days=TOKEN_EXPIRE_DAYS)}
    return jwt.encode(payload, SECRET_KEY, algorithm=ALGORITHM)


def decode_token(token: str) -> dict:
    return jwt.decode(token, SECRET_KEY, algorithms=[ALGORITHM])


def get_current_user(
    lake_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
) -> User:
    if not lake_token:
        raise HTTPException(401, "Not authenticated")
    try:
        payload = decode_token(lake_token)
    except jwt.ExpiredSignatureError:
        raise HTTPException(401, "Token expired")
    except jwt.InvalidTokenError:
        raise HTTPException(401, "Invalid token")

    user = db.query(User).filter(User.id == payload["sub"]).first()
    if not user or not user.is_active:
        raise HTTPException(401, "User not found or disabled")
    return user


def require_admin(current_user: User = Depends(get_current_user)) -> User:
    if current_user.role != "admin":
        raise HTTPException(403, "Admin access required")
    return current_user
