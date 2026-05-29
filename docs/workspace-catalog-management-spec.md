# Workspace Catalog Management Spec

## Overview

Workspace admins gain catalog management capabilities directly inside workspace settings:

1. **Delete** a catalog owned by the workspace (soft-delete with configurable grace period, or immediate hard-delete)
2. **Add** a catalog from another workspace (read-only, via existing request + approval flow)
3. **Remove** a shared catalog that was added from another workspace

Superadmins can configure the soft-delete grace period duration (or disable it entirely) from the admin panel. See §11.

---

## 1. Catalog Display in the Catalog Explorer

### Owned catalogs
Display as `catalog_name` (unchanged).

### Shared catalogs (owned by another workspace)
Display as `<owning_workspace_name>.<catalog_name>` — e.g. `production.events`.

This applies everywhere the catalog name appears: the catalog explorer tree, the table detail panel title, and SQL editor breadcrumbs.

---

## 2. Workspace Settings — Catalog Management UI

A new **Catalogs** section in workspace settings lists all catalogs associated with the workspace in two groups:

### 2a. Owned catalogs

| Column | Notes |
|--------|-------|
| Name | catalog name |
| Status | `active` / `pending deletion` + countdown (e.g. "Deletes in 23 days · Jun 28 2026 14:32 EDT") |
| Schemas | count |
| Actions | See below |

**Actions for active owned catalogs:**
- **Delete** — triggers soft-delete (see §3)

**Actions for soft-deleted owned catalogs:**
- **Reactivate** — cancels soft-delete, restores catalog to active
- **Delete now** — triggers immediate hard-delete (see §4)

### 2b. Shared catalogs (added from other workspaces)

| Column | Notes |
|--------|-------|
| Name | displayed as `<workspace>.<catalog_name>` |
| Owner workspace | |
| Access | always `read` |
| Status | `approved` / `pending` |
| Actions | **Remove** — revokes the access grant |

---

## 3. Soft Delete (Configurable Grace Period)

Triggered when a workspace admin clicks **Delete** on an owned catalog, **and** the soft-delete grace period is enabled (grace period days > 0). See §11 for how superadmins configure this.

If soft delete is **disabled** (grace period = 0), clicking **Delete** skips directly to the hard-delete flow (§4).

### Behavior on trigger
- `deleted_at` is set to now on the catalog record.
- `scheduled_purge_at` is set to `deleted_at + <grace_period_days>`.
- The catalog is **immediately removed** from the catalog explorer in every workspace (owner and all workspaces with shared access). It is not visible or queryable.
- All `CatalogAccess` grants for other workspaces are suspended (access suspended, not deleted — they are restored if the soft-delete is cancelled).
- No confirmation modal is shown for soft-delete — just a standard "Are you sure?" inline confirmation button.

### During the grace period
- The catalog appears only in the owning workspace's settings under **Owned catalogs** with status `pending deletion` and a date/time showing when the scheduled purge will run (e.g. `Deletes Jun 28 2026 at 2:32 PM EDT`).
- **Name reservation**: the catalog name remains reserved for its workspace. No new catalog with the same name (case-insensitive) may be created in that workspace while the soft-deleted catalog exists.

### Reactivation
A workspace admin can click **Reactivate** at any time during the grace period:
- Clears `deleted_at` and `scheduled_purge_at`.
- Restores all suspended `CatalogAccess` grants.
- Catalog reappears in the catalog explorer across all workspaces that had access.

### Changing the grace period while catalogs are pending deletion
If a superadmin changes the grace period duration (e.g. from 30 days to 14 days), **already-pending catalogs keep their original `scheduled_purge_at`** and are unaffected. The new duration applies only to catalogs soft-deleted after the change.

---

## 4. Hard Delete ("Delete Now")

