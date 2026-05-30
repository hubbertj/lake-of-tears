# Workspace Deletion Spec

## Overview

Superadmins can delete any workspace except the system-created **Default** workspace. Deletion has two modes:

- **Hard delete (now):** Immediately and permanently destroys the workspace and all catalogs it owns. Requires typed-name confirmation.
- **Soft delete (inactive):** Marks the workspace inactive. It is permanently purged after a configurable grace period (default: 30 days). The workspace can be restored at any time during the grace period.

This spec covers data model changes, API enforcement, cascading catalog deletion, member redirect behavior, and UI changes.

---

## 1. Invariants

| Rule | Detail |
|------|--------|
| Default workspace is undeletable | The system workspace (`is_system = true`) cannot be deleted by anyone, including superadmins. The API enforces this regardless of role. |
| Only superadmins can delete workspaces | Workspace admins have no delete capability — this action is superadmin-only. |
| Cascade to owned catalogs | When a workspace is deleted (either mode), all catalogs it owns are deleted via the same soft/hard delete process used for catalog deletion (see catalog-spec.md §3/§4). |
| Member redirect on deletion | When a workspace is deleted (either mode), members lose access. On their next navigation the system redirects them to their next eligible workspace (lowest `id` among remaining active memberships). If no other workspace exists, they are redirected to the Default workspace. No notification is shown to members. |

---

## 2. Data Model Changes

### `workspaces` table — new columns

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `is_system` | `BOOLEAN` | `false` | `true` only on the Default workspace; set at creation, never changed |
| `status` | `ENUM('active', 'inactive')` | `'active'` | `inactive` = soft-deleted, pending purge |
| `deleted_at` | `TIMESTAMP NULL` | `NULL` | Set when soft-deleted |
| `scheduled_purge_at` | `TIMESTAMP NULL` | `NULL` | `deleted_at + <grace_period_days>` |

### Migration

```sql
ALTER TABLE workspaces
  ADD COLUMN IF NOT EXISTS is_system BOOLEAN NOT NULL DEFAULT FALSE,
  ADD COLUMN IF NOT EXISTS status VARCHAR(16) NOT NULL DEFAULT 'active',
  ADD COLUMN IF NOT EXISTS deleted_at TIMESTAMP NULL,
  ADD COLUMN IF NOT EXISTS scheduled_purge_at TIMESTAMP NULL;
```

### Backfill

```sql
UPDATE workspaces
SET is_system = TRUE
WHERE LOWER(name) = 'default'
  AND id = (SELECT id FROM workspaces WHERE LOWER(name) = 'default' ORDER BY created_at ASC LIMIT 1);
```

### System settings table — new config key

A new key is added to the existing system settings store:

| Key | Type | Default | Description |
|-----|------|---------|-------------|
| `workspace_inactive_grace_period_days` | `INTEGER` | `30` | Days before an inactive workspace is permanently purged. `0` disables soft delete (hard delete only). |

---

## 3. Hard Delete (Delete Now)

Triggered when a superadmin confirms immediate deletion via the typed-name confirmation dialog.

### Confirmation dialog

A modal is shown before any delete occurs:

```
┌──────────────────────────────────────────────────────────┐
│  Delete workspace "analytics"?                           │
│                                                          │
│  ⚠️  This action is permanent and cannot be undone.      │
│  All catalogs owned by this workspace will be deleted.   │
│                                                          │
│  Type the workspace name to confirm:                     │
│  ┌──────────────────────────────────────────────┐        │
│  │                                              │        │
│  └──────────────────────────────────────────────┘        │
│                                                          │
│                          [Cancel]  [Delete permanently]  │
└──────────────────────────────────────────────────────────┘
```

- The **Delete permanently** button is disabled until the typed name matches the workspace name exactly (case-sensitive).
- The dialog does not close on backdrop click.

### Behavior on confirmation

