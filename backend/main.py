from __future__ import annotations
import os

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from auth import (
    COOKIE_NAME,
    TOKEN_MAX_AGE,
    create_token,
    get_current_user,
    hash_password,
    require_admin,
    verify_password,
)
from database import engine, get_db
from models import Base, User
from oauth import enabled_providers, get_oauth_redirect, handle_oauth_callback
from schemas import LoginRequest, RegisterRequest, UpdateUserRequest, UserResponse

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Lake of Tears Auth API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("AUTH_BASE_URL", "http://localhost")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Auth ──────────────────────────────────────────────────────────────────────


@app.post("/api/auth/register")
def register(req: RegisterRequest, response: Response, db: Session = Depends(get_db)):
    if db.query(User).filter(User.email == req.email).first():
        raise HTTPException(400, "Email already registered")

    is_first = db.query(User).count() == 0
    user = User(
        email=req.email,
        display_name=req.display_name or req.email.split("@")[0],
        hashed_password=hash_password(req.password),
        role="admin" if is_first else "viewer",
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    token = create_token({"sub": str(user.id), "email": user.email, "role": user.role})
    response.set_cookie(COOKIE_NAME, token, max_age=TOKEN_MAX_AGE, httponly=True, samesite="lax")
    return {"ok": True, "role": user.role}


@app.post("/api/auth/login")
def login(req: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not user.hashed_password or not verify_password(req.password, user.hashed_password):
        raise HTTPException(401, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(403, "Account disabled — contact an admin")

    token = create_token({"sub": str(user.id), "email": user.email, "role": user.role})
    response.set_cookie(COOKIE_NAME, token, max_age=TOKEN_MAX_AGE, httponly=True, samesite="lax")
    return {"ok": True, "role": user.role}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    return {"ok": True}


@app.get("/api/auth/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)


@app.get("/api/auth/providers")
def providers():
    return enabled_providers()


# ── OAuth ─────────────────────────────────────────────────────────────────────


@app.get("/api/auth/oauth/{provider}")
def oauth_start(provider: str, request: Request, response: Response):
    return get_oauth_redirect(provider, request, response)


@app.get("/api/auth/oauth/{provider}/callback")
def oauth_callback(provider: str, request: Request, response: Response, db: Session = Depends(get_db)):
    return handle_oauth_callback(provider, request, response, db)


# ── User management (admin only) ──────────────────────────────────────────────


@app.get("/api/users", response_model=list[UserResponse])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_admin)):
    return [UserResponse.model_validate(u) for u in db.query(User).order_by(User.created_at).all()]


@app.patch("/api/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    req: UpdateUserRequest,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_admin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if str(user.id) == str(current_admin.id) and req.role and req.role != "admin":
        raise HTTPException(400, "Cannot demote yourself")
    if req.role is not None:
        user.role = req.role
    if req.is_active is not None:
        user.is_active = req.is_active
    db.commit()
    return UserResponse.model_validate(user)


# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/api/health")
def health():
    return {"status": "ok"}
