# Default Catalog Protection Spec

## Overview

The `default` catalog is automatically created when a workspace is created and is permanently coupled to it. It cannot be soft-deleted or hard-purged by anyone — workspace admins or superadmins. This spec defines the data model change, API enforcement, and UI change needed to protect it.

---

## 1. Motivation

The `default` catalog is structural to every workspace. Allowing it to be deleted would leave a workspace in a broken state with no baseline catalog. Because it is created automatically and tied to the workspace lifecycle, deletion must be prevented at every layer.

---

## 2. Data Model Change

### `catalogs` table — new column

| Column | Type | Default | Notes |
|--------|------|---------|-------|
| `is_default` | `BOOLEAN` | `false` | Set to `true` only on the auto-created default catalog; never changed after creation |

### Migration

```sql
ALTER TABLE catalogs ADD COLUMN IF NOT EXISTS is_default BOOLEAN NOT NULL DEFAULT FALSE;
```

### Backfill

After adding the column, mark the existing default catalogs:

```sql
UPDATE catalogs
SET is_default = TRUE
WHERE LOWER(name) = 'default'
  AND id IN (
    SELECT DISTINCT ON (owner_workspace_id) id
    FROM catalogs
    WHERE LOWER(name) = 'default'
    ORDER BY owner_workspace_id, created_at ASC
  );
```

This marks the oldest catalog named `default` per workspace as the protected one. If a workspace has more than one catalog named `default` (shouldn't happen after the uniqueness constraint landed), only the earliest is marked.

---

## 3. Backend Changes

### Catalog creation

In `_create_default_catalog()` and the backfill function `_backfill_default_catalogs()`, set `is_default=True` on the auto-created catalog record.

### Soft-delete enforcement — `DELETE /api/catalogs/{id}`

Add a guard at the top of `delete_catalog()`:

```python
if catalog.is_default:
    raise HTTPException(409, "The default catalog cannot be deleted")
```

### Hard-purge enforcement — `POST /api/catalogs/{id}/purge`

Add the same guard at the top of `purge_catalog()`:

```python
if catalog.is_default:
    raise HTTPException(409, "The default catalog cannot be deleted")
```

The guard applies regardless of the caller's role — even superadmins cannot delete the default catalog via the API.

---

## 4. UI Changes

### Workspace Settings — Owned Catalogs list

In `ui/templates/settings/workspace.html`, the Delete and Delete now buttons are conditionally hidden when the catalog is the default.

Wrap both action buttons in a check:

```jinja2
{% if not cat.is_default %}
  {# Delete / Reactivate / Delete now buttons #}
{% else %}
  <span class="text-[11px] text-gray-600 shrink-0 italic">protected</span>
{% endif %}
```

The `is_default` field is already returned by `CatalogResponse` once the backend model is updated.

### No change to the catalog explorer

The default catalog appears and behaves normally in the catalog explorer — it is only the deletion controls that are removed.

---

## 5. Schema Change — `CatalogResponse`

Add `is_default: bool = False` to `CatalogResponse` in `backend/schemas.py` so the UI can read it from the settings endpoint.

---

## 6. Decisions

| Question | Decision |
|----------|----------|
| Can a superadmin delete the default catalog? | No. The protection is absolute — enforced at the API level regardless of role. |
| Can the default catalog be renamed? | Out of scope for this spec. The `is_default` flag is decoupled from the name, so a rename would not affect protection. |
| What if a workspace has no `is_default` catalog after migration? | The backfill at startup (`_backfill_default_catalogs`) creates one if missing. On first startup after this deploy, any workspace without a default catalog gets one created with `is_default = True`. |
| Is the "protected" label shown to non-admins? | Non-admins never see the delete buttons regardless, so no change is needed for them. |
| What HTTP status is returned when deletion is blocked? | `409 Conflict` — the request is valid but the resource state prevents it. |
