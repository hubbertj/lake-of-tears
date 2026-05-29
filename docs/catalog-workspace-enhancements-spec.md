# Catalog & Workspace Enhancements Spec

## Overview

Three related changes to tighten up catalog/workspace behavior:

1. Auto-create a `default` catalog when a workspace is created
2. Enforce unique catalog names within a workspace
3. One-time cleanup of duplicate catalogs in the live database

---

## 1. Auto-create Default Catalog on Workspace Creation

### Behavior

When a workspace is created, a catalog named `default` is automatically created and owned by that workspace. It is seeded with the standard medallion schemas (bronze, silver, gold) like any manually created catalog.

### Scope

Applies in two places:

- **`POST /api/workspaces`** — user-triggered workspace creation (superadmin only)
- **`_create_default_workspace()`** — system function called on first user registration to create the Default workspace

### Implementation notes

- `created_by` on the auto-catalog should be set to the user triggering the workspace creation
- Catalog name is `"default"`, slug is derived via `_unique_catalog_slug` as normal
- No description set on the auto-catalog
- Runs inside the same DB transaction as workspace creation — if catalog creation fails, the whole workspace creation rolls back

### Backfill existing workspaces

On deploy, any workspace that does not already have a catalog named `default` (case-insensitive) should have one created automatically. This is a one-time migration that runs at backend startup (or as a standalone script) before the API begins serving requests.

- Check each workspace: if no catalog with `lower(name) == "default"` exists under `owner_workspace_id`, create one
- Same rules as above: medallion schemas seeded, `created_by` set to the workspace's `created_by` user (fall back to any superadmin if that user no longer exists)
- Idempotent — safe to run multiple times

---

## 2. Unique Catalog Name Per Workspace

### Behavior

Within a given workspace, no two catalogs may share the same name (case-insensitive). Attempting to create a duplicate returns a `409 Conflict` error.

### Constraint

- Scoped to `owner_workspace_id` — the same catalog name is allowed in different workspaces
- Comparison is case-insensitive (`lower(name) == lower(requested_name)`)
- Enforced at the app layer in `POST /api/catalogs`, not as a DB unique constraint (since `name` is not globally unique)

### Error response

```json
HTTP 409
{ "detail": "A catalog with that name already exists in this workspace" }
```

---

## 3. One-Time Database Cleanup

### Duplicates found (as of 2026-05-29)

| Workspace | Catalog name | Copies | Action |
|-----------|-------------|--------|--------|
| Default   | `prd`       | 3      | Delete 2, keep 1 |

No schemas or tables are attached to any of the duplicates; no data loss.

### Open questions (to resolve before implementation)

1. **`should-be-in-production-workspace`** sits in Default workspace — artifact from the workspace-assignment bug. Delete it as test junk, or leave it?
2. **`_create_default_workspace`** — should the auto-default catalog also be created when the system auto-creates the Default workspace on first user registration, or only for user-triggered workspace creation via the API?
