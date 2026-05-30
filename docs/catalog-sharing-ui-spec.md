# Catalog Sharing UI Spec

## Overview

The backend for catalog sharing is fully implemented — access request, approval, rejection, revocation, and email notification endpoints all exist. This spec covers the missing UI layer: the flow for requesting access to a catalog from another workspace, and the flow for a catalog owner to review and act on inbound requests.

---

## What Is Already Built

| Layer | Status |
|-------|--------|
| `POST /api/catalogs/{id}/access/request` | ✅ Done |
| `PATCH /api/catalogs/{id}/access/{access_id}` (approve/reject) | ✅ Done |
| `DELETE /api/catalogs/{id}/access/{access_id}` (revoke) | ✅ Done |
| `GET /api/catalogs/{id}/access` (list grants for a catalog) | ✅ Done |
| `DELETE /api/workspaces/{id}/catalogs/{catalog_id}/shared` (requester removes) | ✅ Done |
| Email: access requested / approved / rejected / removed | ✅ Done |
| Workspace settings — Shared tab (lists approved + pending catalogs) | ✅ Done |
| Remove button on shared catalog | ✅ Done |

---

## What This Spec Adds

1. **Global catalog directory** — browse all catalogs system-wide and request access
2. **Inbound access requests panel** — catalog owners review and approve/reject requests inline in workspace settings
3. **New backend endpoint** — `GET /api/catalogs/directory` (global directory with per-workspace access status)

---

## 1. New Backend Endpoint — `GET /api/catalogs/directory`

The existing `GET /api/catalogs` only returns catalogs the caller already has access to. The directory needs all active catalogs system-wide, annotated with the current workspace's relationship to each.

### Request

```
GET /api/catalogs/directory?workspace_id={workspace_id}
Authorization: Bearer <token>
```

Requires the caller to be a workspace admin in the given workspace (or superadmin).

### Response schema

```python
class CatalogDirectoryItem(BaseModel):
    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    owner_workspace_id: uuid.UUID
    owner_workspace_name: str | None
    schema_count: int
    # 'owned' | 'approved' | 'pending' | 'none'
    access_status: str
    access_id: uuid.UUID | None  # populated when status is pending or approved
```

### Filtering

- Excludes soft-deleted catalogs (`deleted_at IS NOT NULL`)
- Excludes catalogs owned by the requesting workspace (they already own them)
- Returns all remaining active catalogs, regardless of access status

---

## 2. Workspace Settings — "Add catalog" Flow

### 2a. Add button

An **Add catalog** button is added to the Catalogs section header in workspace settings, visible to workspace admins. It sits to the right of the "Catalogs" heading, adjacent to the existing tab bar.

```
Catalogs                                          [ + Add catalog ]
─────────────────────────────────────────────────────────────────
Owned (2)   Shared (1)
```

### 2b. Global catalog directory modal

Clicking **Add catalog** opens a modal:

```
┌─────────────────────────────────────────────────────────────────┐
│  Browse catalogs                                           [✕]  │
│  ─────────────────────────────────────────────────────────────  │
│  [ 🔍 Search by name or workspace...                         ]  │
│                                                                 │
│  production · Default        3 schemas  · analytics platform   │
│  ──────────────────────────────────────────────── [Request]     │
│                                                                 │
│  raw · Default               1 schema   · legacy ingestion     │
│  ──────────────────────────────────────────────── [Pending…]    │
│                                                                 │
│  events · Analytics          2 schemas  ·                      │
│  ──────────────────────────────────────────────── [✓ Added]     │
└─────────────────────────────────────────────────────────────────┘
```

**Columns:** `<catalog_name> · <owner_workspace_name>`, schema count, description (truncated), action button.

**Action button states:**

| `access_status` | Button | Behaviour |
|---|---|---|
| `none` | **Request** | Submits access request |
| `pending` | **Pending…** | Disabled, tooltip: "Request is awaiting approval" |
| `approved` | **✓ Added** | Disabled |
| `owned` | Hidden | Row not shown (filtered server-side) |

**Search** filters the list client-side by catalog name or owner workspace name.

**On Request click:**
- Button immediately switches to **Pending…** (optimistic update)
- UI calls `POST /api/catalogs/{id}/access/request` with `{"mode": "read", "workspace_id": "<active_ws_id>"}`
- On success: button stays **Pending…**
- On error: button reverts to **Request**, shows inline error text below the row

