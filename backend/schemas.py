from __future__ import annotations
import re
import uuid
from datetime import datetime
from typing import Optional
from pydantic import BaseModel, EmailStr, field_validator


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str
    display_name: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class UserResponse(BaseModel):
    id: uuid.UUID
    email: str
    display_name: Optional[str]
    role: str
    is_active: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class UpdateUserRequest(BaseModel):
    role: Optional[str] = None
    is_active: Optional[bool] = None
    display_name: Optional[str] = None


class UpdateMeRequest(BaseModel):
    display_name: Optional[str] = None


# ── Workspaces ────────────────────────────────────────────────────────────────

class CreateWorkspaceRequest(BaseModel):
    name: str
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Workspace name cannot be empty")
        return v


class WorkspaceMemberResponse(BaseModel):
    user_id: uuid.UUID
    email: str
    display_name: Optional[str]
    role: str
    joined_at: datetime

    model_config = {"from_attributes": True}


class WorkspaceResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    description: Optional[str]
    created_at: datetime
    # caller's role within this workspace (populated on the fly)
    my_role: Optional[str] = None

    model_config = {"from_attributes": True}


class AddMemberRequest(BaseModel):
    email: EmailStr
    role: str = "user"

    @field_validator("role")
    @classmethod
    def valid_role(cls, v: str) -> str:
        if v not in ("admin", "user"):
            raise ValueError("role must be 'admin' or 'user'")
        return v


class UpdateMemberRequest(BaseModel):
    role: str

    @field_validator("role")
    @classmethod
    def valid_role(cls, v: str) -> str:
        if v not in ("admin", "user"):
            raise ValueError("role must be 'admin' or 'user'")
        return v


class UpdateWorkspaceRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


# ── Catalogs ──────────────────────────────────────────────────────────────────

class CreateCatalogRequest(BaseModel):
    name: str
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Catalog name cannot be empty")
        return v


class UpdateCatalogRequest(BaseModel):
    name: Optional[str] = None
    description: Optional[str] = None


class CreateSchemaRequest(BaseModel):
    name: str
    description: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Schema name cannot be empty")
        return v


class UpdateSchemaRequest(BaseModel):
    description: Optional[str] = None


class ColumnDef(BaseModel):
    name: str
    type: str
    description: Optional[str] = None
    deprecated: bool = False


class CreateTableRequest(BaseModel):
    name: str
    description: Optional[str] = None
    s3_path_pattern: Optional[str] = None

    @field_validator("name")
    @classmethod
    def name_not_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("Table name cannot be empty")
        return v


class UpdateTableRequest(BaseModel):
    description: Optional[str] = None
    s3_path_pattern: Optional[str] = None
    column_defs: Optional[list[ColumnDef]] = None


class CatalogTableResponse(BaseModel):
    id: uuid.UUID
    schema_id: uuid.UUID
    name: str
    slug: str
    description: Optional[str]
    s3_path_pattern: Optional[str]
    column_defs: Optional[list]
    schema_drift: bool
    created_at: datetime

    model_config = {"from_attributes": True}


class CatalogSchemaResponse(BaseModel):
    id: uuid.UUID
    catalog_id: uuid.UUID
    name: str
    slug: str
    description: Optional[str]
    tier: str
    created_at: datetime
    tables: list[CatalogTableResponse] = []

    model_config = {"from_attributes": True}


class CatalogResponse(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    description: Optional[str]
    owner_workspace_id: uuid.UUID
    created_at: datetime
    schemas: list[CatalogSchemaResponse] = []
    my_access: Optional[str] = None  # 'owner' | 'write' | 'read' | None

    model_config = {"from_attributes": True}


class RequestAccessRequest(BaseModel):
    mode: str = "read"
    message: Optional[str] = None

    @field_validator("mode")
    @classmethod
    def valid_mode(cls, v: str) -> str:
        if v not in ("read", "write"):
            raise ValueError("mode must be 'read' or 'write'")
        return v


class ReviewAccessRequest(BaseModel):
    status: str

    @field_validator("status")
    @classmethod
    def valid_status(cls, v: str) -> str:
        if v not in ("approved", "rejected"):
            raise ValueError("status must be 'approved' or 'rejected'")
        return v


class CatalogAccessResponse(BaseModel):
    id: uuid.UUID
    catalog_id: uuid.UUID
    workspace_id: uuid.UUID
    workspace_name: Optional[str] = None
    mode: str
    status: str
    requested_by_email: Optional[str] = None
    requested_at: datetime
    reviewed_at: Optional[datetime]

    model_config = {"from_attributes": True}
