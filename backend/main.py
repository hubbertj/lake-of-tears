from __future__ import annotations

import os
import re
import threading
from datetime import UTC

from auth import (
    COOKIE_NAME,
    TOKEN_MAX_AGE,
    create_token,
    get_current_user,
    hash_password,
    require_superadmin,
    verify_password,
)
from database import engine, get_db
from email_service import send_access_requested, send_access_reviewed
from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from models import (
    Base,
    Catalog,
    CatalogAccess,
    CatalogSchema,
    CatalogTable,
    User,
    Workspace,
    WorkspaceMember,
)
from oauth import enabled_providers, get_oauth_redirect, handle_oauth_callback
from schemas import (
    AddMemberRequest,
    CatalogAccessResponse,
    CatalogResponse,
    CatalogSchemaResponse,
    CatalogTableResponse,
    CreateCatalogRequest,
    CreateSchemaRequest,
    CreateTableRequest,
    CreateWorkspaceRequest,
    LoginRequest,
    RegisterRequest,
    RequestAccessRequest,
    ReviewAccessRequest,
    UpdateCatalogRequest,
    UpdateMemberRequest,
    UpdateMeRequest,
    UpdateSchemaRequest,
    UpdateTableRequest,
    UpdateUserRequest,
    UpdateWorkspaceRequest,
    UserResponse,
    WorkspaceMemberResponse,
    WorkspaceResponse,
)
from sqlalchemy.orm import Session

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
    token = create_token(
        {
            "sub": str(user.id),
            "email": user.email,
            "role": user.role,
            "display_name": user.display_name or "",
        }
    )
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
    if (
        not user
        or not user.hashed_password
        or not verify_password(req.password, user.hashed_password)
    ):
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
def oauth_callback(
    provider: str, request: Request, response: Response, db: Session = Depends(get_db)
):
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
        member = next(
            (m for m in ws.members if m.user_id == current_user.id and m.role == "admin"), None
        )
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
        member_check = next(
            (m for m in ws.members if m.user_id == current_user.id and m.role == "admin"), None
        )
        if not member_check:
            raise HTTPException(403, "Workspace admin access required")

    target = db.query(User).filter(User.email == req.email).first()
    if not target:
        raise HTTPException(404, "User not found — they must register first")

    existing = (
        db.query(WorkspaceMember).filter_by(workspace_id=workspace_id, user_id=target.id).first()
    )
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
        admin_check = next(
            (m for m in ws.members if m.user_id == current_user.id and m.role == "admin"), None
        )
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
        admin_check = next(
            (m for m in ws.members if m.user_id == current_user.id and m.role == "admin"), None
        )
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


# ── Catalog helpers ───────────────────────────────────────────────────────────

_MEDALLION_TIERS = ("bronze", "silver", "gold")


def _slugify(name: str) -> str:
    slug = re.sub(r"[^\w\s-]", "", name.lower())
    return re.sub(r"[\s_-]+", "-", slug).strip("-") or "item"


def _unique_catalog_slug(db: Session, base: str) -> str:
    slug, i = base, 0
    while db.query(Catalog).filter(Catalog.slug == slug).first():
        i += 1
        slug = f"{base}-{i}"
    return slug


def _seed_medallion_schemas(db: Session, catalog: Catalog) -> None:
    for tier in _MEDALLION_TIERS:
        db.add(
            CatalogSchema(
                catalog_id=catalog.id,
                name=tier.capitalize(),
                slug=tier,
                tier=tier,
            )
        )


def _caller_access(db: Session, catalog: Catalog, user: User) -> str | None:
    if user.role == "superadmin":
        return "owner"
    ws_ids = [str(m.workspace_id) for m in user.workspace_memberships]
    if str(catalog.owner_workspace_id) in ws_ids:
        return "owner"
    grant = (
        db.query(CatalogAccess)
        .filter(
            CatalogAccess.catalog_id == catalog.id,
            CatalogAccess.workspace_id.in_(ws_ids),
            CatalogAccess.status == "approved",
        )
        .first()
    )
    return grant.mode if grant else None


def _assert_catalog_access(db: Session, catalog: Catalog, user: User, require: str = "read") -> str:
    access = _caller_access(db, catalog, user)
    if access is None:
        raise HTTPException(403, "No access to this catalog")
    if require == "write" and access not in ("owner", "write"):
        raise HTTPException(403, "Write access required")
    if require == "owner" and access != "owner":
        raise HTTPException(403, "Catalog owner access required")
    return access


