from __future__ import annotations
import os
import re

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy.orm import Session

from auth import (
    COOKIE_NAME,
    TOKEN_MAX_AGE,
    create_token,
    get_current_user,
    hash_password,
    require_superadmin,
    require_workspace_admin,
    verify_password,
)
from database import engine, get_db
from models import Base, OAuthAccount, User, Workspace, WorkspaceMember
from oauth import enabled_providers, get_oauth_redirect, handle_oauth_callback
from schemas import (
    AddMemberRequest,
    CreateWorkspaceRequest,
    LoginRequest,
    RegisterRequest,
    UpdateMemberRequest,
    UpdateMeRequest,
    UpdateUserRequest,
    UpdateWorkspaceRequest,
    UserResponse,
    WorkspaceMemberResponse,
    WorkspaceResponse,
)

Base.metadata.create_all(bind=engine)

app = FastAPI(title="Lake of Tears Auth API", docs_url=None, redoc_url=None)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[os.getenv("AUTH_BASE_URL", "http://localhost")],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ── Helpers ───────────────────────────────────────────────────────────────────

def _slugify(name: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    slug = re.sub(r"[\s_-]+", "-", slug).strip("-")
    return slug or "workspace"


def _unique_slug(db: Session, base: str) -> str:
    slug, i = base, 0
    while db.query(Workspace).filter(Workspace.slug == slug).first():
        i += 1
        slug = f"{base}-{i}"
    return slug


def _create_default_workspace(db: Session, user: User) -> Workspace:
    slug = _unique_slug(db, "default")
    ws = Workspace(name="Default", slug=slug, description="Default workspace", created_by=user.id)
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=user.id, role="admin"))
    return ws


def _set_auth_cookie(response: Response, user: User) -> None:
    token = create_token({"sub": str(user.id), "email": user.email, "role": user.role, "display_name": user.display_name or ""})
    response.set_cookie(COOKIE_NAME, token, max_age=TOKEN_MAX_AGE, httponly=True, samesite="lax")


def _workspace_response(ws: Workspace, user: User) -> WorkspaceResponse:
    member = next((m for m in ws.members if m.user_id == user.id), None)
    return WorkspaceResponse(
        id=ws.id,
        name=ws.name,
        slug=ws.slug,
        description=ws.description,
        created_at=ws.created_at,
        my_role=member.role if member else ("superadmin" if user.role == "superadmin" else None),
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
        role="superadmin" if is_first else "user",
    )
    db.add(user)
    db.flush()

    if is_first:
        _create_default_workspace(db, user)
    else:
        # Add to Default workspace automatically
        default_ws = db.query(Workspace).filter(Workspace.slug == "default").first()
        if default_ws:
            db.add(WorkspaceMember(workspace_id=default_ws.id, user_id=user.id, role="user"))

    db.commit()
    db.refresh(user)
    _set_auth_cookie(response, user)
    return {"ok": True, "role": user.role}


@app.post("/api/auth/login")
def login(req: LoginRequest, response: Response, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.email == req.email).first()
    if not user or not user.hashed_password or not verify_password(req.password, user.hashed_password):
        raise HTTPException(401, "Invalid email or password")
    if not user.is_active:
        raise HTTPException(403, "Account disabled — contact your superadmin")
    _set_auth_cookie(response, user)
    return {"ok": True, "role": user.role}


@app.post("/api/auth/logout")
def logout(response: Response):
    response.delete_cookie(COOKIE_NAME)
    response.delete_cookie("lake_workspace_id")
    return {"ok": True}


@app.get("/api/auth/me", response_model=UserResponse)
def me(current_user: User = Depends(get_current_user)):
    return UserResponse.model_validate(current_user)


