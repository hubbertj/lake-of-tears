# Catalog Feature Spec

## Overview

Catalogs are global, workspace-owned objects that group schemas and tables into a three-level namespace: `catalog.schema.table`. A workspace can only query a catalog it has been explicitly granted access to. Any workspace admin can create a catalog. Sharing requires the owning workspace admin to approve the request.

## Core Concepts

```
Catalog  (globally unique slug, owned by one workspace)
  └── Schema  (unique within catalog)
        └── Table  (unique within schema → maps to an S3 path pattern in MinIO)
```

A reference like `production.revenue.stripe_charges` resolves to:
`read_parquet('s3://datalake/production/revenue/stripe_charges/**/*.parquet')`

## Data Model

Six new PostgreSQL tables:

```sql
catalogs
  id                  UUID PK
  name                TEXT
  slug                TEXT UNIQUE            -- globally unique, e.g. "production"
  description         TEXT
  owner_workspace_id  UUID FK workspaces
  created_by          UUID FK users
  created_at          TIMESTAMP

schemas
  id          UUID PK
  catalog_id  UUID FK catalogs CASCADE
  name        TEXT
  slug        TEXT                           -- unique within catalog
  description TEXT
  created_at  TIMESTAMP
  UNIQUE(catalog_id, slug)

catalog_tables
  id              UUID PK
  schema_id       UUID FK schemas CASCADE
  name            TEXT
  slug            TEXT                       -- unique within schema
  description     TEXT
  s3_path_pattern TEXT   -- e.g. s3://datalake/production/revenue/stripe_charges/**/*.parquet
  column_defs     JSONB  -- [{name, type, description, deprecated}]
  created_at      TIMESTAMP
  UNIQUE(schema_id, slug)

catalog_access
  id            UUID PK
  catalog_id    UUID FK catalogs CASCADE
  workspace_id  UUID FK workspaces CASCADE
  mode          ENUM('read', 'write')
  status        ENUM('pending', 'approved', 'rejected')
  requested_by  UUID FK users
  reviewed_by   UUID FK users NULL
  requested_at  TIMESTAMP
  reviewed_at   TIMESTAMP NULL
  UNIQUE(catalog_id, workspace_id)
```

The owner workspace has implicit full access — no `catalog_access` row needed. Superadmin bypasses all access checks.

## MinIO Storage Convention

```
s3://datalake/{catalog_slug}/{schema_slug}/{table_slug}/year=YYYY/month=MM/day=DD/*.parquet
```

### Backward Compatibility

Existing `raw/{source}/...` paths are seeded as a built-in catalog on first deploy:

- Catalog: `name="Raw"`, `slug="raw"`, owned by the default workspace
- Schema: `slug="sources"`
- One table entry per existing source pointing at its existing glob pattern

No Parquet files are moved. The catalog browser immediately shows existing data.

## Access Control

| Action | Owner ws admin | Shared ws admin (write) | Shared ws admin (read) | Regular user | Superadmin |
|---|---|---|---|---|---|
| Create catalog | ✅ (becomes owner) | — | — | ❌ | ✅ |
| Edit / delete catalog | ✅ | ❌ | ❌ | ❌ | ✅ |
| Create schema / table | ✅ | ✅ | ❌ | ❌ | ✅ |
| Write data (ingest) | ✅ | ✅ | ❌ | ❌ | ✅ |
| Query catalog | ✅ | ✅ | ✅ | workspace-inherited* | ✅ |
| Browse global directory | ✅ | ✅ | ✅ | ❌ | ✅ |
| Request access to catalog | ✅ | ✅ | ✅ | ❌ | ✅ |
| Approve / reject requests | ✅ (own catalog) | ❌ | ❌ | ❌ | ✅ |
| Revoke shared access | ✅ (own catalog) | ❌ | ❌ | ❌ | ✅ |

*Regular users inherit access to all catalogs their workspace has approved access to. Fine-grained table-level group permissions are a future feature.

## Backend API

### Catalogs
```
GET    /api/catalogs                                    # global directory (workspace admins)
POST   /api/catalogs                                    # create (any workspace admin)
GET    /api/catalogs/{catalog_id}                       # detail (accessible workspaces only)
PATCH  /api/catalogs/{catalog_id}                       # rename/describe (owner or superadmin)
DELETE /api/catalogs/{catalog_id}                       # owner or superadmin
```

### Schemas
```
GET    /api/catalogs/{catalog_id}/schemas
POST   /api/catalogs/{catalog_id}/schemas               # owner or write-access ws admin
PATCH  /api/catalogs/{catalog_id}/schemas/{schema_id}
DELETE /api/catalogs/{catalog_id}/schemas/{schema_id}
```

### Tables
```
GET    /api/catalogs/{catalog_id}/schemas/{schema_id}/tables
POST   /api/catalogs/{catalog_id}/schemas/{schema_id}/tables
PATCH  /api/catalogs/{catalog_id}/schemas/{schema_id}/tables/{table_id}
DELETE /api/catalogs/{catalog_id}/schemas/{schema_id}/tables/{table_id}
POST   /api/catalogs/{catalog_id}/schemas/{schema_id}/tables/{table_id}/refresh-schema
       # re-infers column_defs from Parquet metadata, merges with existing descriptions
```