def _owner_emails(db: Session, catalog: Catalog) -> list[str]:
    members = (
        db.query(WorkspaceMember)
        .filter(WorkspaceMember.workspace_id == catalog.owner_workspace_id)
        .all()
    )
    return [m.user.email for m in members if m.user and m.user.is_active]


def _catalog_response(catalog: Catalog, user: User, db: Session) -> CatalogResponse:
    access = _caller_access(db, catalog, user)
    return CatalogResponse(
        id=catalog.id,
        name=catalog.name,
        slug=catalog.slug,
        description=catalog.description,
        owner_workspace_id=catalog.owner_workspace_id,
        created_at=catalog.created_at,
        schemas=[
            CatalogSchemaResponse(
                id=s.id,
                catalog_id=s.catalog_id,
                name=s.name,
                slug=s.slug,
                description=s.description,
                tier=s.tier,
                created_at=s.created_at,
                tables=[CatalogTableResponse.model_validate(t) for t in s.tables],
            )
            for s in catalog.schemas
        ],
        my_access=access,
    )


# ── Catalog CRUD ──────────────────────────────────────────────────────────────


@app.get("/api/catalogs", response_model=list[CatalogResponse])
def list_catalogs(db: Session = Depends(get_db), current_user: User = Depends(get_current_user)):
    if current_user.role == "superadmin":
        catalogs = db.query(Catalog).order_by(Catalog.created_at).all()
    else:
        ws_ids = [m.workspace_id for m in current_user.workspace_memberships]
        shared_ids = db.query(CatalogAccess.catalog_id).filter(
            CatalogAccess.workspace_id.in_(ws_ids),
            CatalogAccess.status == "approved",
        )
        from sqlalchemy import or_

        catalogs = (
            db.query(Catalog)
            .filter(or_(Catalog.owner_workspace_id.in_(ws_ids), Catalog.id.in_(shared_ids)))
            .order_by(Catalog.created_at)
            .all()
        )
    return [_catalog_response(c, current_user, db) for c in catalogs]