1. All catalogs owned by the workspace are hard-purged (same process as `POST /api/catalogs/{id}/purge` — deletes DB records and all MinIO objects under the catalog's slug prefix).
2. All workspace memberships (`workspace_members` rows) are deleted.
3. The workspace record is deleted.
4. Members are silently redirected per §1.

---

## 4. Soft Delete (Inactive)

Triggered when a superadmin clicks **Mark inactive** on a workspace that is currently active.

A standard inline "Are you sure?" confirmation is shown — no typed-name input required. The dialog notes: *"The workspace and its catalogs will be permanently deleted in X days. You can restore it before then."*

### Behavior on trigger

1. `status` → `'inactive'`, `deleted_at` → now, `scheduled_purge_at` → `now + grace_period_days`.
2. All catalogs owned by the workspace are soft-deleted (same process as catalog soft-delete: `deleted_at` and `scheduled_purge_at` set, catalog removed from explorer across all workspaces).
3. The workspace is hidden from all non-superadmin views immediately.
4. Members are silently redirected per §1.

### During the grace period

- The workspace appears only in the Superadmin > Workspaces list with:
  - Status badge: `inactive`
  - Countdown label: *"Permanent deletion: Jun 28 2026 at 2:32 PM EDT"*
- The workspace name remains reserved — no new workspace with the same name (case-insensitive) can be created while the inactive record exists.
- No new members can be added to an inactive workspace.

### Restore (reactivation)

A superadmin can click **Restore** at any time during the grace period:

1. `status` → `'active'`, `deleted_at` → `NULL`, `scheduled_purge_at` → `NULL`.
2. All soft-deleted catalogs owned by the workspace are reactivated (their `deleted_at` and `scheduled_purge_at` are cleared; suspended `CatalogAccess` grants are restored).
3. The workspace and its catalogs reappear in all relevant views.
4. Members regain access — their membership rows were never deleted.

### Scheduled purge

A background Airflow DAG (`purge_inactive_workspaces_dag.py`, runs hourly) checks for workspaces where `scheduled_purge_at <= now` and `status = 'inactive'`, then hard-purges each one via the same process as §3 (cascade catalog purge → delete memberships → delete workspace record).

---

## 5. Backend API

### Workspace deletion

```
DELETE /api/admin/workspaces/{workspace_id}
  Body: { "mode": "hard" | "soft", "confirm_name": "<name>" }  # confirm_name required for hard only
```

- Returns `409` if `workspace.is_system = true` (Default workspace).
- Returns `400` if `mode = "hard"` and `confirm_name` does not match `workspace.name` exactly.
- Returns `403` if caller is not a superadmin.
- On success: `204 No Content`.

### Workspace restore

```
POST /api/admin/workspaces/{workspace_id}/restore
```

- Returns `409` if workspace is not `inactive`.
- Returns `403` if caller is not a superadmin.
- On success: `200` with updated `WorkspaceAdminResponse`.

### Grace period config

```
GET  /api/admin/settings/workspace_inactive_grace_period_days
PATCH /api/admin/settings
  Body: { "workspace_inactive_grace_period_days": 14 }
```

Changing the grace period does not retroactively adjust `scheduled_purge_at` on already-inactive workspaces.

---

## 6. UI Changes

### 6a. Superadmin > Workspaces list

The workspace management table gains an **Actions** column. For each workspace:

| Workspace state | Available actions |
|-----------------|-------------------|
| `active`, not Default | **Mark inactive**, **Delete now** |
| `active`, Default | *(no delete actions — shows "System" badge)* |
| `inactive` | **Restore**, **Delete now** |

The Default workspace row shows a `System` badge in place of action buttons (same pattern as `protected` label on default catalogs).

Inactive workspaces show:
- Status badge: `inactive` (amber)
- Countdown: *"Permanent deletion: {date} at {time} {tz}"*

### 6b. Delete confirmation modal

Described in §3. Rendered as a full overlay modal (not a drawer). The workspace name input is cleared on open. The **Delete permanently** button uses a destructive red style.

### 6c. System Settings — Workspace Grace Period

In the superadmin Settings page, under a **Workspace Lifecycle** section:

```
Inactive workspace grace period
[ 30 ] days
Save
```

- Input: integer, minimum 0, maximum 365.
- Setting to `0` disables soft delete system-wide: the **Mark inactive** action is hidden and **Delete now** is the only option.
- Help text: *"How long an inactive workspace is kept before permanent deletion. Set to 0 to require immediate deletion (no grace period)."*

---

## 7. WorkspaceAdminResponse Schema

Add to the existing admin workspace response schema:

```python
class WorkspaceAdminResponse(BaseModel):
    id: UUID
    name: str
    is_system: bool
    status: Literal["active", "inactive"]
    deleted_at: datetime | None
    scheduled_purge_at: datetime | None
    member_count: int
    owned_catalog_count: int
    created_at: datetime
```

---

## 8. Cascade Behavior Summary

| Action | Owned catalogs | Members | Workspace record |
|--------|---------------|---------|-----------------|
| Hard delete | Immediately purged (data + DB) | Deleted | Deleted |
| Soft delete | Soft-deleted (same grace period) | Kept (access suspended) | Set to `inactive` |
| Restore from soft delete | Reactivated | Regain access | Set to `active` |
| Grace period expires (purge DAG) | Purged (if still inactive) | Deleted | Deleted |

---

## 9. Decisions

| Question | Decision |
|----------|----------|
| Can a superadmin delete the Default workspace? | No. Blocked at the API level regardless of role (`is_system = true`). |
| Which workspace are members redirected to after deletion? | The member's remaining workspace with the lowest `id` (earliest created). If none, the Default workspace. No notification is sent. |
| Does the grace period apply retroactively when changed? | No. `scheduled_purge_at` is fixed at soft-delete time. Changing the setting only affects future soft-deletes. |
| Are catalog grace periods aligned with the workspace grace period? | Yes — when a workspace is soft-deleted, its owned catalogs are soft-deleted using the same grace period value at that moment. They share the same `scheduled_purge_at` as the workspace. |
| What if the workspace name is reused after deletion? | After a hard delete (or after a soft-deleted workspace is purged), the name is released and can be used for a new workspace. During the inactive grace period the name remains reserved. |
| Is the DAG idempotent? | Yes — it selects `WHERE scheduled_purge_at <= now AND status = 'inactive'` and processes each in a transaction. Re-running after partial failure is safe. |