### Access Management
```
GET    /api/catalogs/{catalog_id}/access                # list grants (owner or superadmin)
POST   /api/catalogs/{catalog_id}/access/request        # {mode: read|write} (workspace admin)
PATCH  /api/catalogs/{catalog_id}/access/{access_id}    # {status: approved|rejected} (owner or superadmin)
DELETE /api/catalogs/{catalog_id}/access/{access_id}    # revoke (owner or superadmin)
```

### Workspace convenience
```
GET    /api/workspaces/{workspace_id}/catalogs          # catalogs this workspace has approved access to
```

## Column Definitions — Option C (Hybrid)

### Behavior

**On table creation:**
- System fires a DuckDB query against MinIO to read Parquet schema metadata (no data rows scanned):
  ```sql
  DESCRIBE SELECT * FROM read_parquet('{s3_path_pattern}') LIMIT 0;
  ```
- If data exists, inferred columns are saved to `column_defs` JSONB.
- If no data exists yet (table registered before first ingest), `column_defs` is saved as `[]` and the catalog browser shows: *"Schema not yet available — data hasn't been ingested yet."*

**column_defs schema:**
```json
[
  { "name": "charge_id",    "type": "VARCHAR",   "description": "Stripe charge ID", "deprecated": false },
  { "name": "amount",       "type": "BIGINT",    "description": "Amount in cents",  "deprecated": false },
  { "name": "old_field_xyz","type": "VARCHAR",   "description": "Legacy field",     "deprecated": true }
]
```

**Refresh (manual):**
Workspace admins with write access or the catalog owner can click **Refresh schema** in the catalog browser. The system re-runs the DuckDB inference and merges:
- New columns in Parquet → added to `column_defs`
- Columns removed from Parquet → flagged `deprecated: true` (not deleted, so descriptions are preserved)
- Admin-written descriptions → always preserved across refreshes

**Manual entry:**
Admins can add or edit columns manually before data exists, enabling documentation of the expected schema for a table a pipeline is about to start writing.

**Nightly auto-refresh (Airflow DAG):**
A scheduled DAG (`refresh_catalog_schemas_dag.py`, runs at 06:00) iterates all registered tables, re-runs schema inference, merges results, and flags drift. Any table where new columns appeared or existing columns disappeared gets a `schema_drift: true` flag on its record. The catalog browser surfaces this as a warning badge.

## Notifications

### In-app
- Workspace admins see a badge count on the **Workspace Settings > Catalogs** tab for:
  - Inbound access requests awaiting approval (on catalogs they own)
  - Access requests they submitted that were approved or rejected
- Badge is resolved by the existing `/api/workspaces/{id}/catalogs` endpoint returning `pending_approvals_count` and `pending_requests_count` fields.

### Email
- **On access request submitted:** email to all workspace admins of the owning workspace — subject: `[Lake of Tears] Catalog access request: {workspace_name} → {catalog_name}`
- **On access request approved/rejected:** email to the requesting workspace admin — subject: `[Lake of Tears] Catalog access {approved|rejected}: {catalog_name}`
- Email is sent via a background task after the DB write. SMTP config added to `.env` (`SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`).

## UI Changes

### 1. Workspace Settings — Catalogs tab
Visible to workspace admins only. Three sections:

**Owned catalogs** — catalogs this workspace created. Each shows schema/table count, access request badge (if pending approvals exist), and a Manage button (edit name/description, manage schemas/tables, review access requests).

**Shared catalogs** — catalogs from other workspaces with approved access. Shows access level badge (Read / Write) and a Remove button to revoke the workspace's own access.

**Browse global directory** button — opens the global catalog browser modal.

### 2. Global Catalog Directory (modal)
Browseable table of all catalogs in the system. Columns: Name, Owner workspace, Description, Schemas, Tables, Your access status.

Each row has a **Request Access** button (disabled if already approved or pending). Clicking opens a small form: choose `read` or `write`, optional message. Submitting creates a `pending` `catalog_access` record and sends an email to the owning workspace admins.

### 3. Inbound Access Requests (Workspace Settings > Catalogs > Owned catalog > Manage)
Lists pending requests: requesting workspace name, requested mode, date requested, optional message. Approve / Reject buttons. Approving sends an email confirmation to the requesting workspace admin.

### 4. Catalog Browser (replaces `/catalog` page)
Three-level collapsible tree showing only catalogs the active workspace has access to:

```
📂 production          [owned]           ← catalog
  📂 revenue                             ← schema
    📄 stripe_charges                    ← table
    📄 shopify_orders
  📂 crm
    📄 hubspot_deals
📂 raw                 [read]            ← seeded legacy catalog
  📂 sources
    📄 truenas
    📄 jellyfin
```