@app.post("/api/catalogs", response_model=CatalogResponse)
def create_catalog(
    req: CreateCatalogRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    ws = _resolve_workspace(req.workspace_id, current_user, db)
    slug = _unique_catalog_slug(db, _slugify(req.name))
    catalog = Catalog(
        name=req.name.strip(),
        slug=slug,
        description=req.description,
        owner_workspace_id=ws.id,
        created_by=current_user.id,
    )
    db.add(catalog)
    db.flush()
    _seed_medallion_schemas(db, catalog)
    db.commit()
    db.refresh(catalog)
    return _catalog_response(catalog, current_user, db)


def _resolve_workspace(workspace_id: str | None, user: User, db: Session) -> Workspace:
    if workspace_id:
        ws = db.query(Workspace).filter(Workspace.id == workspace_id).first()
        if not ws:
            raise HTTPException(404, "Workspace not found")
        if user.role != "superadmin":
            member = next(
                (
                    m
                    for m in user.workspace_memberships
                    if str(m.workspace_id) == workspace_id and m.role == "admin"
                ),
                None,
            )
            if not member:
                raise HTTPException(403, "Workspace admin access required")
        return ws
    # Fallback: first admin workspace, or default for superadmin
    if user.role == "superadmin":
        ws = db.query(Workspace).order_by(Workspace.created_at).first()
    else:
        member = next((m for m in user.workspace_memberships if m.role == "admin"), None)
        if not member:
            raise HTTPException(403, "Workspace admin access required to create a catalog")
        ws = db.query(Workspace).filter(Workspace.id == member.workspace_id).first()
    if not ws:
        raise HTTPException(400, "No eligible workspace found")
    return ws


@app.get("/api/catalogs/{catalog_id}", response_model=CatalogResponse)
def get_catalog(
    catalog_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = db.query(Catalog).filter(Catalog.id == catalog_id).first()
    if not catalog:
        raise HTTPException(404, "Catalog not found")
    _assert_catalog_access(db, catalog, current_user, "read")
    return _catalog_response(catalog, current_user, db)


@app.patch("/api/catalogs/{catalog_id}", response_model=CatalogResponse)
def update_catalog(
    catalog_id: str,
    req: UpdateCatalogRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = db.query(Catalog).filter(Catalog.id == catalog_id).first()
    if not catalog:
        raise HTTPException(404, "Catalog not found")
    _assert_catalog_access(db, catalog, current_user, "owner")
    if req.name is not None:
        catalog.name = req.name.strip()
    if req.description is not None:
        catalog.description = req.description
    db.commit()
    db.refresh(catalog)
    return _catalog_response(catalog, current_user, db)


@app.delete("/api/catalogs/{catalog_id}")
def delete_catalog(
    catalog_id: str,
    db: Session = Depends(get_db),
    _: User = Depends(require_superadmin),
):
    catalog = db.query(Catalog).filter(Catalog.id == catalog_id).first()
    if not catalog:
        raise HTTPException(404, "Catalog not found")
    db.delete(catalog)
    db.commit()
    return {"ok": True}


# ── Schema CRUD ───────────────────────────────────────────────────────────────


@app.get("/api/catalogs/{catalog_id}/schemas", response_model=list[CatalogSchemaResponse])
def list_schemas(
    catalog_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = db.query(Catalog).filter(Catalog.id == catalog_id).first()
    if not catalog:
        raise HTTPException(404, "Catalog not found")
    _assert_catalog_access(db, catalog, current_user, "read")
    return [
        CatalogSchemaResponse(
            id=s.id,
            catalog_id=s.catalog_id,
            name=s.name,
            slug=s.slug,
            description=s.description,
            tier=s.tier,
            created_at=s.created_at,
            tables=[CatalogTableResponse.model_validate(t) for t in s.tables],
        )
        for s in catalog.schemas
    ]


@app.post("/api/catalogs/{catalog_id}/schemas", response_model=CatalogSchemaResponse)
def create_schema(
    catalog_id: str,
    req: CreateSchemaRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = db.query(Catalog).filter(Catalog.id == catalog_id).first()
    if not catalog:
        raise HTTPException(404, "Catalog not found")
    _assert_catalog_access(db, catalog, current_user, "write")
    slug = _slugify(req.name)
    if db.query(CatalogSchema).filter_by(catalog_id=catalog_id, slug=slug).first():
        raise HTTPException(400, f"Schema slug '{slug}' already exists in this catalog")
    schema = CatalogSchema(
        catalog_id=catalog.id, name=req.name.strip(), slug=slug, description=req.description
    )
    db.add(schema)
    db.commit()
    db.refresh(schema)
    return CatalogSchemaResponse(
        id=schema.id,
        catalog_id=schema.catalog_id,
        name=schema.name,
        slug=schema.slug,
        description=schema.description,
        tier=schema.tier,
        created_at=schema.created_at,
        tables=[],
    )


@app.patch("/api/catalogs/{catalog_id}/schemas/{schema_id}", response_model=CatalogSchemaResponse)
def update_schema(
    catalog_id: str,
    schema_id: str,
    req: UpdateSchemaRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = db.query(Catalog).filter(Catalog.id == catalog_id).first()
    if not catalog:
        raise HTTPException(404, "Catalog not found")
    _assert_catalog_access(db, catalog, current_user, "write")
    schema = db.query(CatalogSchema).filter_by(id=schema_id, catalog_id=catalog_id).first()
    if not schema:
        raise HTTPException(404, "Schema not found")
    if schema.tier in _MEDALLION_TIERS:
        raise HTTPException(400, f"Cannot modify the locked '{schema.tier}' medallion schema")
    if req.description is not None:
        schema.description = req.description
    db.commit()
    db.refresh(schema)
    return CatalogSchemaResponse(
        id=schema.id,
        catalog_id=schema.catalog_id,
        name=schema.name,
        slug=schema.slug,
        description=schema.description,
        tier=schema.tier,
        created_at=schema.created_at,
        tables=[CatalogTableResponse.model_validate(t) for t in schema.tables],
    )


@app.delete("/api/catalogs/{catalog_id}/schemas/{schema_id}")
def delete_schema(
    catalog_id: str,
    schema_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = db.query(Catalog).filter(Catalog.id == catalog_id).first()
    if not catalog:
        raise HTTPException(404, "Catalog not found")
    _assert_catalog_access(db, catalog, current_user, "owner")
    schema = db.query(CatalogSchema).filter_by(id=schema_id, catalog_id=catalog_id).first()
    if not schema:
        raise HTTPException(404, "Schema not found")
    if schema.tier in _MEDALLION_TIERS:
        raise HTTPException(400, f"Cannot delete the locked '{schema.tier}' medallion schema")
    db.delete(schema)
    db.commit()
    return {"ok": True}


# ── Table CRUD ────────────────────────────────────────────────────────────────


@app.get(
    "/api/catalogs/{catalog_id}/schemas/{schema_id}/tables",
    response_model=list[CatalogTableResponse],
)
def list_tables(
    catalog_id: str,
    schema_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = db.query(Catalog).filter(Catalog.id == catalog_id).first()
    if not catalog:
        raise HTTPException(404, "Catalog not found")
    _assert_catalog_access(db, catalog, current_user, "read")
    schema = db.query(CatalogSchema).filter_by(id=schema_id, catalog_id=catalog_id).first()
    if not schema:
        raise HTTPException(404, "Schema not found")
    return [CatalogTableResponse.model_validate(t) for t in schema.tables]


@app.post(
    "/api/catalogs/{catalog_id}/schemas/{schema_id}/tables", response_model=CatalogTableResponse
)
def create_table(
    catalog_id: str,
    schema_id: str,
    req: CreateTableRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = db.query(Catalog).filter(Catalog.id == catalog_id).first()
    if not catalog:
        raise HTTPException(404, "Catalog not found")
    _assert_catalog_access(db, catalog, current_user, "write")
    schema = db.query(CatalogSchema).filter_by(id=schema_id, catalog_id=catalog_id).first()
    if not schema:
        raise HTTPException(404, "Schema not found")
    slug = _slugify(req.name)
    if db.query(CatalogTable).filter_by(schema_id=schema_id, slug=slug).first():
        raise HTTPException(400, f"Table slug '{slug}' already exists in this schema")
    table = CatalogTable(
        schema_id=schema.id,
        name=req.name.strip(),
        slug=slug,
        description=req.description,
        s3_path_pattern=req.s3_path_pattern,
        column_defs=[],
    )
    db.add(table)
    db.commit()
    db.refresh(table)
    return CatalogTableResponse.model_validate(table)


@app.patch(
    "/api/catalogs/{catalog_id}/schemas/{schema_id}/tables/{table_id}",
    response_model=CatalogTableResponse,
)
def update_table(
    catalog_id: str,
    schema_id: str,
    table_id: str,
    req: UpdateTableRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = db.query(Catalog).filter(Catalog.id == catalog_id).first()
    if not catalog:
        raise HTTPException(404, "Catalog not found")
    _assert_catalog_access(db, catalog, current_user, "write")
    table = db.query(CatalogTable).filter_by(id=table_id, schema_id=schema_id).first()
    if not table:
        raise HTTPException(404, "Table not found")
    if req.description is not None:
        table.description = req.description
    if req.s3_path_pattern is not None:
        table.s3_path_pattern = req.s3_path_pattern
    if req.column_defs is not None:
        table.column_defs = [c.model_dump() for c in req.column_defs]
    db.commit()
    db.refresh(table)
    return CatalogTableResponse.model_validate(table)


@app.delete("/api/catalogs/{catalog_id}/schemas/{schema_id}/tables/{table_id}")
def delete_table(
    catalog_id: str,
    schema_id: str,
    table_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = db.query(Catalog).filter(Catalog.id == catalog_id).first()
    if not catalog:
        raise HTTPException(404, "Catalog not found")
    _assert_catalog_access(db, catalog, current_user, "write")
    table = db.query(CatalogTable).filter_by(id=table_id, schema_id=schema_id).first()
    if not table:
        raise HTTPException(404, "Table not found")
    db.delete(table)
    db.commit()
    return {"ok": True}


# ── Access management ─────────────────────────────────────────────────────────


@app.get("/api/catalogs/{catalog_id}/access", response_model=list[CatalogAccessResponse])
def list_access(
    catalog_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = db.query(Catalog).filter(Catalog.id == catalog_id).first()
    if not catalog:
        raise HTTPException(404, "Catalog not found")
    _assert_catalog_access(db, catalog, current_user, "owner")
    grants = db.query(CatalogAccess).filter(CatalogAccess.catalog_id == catalog_id).all()
    result = []
    for g in grants:
        ws = db.query(Workspace).filter(Workspace.id == g.workspace_id).first()
        requester = (
            db.query(User).filter(User.id == g.requested_by).first() if g.requested_by else None
        )
        result.append(
            CatalogAccessResponse(
                id=g.id,
                catalog_id=g.catalog_id,
                workspace_id=g.workspace_id,
                workspace_name=ws.name if ws else None,
                mode=g.mode,
                status=g.status,
                requested_by_email=requester.email if requester else None,
                requested_at=g.requested_at,
                reviewed_at=g.reviewed_at,
            )
        )
    return result


@app.post("/api/catalogs/{catalog_id}/access/request")
def request_access(
    catalog_id: str,
    req: RequestAccessRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = db.query(Catalog).filter(Catalog.id == catalog_id).first()
    if not catalog:
        raise HTTPException(404, "Catalog not found")

    # caller must be a workspace admin
    admin_membership = next(
        (m for m in current_user.workspace_memberships if m.role == "admin"), None
    )
    if not admin_membership and current_user.role != "superadmin":
        raise HTTPException(403, "Workspace admin access required to request catalog access")

    ws_id = admin_membership.workspace_id if admin_membership else catalog.owner_workspace_id
    if str(ws_id) == str(catalog.owner_workspace_id):
        raise HTTPException(400, "Owner workspace already has full access")

    existing = db.query(CatalogAccess).filter_by(catalog_id=catalog_id, workspace_id=ws_id).first()
    if existing:
        if existing.status == "approved":
            raise HTTPException(400, "Access already approved")
        existing.mode = req.mode
        existing.status = "pending"
        existing.requested_by = current_user.id
        existing.requested_at = __import__("datetime").datetime.now(
            __import__("datetime").timezone.utc
        )
    else:
        db.add(
            CatalogAccess(
                catalog_id=catalog.id,
                workspace_id=ws_id,
                mode=req.mode,
                status="pending",
                requested_by=current_user.id,
            )
        )
    db.commit()

    owner_emails = _owner_emails(db, catalog)
    ws = db.query(Workspace).filter(Workspace.id == ws_id).first()
    threading.Thread(
        target=send_access_requested,
        args=(owner_emails, ws.name if ws else "Unknown", catalog.name, req.mode),
        daemon=True,
    ).start()

    return {"ok": True}


@app.patch("/api/catalogs/{catalog_id}/access/{access_id}")
def review_access(
    catalog_id: str,
    access_id: str,
    req: ReviewAccessRequest,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = db.query(Catalog).filter(Catalog.id == catalog_id).first()
    if not catalog:
        raise HTTPException(404, "Catalog not found")
    _assert_catalog_access(db, catalog, current_user, "owner")

    grant = db.query(CatalogAccess).filter_by(id=access_id, catalog_id=catalog_id).first()
    if not grant:
        raise HTTPException(404, "Access grant not found")

    grant.status = req.status
    grant.reviewed_by = current_user.id
    from datetime import datetime

    grant.reviewed_at = datetime.now(UTC)
    db.commit()

    requester = (
        db.query(User).filter(User.id == grant.requested_by).first() if grant.requested_by else None
    )
    if requester:
        threading.Thread(
            target=send_access_reviewed,
            args=(requester.email, catalog.name, req.status),
            daemon=True,
        ).start()

    return {"ok": True}


@app.delete("/api/catalogs/{catalog_id}/access/{access_id}")
def revoke_access(
    catalog_id: str,
    access_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    catalog = db.query(Catalog).filter(Catalog.id == catalog_id).first()
    if not catalog:
        raise HTTPException(404, "Catalog not found")
    _assert_catalog_access(db, catalog, current_user, "owner")
    grant = db.query(CatalogAccess).filter_by(id=access_id, catalog_id=catalog_id).first()
    if not grant:
        raise HTTPException(404, "Access grant not found")
    db.delete(grant)
    db.commit()
    return {"ok": True}


@app.get("/api/workspaces/{workspace_id}/catalogs", response_model=list[CatalogResponse])
def workspace_catalogs(
    workspace_id: str,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    from sqlalchemy import or_

    shared_ids = db.query(CatalogAccess.catalog_id).filter(
        CatalogAccess.workspace_id == workspace_id, CatalogAccess.status == "approved"
    )
    catalogs = (
        db.query(Catalog)
        .filter(or_(Catalog.owner_workspace_id == workspace_id, Catalog.id.in_(shared_ids)))
        .order_by(Catalog.created_at)
        .all()
    )
    return [_catalog_response(c, current_user, db) for c in catalogs]