@app.patch("/api/auth/me", response_model=UserResponse)
def update_me(
    req: UpdateMeRequest,
    response: Response,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    if req.display_name is not None:
        name = req.display_name.strip()
        if name:
            current_user.display_name = name
    db.commit()
    db.refresh(current_user)
    _set_auth_cookie(response, current_user)
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


# ── Users (superadmin) ────────────────────────────────────────────────────────


@app.get("/api/users", response_model=list[UserResponse])
def list_users(db: Session = Depends(get_db), _: User = Depends(require_superadmin)):
    return [UserResponse.model_validate(u) for u in db.query(User).order_by(User.created_at).all()]


@app.patch("/api/users/{user_id}", response_model=UserResponse)
def update_user(
    user_id: str,
    req: UpdateUserRequest,
    db: Session = Depends(get_db),
    current_admin: User = Depends(require_superadmin),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        raise HTTPException(404, "User not found")
    if str(user.id) == str(current_admin.id) and req.role and req.role != "superadmin":
        raise HTTPException(400, "Cannot demote yourself")
    if req.role is not None:
        if req.role not in ("superadmin", "user"):
            raise HTTPException(400, "Global role must be 'superadmin' or 'user'")
        user.role = req.role
    if req.is_active is not None:
        user.is_active = req.is_active
    if req.display_name is not None:
        user.display_name = req.display_name
    db.commit()
    return UserResponse.model_validate(user)


# ── Workspaces ────────────────────────────────────────────────────────────────


@app.get("/api/workspaces", response_model=list[WorkspaceResponse])
def list_workspaces(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role == "superadmin":
        workspaces = db.query(Workspace).order_by(Workspace.created_at).all()
    else:
        workspaces = (
            db.query(Workspace)
            .join(WorkspaceMember, WorkspaceMember.workspace_id == Workspace.id)
            .filter(WorkspaceMember.user_id == current_user.id)
            .order_by(Workspace.created_at)
            .all()
        )
    return [_workspace_response(ws, current_user) for ws in workspaces]


@app.post("/api/workspaces", response_model=WorkspaceResponse)
def create_workspace(
    req: CreateWorkspaceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_superadmin),
):
    slug = _unique_slug(db, _slugify(req.name))
    ws = Workspace(
        name=req.name.strip(),
        slug=slug,
        description=req.description,
        created_by=current_user.id,
    )
    db.add(ws)
    db.flush()
    db.add(WorkspaceMember(workspace_id=ws.id, user_id=current_user.id, role="admin"))
    db.commit()
    db.refresh(ws)
    return _workspace_response(ws, current_user)


@app.get("/api/workspaces/{workspace_id}", response_model=WorkspaceResponse)
def get_workspace(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    # check membership unless superadmin
    if current_user.role != "superadmin":
        member = next((m for m in ws.members if m.user_id == current_user.id), None)
        if not member:
            raise HTTPException(403, "Not a member of this workspace")
    return _workspace_response(ws, current_user)


@app.patch("/api/workspaces/{workspace_id}", response_model=WorkspaceResponse)
def update_workspace(
    workspace_id: str,
    req: UpdateWorkspaceRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    # Must be workspace admin or superadmin
    if current_user.role != "superadmin":
        member = next((m for m in ws.members if m.user_id == current_user.id and m.role == "admin"), None)
        if not member:
            raise HTTPException(403, "Workspace admin access required")
    if req.name is not None:
        ws.name = req.name.strip()
    if req.description is not None:
        ws.description = req.description
    db.commit()
    return _workspace_response(ws, current_user)


@app.delete("/api/workspaces/{workspace_id}")
def delete_workspace(
    workspace_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    if ws.slug == "default":
        raise HTTPException(400, "Cannot delete the Default workspace")
    db.delete(ws)
    db.commit()
    return {"ok": True}


# ── Workspace members ─────────────────────────────────────────────────────────


@app.get("/api/workspaces/{workspace_id}/members", response_model=list[WorkspaceMemberResponse])
def list_members(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    if current_user.role != "superadmin":
        if not any(m.user_id == current_user.id for m in ws.members):
            raise HTTPException(403, "Not a member of this workspace")
    return [
        WorkspaceMemberResponse(
            user_id=m.user_id,
            email=m.user.email,
            display_name=m.user.display_name,
            role=m.role,
            joined_at=m.joined_at,
        )
        for m in ws.members
    ]


@app.post("/api/workspaces/{workspace_id}/members")
def add_member(
    workspace_id: str,
    req: AddMemberRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    if current_user.role != "superadmin":
        member_check = next((m for m in ws.members if m.user_id == current_user.id and m.role == "admin"), None)
        if not member_check:
            raise HTTPException(403, "Workspace admin access required")

    target = db.query(User).filter(User.email == req.email).first()
    if not target:
        raise HTTPException(404, "User not found — they must register first")

    existing = db.query(WorkspaceMember).filter_by(workspace_id=workspace_id, user_id=target.id).first()
    if existing:
        existing.role = req.role
    else:
        db.add(WorkspaceMember(workspace_id=ws.id, user_id=target.id, role=req.role))
    db.commit()
    return {"ok": True}


@app.patch("/api/workspaces/{workspace_id}/members/{user_id}")
def update_member(
    workspace_id: str,
    user_id: str,
    req: UpdateMemberRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    if current_user.role != "superadmin":
        admin_check = next((m for m in ws.members if m.user_id == current_user.id and m.role == "admin"), None)
        if not admin_check:
            raise HTTPException(403, "Workspace admin access required")

    member = db.query(WorkspaceMember).filter_by(workspace_id=workspace_id, user_id=user_id).first()
    if not member:
        raise HTTPException(404, "Member not found")
    if str(member.user_id) == str(current_user.id):
        raise HTTPException(400, "Cannot change your own workspace role")
    member.role = req.role
    db.commit()
    return {"ok": True}


@app.delete("/api/workspaces/{workspace_id}/members/{user_id}")
def remove_member(
    workspace_id: str,
    user_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
    if not ws:
        raise HTTPException(404, "Workspace not found")
    if current_user.role != "superadmin":
        admin_check = next((m for m in ws.members if m.user_id == current_user.id and m.role == "admin"), None)
        if not admin_check:
            raise HTTPException(403, "Workspace admin access required")

    member = db.query(WorkspaceMember).filter_by(workspace_id=workspace_id, user_id=user_id).first()
    if not member:
        raise HTTPException(404, "Member not found")
    if str(member.user_id) == str(current_user.id):
        raise HTTPException(400, "Cannot remove yourself from a workspace")
    db.delete(member)
    db.commit()
    return {"ok": True}


# ── Health ────────────────────────────────────────────────────────────────────


@app.get("/api/health")
def health():
    return {"status": "ok"}