Available in two scenarios:
- **Workspace admin**: only when the catalog is already soft-deleted (in the grace period window), via the **Delete now** button.
- **Superadmin**: on any active catalog directly, bypassing soft-delete entirely — the **Delete now** button is visible on active catalogs in the admin panel.
- **Soft delete disabled**: when the grace period is set to 0, clicking **Delete** on any catalog goes directly to this flow.

Clicking **Delete now** opens a confirmation modal:

### Confirmation modal
```
Delete catalog permanently

This action is permanent and cannot be reversed.

All schemas, tables, and data stored under this catalog
will be immediately and permanently deleted.

Type the catalog name to confirm:
[ _________________ ]

[ Cancel ]   [ Delete permanently ]
```

- The **Delete permanently** button is disabled until the typed value exactly matches the catalog name (case-sensitive).
- On confirm:
  - All `CatalogSchema`, `CatalogTable`, and `CatalogAccess` records are deleted immediately via DB cascade.
  - All S3/MinIO data under the catalog's path is deleted immediately.
  - The `Catalog` record is hard-deleted from the database.
  - No Airflow job is queued; deletion is synchronous in the API response.

---

## 5. Adding a Shared Catalog

Uses the existing request + approval flow (`CatalogAccess` with `status = pending/approved`):

1. Workspace admin clicks **Add catalog** in workspace settings.
2. A search/browse modal shows catalogs from other workspaces the user can request access to (excludes catalogs owned by this workspace, catalogs already added, and soft-deleted catalogs).
3. Admin selects a catalog and submits a read-access request.
4. The owning workspace's admin receives a notification and approves or denies.
5. On approval, the catalog appears in this workspace's catalog explorer as `<owning_workspace>.<catalog_name>`.

---

## 6. Removing a Shared Catalog

Workspace admin clicks **Remove** on a shared catalog in workspace settings:
- Deletes the `CatalogAccess` grant immediately.
- The catalog disappears from this workspace's catalog explorer.
- The owning workspace and any other workspaces with access are unaffected.
- An email notification is sent to all admins of the owning workspace informing them that access was removed.
- No grace period — removal of a shared (read-only) catalog is immediate and does not require confirmation beyond a standard inline confirmation.

---

## 7. Airflow DAG — `purge_deleted_catalogs_dag`

### Schedule
Daily at `02:00` (local server time).

### Logic
```
For each Catalog where scheduled_purge_at <= now AND purged_at IS NULL:
  1. Delete all S3/MinIO objects under the catalog's storage paths
  2. Delete CatalogSchema, CatalogTable, CatalogAccess records (or rely on DB CASCADE)
  3. Hard-delete the Catalog record from the database
```

### Idempotency
The DAG queries by `scheduled_purge_at <= now`, so replaying a failed run is safe — already-purged records are gone and won't be matched.

### Failure handling
If S3 deletion fails, the DAG marks the task failed and retries (3 retries, 5-minute delay). The DB record is not deleted until S3 deletion succeeds, preventing orphaned data.

---

## 8. Data Model Changes

### `catalogs` table — new columns

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `deleted_at` | `TIMESTAMPTZ` | `NULL` | Set when soft-delete is triggered |
| `scheduled_purge_at` | `TIMESTAMPTZ` | `NULL` | `deleted_at + 30 days` |

### `catalog_access` table — new column

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `suspended` | `BOOLEAN` | `false` | Set to `true` when owning catalog is soft-deleted; restored on reactivation |

### `system_settings` table — new table

Stores singleton key/value configuration for superadmin-controlled settings.

| Column | Type | Notes |
|--------|------|-------|
| `key` | `VARCHAR` PK | Setting identifier |
| `value` | `JSONB` | Setting value |
| `updated_at` | `TIMESTAMPTZ` | Last changed |
| `updated_by` | `UUID FK → users` | Superadmin who last changed it |

Initial seed row:

| key | value |
|-----|-------|
| `catalog_soft_delete_days` | `30` |

A value of `0` means soft delete is disabled — deletions go straight to the hard-delete flow.