Clicking a table shows:
- Description
- Column definitions (name, type, description; deprecated columns shown in muted style)
- Schema drift warning if flagged
- Last modified (from MinIO object metadata)
- S3 path pattern
- **Query in SQL Editor** button → pre-fills `SELECT * FROM read_parquet('{path}') LIMIT 100`
- **Refresh schema** button (write-access or owner only)

### 5. SQL Editor — Catalog-aware autocomplete
When the user types `catalog_slug.schema_slug.` the editor fetches accessible tables for that schema and suggests completions. Selecting one inserts the resolved `read_parquet(s3_path_pattern)` expression.

## Pipeline / StorageWriter Changes

`StorageWriter` gains catalog/schema/table parameters:

```python
# Current
StorageWriter(source="stripe_charges").write(df)
# → s3://datalake/raw/stripe_charges/year=.../

# New
StorageWriter(catalog="production", schema="revenue", table="stripe_charges").write(df)
# → s3://datalake/production/revenue/stripe_charges/year=.../
```

Write path is derived from slugs. The writer validates the calling workspace has write access to the catalog before writing. Existing `source=` parameter continues to work via the `raw.sources` catalog for backward compatibility.

## New Airflow DAG

`refresh_catalog_schemas_dag.py` — scheduled daily at 06:00:
- Iterates all `catalog_tables` records
- Re-runs DuckDB schema inference for each
- Merges result into `column_defs` (add new, deprecate removed, preserve descriptions)
- Sets `schema_drift = true` on any table where the column set changed
- Logs a summary of changes

## Medallion Schema Tiers

Every new catalog is created with three locked schemas — **bronze**, **silver**, and **gold** — representing the standard lakehouse medallion architecture. These schemas cannot be deleted or renamed; they are permanent fixtures of every catalog.

| Tier | Slug | Purpose |
|---|---|---|
| 🟤 Bronze | `bronze` | Raw ingested data — unmodified, as-landed from the source |
| ⚪ Silver | `silver` | Cleaned and validated data — deduplicated, typed, nulls handled |
| 🟡 Gold | `gold` | Aggregated and business-ready data — metrics, summaries, joined views |

Additional schemas beyond these three can be created and managed freely by workspace admins with write access.

### Data Model Change

The `schemas` table gains a `tier` column:

```sql
schemas
  ...
  tier  ENUM('bronze', 'silver', 'gold', 'custom') DEFAULT 'custom'
  ...
```

When a catalog is created, the backend inserts three rows with `tier = 'bronze'`, `tier = 'silver'`, and `tier = 'gold'`. Delete and rename operations on these rows are rejected by the API (HTTP 400).

---

## Catalog Browser UI

### Layout: Expandable List

The catalog browser (`/catalog`) renders as a vertical list of expandable sections — not a file tree. Each level is a discrete accordion row that expands inline.

```
┌─────────────────────────────────────────────────┐
│ ▼  production                        [owned]    │
│                                                  │
│    🟤 ▼  bronze                                 │
│         📄 raw_stripe_charges                   │
│         📄 raw_weather_hourly                   │
│                                                  │
│    ⚪ ▶  silver                                 │
│    🟡 ▶  gold                                   │
│    📂 ▶  analytics            (custom schema)   │
│                                                  │
│ ▶  raw                               [read]     │
└─────────────────────────────────────────────────┘
```

- **Catalog row**: full-width, bold name, access badge (`[owned]` / `[read]` / `[write]`), chevron toggle.
- **Schema row**: indented, nugget icon for tier (see below), schema name, chevron toggle. Custom schemas use a plain folder icon `📂`.
- **Table row**: indented further, document icon `📄`, table name. Clicking opens the table detail panel.

All three levels are collapsed by default. Browser remembers expanded state per session (localStorage).

### Medallion Nugget Icons

Bronze, silver, and gold schemas each have a distinct visual treatment applied only to the schema row itself (not propagated to table rows). The icons are the actual ore-rock images committed to the repo:

| Tier | Image asset | Label color | Row accent |
|---|---|---|---|
| Bronze | `ui/static/img/medallion/bronze-ore.jpg` | `#cd7f32` | Left border `2px solid #cd7f32` |
| Silver | `ui/static/img/medallion/silver-ore.jpg` | `#a8a9ad` | Left border `2px solid #a8a9ad` |
| Gold   | `ui/static/img/medallion/gold-ore.jpg`   | `#b8860b` | Left border `2px solid #ffd700` |

Each image is displayed at **20×20 px** (CSS `object-fit: contain`) next to the schema name. Custom schemas use a plain folder icon with no border accent.

## Implementation Order

1. PostgreSQL models + Alembic migrations (include `tier` column on `schemas`)
2. Backend API (catalogs → schemas → tables → access management; enforce locked medallion schemas)
3. Email notification service (SMTP)
4. Workspace Settings — Catalogs tab + global directory modal
5. Catalog Browser page (replaces `/catalog`) — expandable list layout with medallion nugget icons
6. Nightly refresh DAG
7. StorageWriter refactor
8. SQL Editor autocomplete
9. Seed migration (existing raw/* paths → raw.sources catalog; seed bronze/silver/gold on raw catalog)