Access mode is always `read`. There is no mode selector.

---

## 3. Workspace Settings — Inbound Access Requests

A new **Inbound requests** section is inserted in workspace settings, between the Catalogs card and the Members card. It is only visible when there is at least one pending inbound request across any owned catalog.

```
Inbound access requests (2)
──────────────────────────────────────────────────────────────────
Analytics workspace  →  production       read  · 3 days ago
                                         [ Approve ]  [ Reject ]

Reporting workspace  →  raw              read  · 1 day ago
                                         [ Approve ]  [ Reject ]
```

**Columns:** requesting workspace name, arrow, owned catalog name, requested mode, time ago, action buttons.

**Approve:**
- Calls `PATCH /api/catalogs/{catalog_id}/access/{access_id}` with `{"status": "approved"}`
- Row disappears immediately on success
- Email is sent to the requesting workspace admin (handled by existing backend logic)

**Reject:**
- Same endpoint, `{"status": "rejected"}`
- Row disappears immediately on success
- Email is sent to the requesting workspace admin

**Section disappears** entirely when there are no pending requests — it is not shown as an empty state.

### Backend: pending requests list endpoint

A new endpoint is needed to fetch all pending inbound requests across all catalogs owned by a workspace:

```
GET /api/workspaces/{workspace_id}/access/requests/inbound
```

Returns:

```python
class InboundAccessRequest(BaseModel):
    access_id: uuid.UUID
    catalog_id: uuid.UUID
    catalog_name: str
    requesting_workspace_id: uuid.UUID
    requesting_workspace_name: str | None
    mode: str
    requested_at: datetime
```

Requires the caller to be a workspace admin in the given workspace (or superadmin). Only returns `status = 'pending'` grants.

---

## 4. Shared Tab — Pending Request Display

The existing Shared tab already shows catalogs with `status: pending`. The display should make the pending state clearer:

- Catalog name row shows a `pending approval` badge (yellow, same style as `pending deletion`)
- No **Remove** button while still pending — only after approval
- Once approved the badge is replaced by the `read` access badge and the Remove button appears

Current behaviour:

```
production.events   read    [Remove]    ← approved, correct
raw.sources         pending [Remove]    ← pending, Remove shouldn't show yet
```

Desired behaviour:

```
production.events   read    [Remove]    ← approved
raw.sources         pending approval    ← pending, no Remove button
```

---

## 5. API Changes Summary

| Method | Path | Status | Notes |
|--------|------|--------|-------|
| `GET /api/catalogs/directory` | new | All active catalogs with per-workspace access status |
| `GET /api/workspaces/{id}/access/requests/inbound` | new | Pending inbound access requests for owned catalogs |
| `POST /api/catalogs/{id}/access/request` | existing | No change needed |
| `PATCH /api/catalogs/{id}/access/{access_id}` | existing | No change needed |

---

## 6. UI Route Changes (ui/main.py)

| Route | Change |
|-------|--------|
| `GET /settings/workspace` | Load inbound requests from new endpoint alongside existing catalog settings |
| `POST /settings/workspace/catalogs/{id}/access/request` | New: proxy to backend request endpoint |
| `POST /settings/workspace/catalogs/{catalog_id}/access/{access_id}/approve` | New: proxy to backend review endpoint |
| `POST /settings/workspace/catalogs/{catalog_id}/access/{access_id}/reject` | New: proxy to backend review endpoint |

The global catalog directory is loaded client-side via a fetch call from the modal JavaScript (calls `GET /api/catalogs/directory?workspace_id=...` directly with the auth cookie), keeping the workspace settings page load fast.

---

## 7. Decisions

| Question | Decision |
|---|---|
| Global directory scope | All active catalogs system-wide — any workspace admin can see and request any catalog |
| Where owners review requests | Inline in workspace settings — no separate manage page |
| Access mode from UI | Read-only only. The `mode` field is always `read` when submitting from the UI |
| Can requesting workspace cancel a pending request? | Yes — the existing Remove button on a pending shared catalog entry handles this |
| What happens to pending requests on shared tab? | Remove button is hidden for pending entries; only the `pending approval` badge is shown |