### Uniqueness constraint
`POST /api/catalogs` must check for name conflicts against **all** catalogs in the workspace, including soft-deleted ones (`deleted_at IS NOT NULL`).

---

## 9. API Changes

| Method | Path | Change |
|--------|------|--------|
| `DELETE /api/catalogs/{id}` | existing | Now performs soft-delete instead of hard-delete; suspends access grants; returns 200 with updated catalog |
| `POST /api/catalogs/{id}/reactivate` | new | Clears `deleted_at` / `scheduled_purge_at`; restores suspended access grants |
| `DELETE /api/catalogs/{id}/purge` | new | Hard-delete now; workspace admins require catalog to be soft-deleted first; superadmins may call this on any active catalog directly. Typed-name confirmation required in request body. |
| `GET /api/workspaces/{id}/catalogs` | existing | Exclude catalogs where `deleted_at IS NOT NULL` |
| `GET /api/workspaces/{id}/settings/catalogs` | new | Returns owned catalogs (including soft-deleted) + shared catalogs for the settings page |
| `GET /api/admin/settings` | new | Returns all system settings (superadmin only) |
| `PATCH /api/admin/settings` | new | Updates one or more system settings (superadmin only) |

---

## 11. Admin Panel — Soft Delete Configuration

A new **Catalog Settings** section in the superadmin admin panel.

### UI

```
Catalog Settings

  Soft Delete Grace Period
  ─────────────────────────────────────────────────────────
  When a workspace admin deletes a catalog, it enters a
  grace period before permanent deletion. Set to 0 to
  disable soft delete entirely.

  Grace period (days):  [ 30 ]   (0 = disabled)

  [ Save ]
```

- Input is a non-negative integer. Min: `0`, max: `365`.
- A value of `0` shows a warning inline: `"Soft delete is disabled — catalog deletions will be permanent immediately."`
- On save, the new value is written to `system_settings` (`catalog_soft_delete_days`).
- A confirmation is shown if the value is being reduced (e.g. from 30 to 7): `"Reducing the grace period will not affect catalogs already in the pending-deletion window."` This is informational only and does not block saving.

### Behavior by setting value

| `catalog_soft_delete_days` | Workspace admin deletes a catalog | Superadmin deletes a catalog |
|---|---|---|
| `> 0` (e.g. 30) | Soft-delete → grace period → Airflow purge | **Delete now** skips straight to hard-delete (type-to-confirm modal) |
| `0` (disabled) | Skips straight to hard-delete (type-to-confirm modal) | Same — hard-delete directly |

### Effect on in-flight pending deletions
Changing this setting does **not** affect catalogs already in the pending-deletion state. Their original `scheduled_purge_at` is preserved and the Airflow DAG will purge them as scheduled.

---

## 10. Decisions

| Question | Decision |
|----------|----------|
| Can superadmins hard-delete a catalog without the soft-delete step? | **Yes.** Superadmins see a **Delete now** action on any active catalog (not just soft-deleted ones), bypassing the grace period entirely. The same type-to-confirm modal applies. |
| Should the owning workspace admin be notified when a shared workspace removes access? | **Yes.** An email notification is sent to the owning workspace's admins when any workspace removes a shared catalog. Same email infrastructure as the existing access-request notifications. |
| Is there a cap on how many catalogs a workspace can have in the pending-deletion state? | **No limit.** A workspace may have any number of catalogs in the grace period window simultaneously. |
| Is the grace period duration configurable? | **Yes.** Superadmins set it in the admin panel (0–365 days). Default is 30. Setting to 0 disables soft delete entirely — deletions go straight to the hard-delete flow for all users. |
| What happens to in-flight pending deletions when the grace period is changed? | **They keep their original `scheduled_purge_at`.** The new duration applies only to catalogs soft-deleted after the change. |
| Where is the setting persisted? | **Database** (`system_settings` table). Survives restarts; changeable at runtime without a redeploy. |
