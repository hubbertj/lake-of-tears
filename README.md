# Lake of Tears 💧

**A self-hosted datalakehouse with AI-powered search, DuckDB analytics, and a custom web UI**

![License: MIT](https://img.shields.io/badge/License-MIT-blue.svg)
![Docker Compose: ready](https://img.shields.io/badge/Docker%20Compose-ready-2496ED?logo=docker&logoColor=white)
![Helm: ready](https://img.shields.io/badge/Helm-ready-0F1689?logo=helm&logoColor=white)
![Python: 3.12](https://img.shields.io/badge/Python-3.12-3776AB?logo=python&logoColor=white)

> Screenshot: Lake UI dashboard

---

## Features

- S3-compatible object storage via MinIO CE
- Parquet-native storage partitioned by `year=/month=/day=`
- DuckDB query engine — run SQL directly over S3, no server required
- 768-dim vector embeddings via Gemini + DuckDB VSS extension for semantic search
- RAG queries: ask questions in plain English, get AI answers grounded in your data
- Anomaly detection via scikit-learn Isolation Forest + Gemini explanations
- Built-in pipeline orchestration with Apache Airflow
- Custom web UI: dashboard, data browser, SQL playground, AI query interface
- Deploy anywhere: Docker Compose (single node) or Kubernetes (Helm)

---

## Architecture

```
┌──────────────┐    ┌──────────────────────────────────────┐
│  Data Sources│    │           Lake of Tears               │
│              │    │                                        │
│  Stripe      ├───►│  Airflow  ──►  MinIO CE (S3)          │
│  Shopify     ├───►│  Pipeline     s3://datalake/raw/       │
│  HubSpot     ├───►│               s3://datalake/embeddings/│
│  PostgreSQL  ├───►│                     │                  │
└──────────────┘    │               DuckDB │ VSS             │
                    │                     ▼                  │
                    │  Gemini API ──► Embeddings             │
                    │                     │                  │
                    │              ┌──────▼──────┐           │
                    │              │   Lake UI   │           │
                    │              │  Dashboard  │           │
                    │              │  SQL Query  │           │
                    │              │  AI / RAG   │           │
                    │              └─────────────┘           │
                    │  Jupyter · Superset · Airflow UI        │
                    └──────────────────────────────────────┘
```

---

## Quick Start (Docker Compose)

```bash
git clone https://github.com/hubbertj/lake-of-tears
cd lake-of-tears
cp .env.example .env
# Edit .env with your credentials
docker compose up -d
```

Once running, open:

| Service | URL |
|---------|-----|
| Lake UI | http://localhost:3000 |
| MinIO console | http://localhost:9001 |
| JupyterLab | http://localhost:8888 |
| Apache Superset | http://localhost:8088 |
| Apache Airflow | http://localhost:8080 |

---

## Quick Start (Kubernetes / Helm)

```bash
helm install lake-of-tears ./deploy/helm/lake-of-tears \
  --set minio.rootPassword=your-password \
  --set gemini.apiKey=your-key \
  --set jupyter.token=your-token \
  --set superset.adminPassword=your-password \
  --set airflow.adminPassword=your-password
```

See `deploy/helm/lake-of-tears/values.yaml` for the full list of configurable values.

---

## Configuration

All configuration is driven by environment variables. Copy `.env.example` to `.env` and fill in the required values before starting the stack.

| Variable | Required | Default | Description |
|---|---|---|---|
| `MINIO_ROOT_USER` | yes | — | MinIO root username (5–20 chars) |
| `MINIO_ROOT_PASSWORD` | yes | — | MinIO root password (8–40 chars) |
| `MINIO_ENDPOINT` | no | `localhost:9000` | MinIO S3 API endpoint |
| `GEMINI_API_KEY` | yes | — | Google AI Studio API key |
| `JUPYTER_TOKEN` | yes | — | JupyterLab access token |
| `SUPERSET_ADMIN_PASSWORD` | yes | — | Superset admin password |
| `SUPERSET_SECRET_KEY` | no | auto | Superset Flask secret key |
| `AIRFLOW_PASSWORD` | yes | — | Airflow admin password |
| `AIRFLOW_SECRET_KEY` | no | auto | Airflow Flask secret key |
| `STRIPE_SECRET_KEY` | no | — | Stripe secret key for payments ingestion |
| `SHOPIFY_STORE_DOMAIN` | no | — | Shopify store domain (e.g. `mystore.myshopify.com`) |
| `SHOPIFY_ACCESS_TOKEN` | no | — | Shopify Admin API access token |
| `HUBSPOT_ACCESS_TOKEN` | no | — | HubSpot private app access token |
| `POSTGRES_DSN` | no | — | PostgreSQL connection string for app DB ingestion |
| `DATALAKE_DATA_DIR` | yes | `~` | Host path for Docker volume mounts |

---

## Data Sources

Lake of Tears is built to ingest operational business data from the APIs your company already uses. Every pipeline writes Parquet files partitioned by `year=/month=/day=` to `s3://datalake/raw/<source>/`, queryable immediately with DuckDB.

| Source | Script | Schedule | Output path | Requires |
|--------|--------|----------|-------------|----------|
| **Stripe** | `pipeline/ingest/stripe.py` | Hourly | `raw/stripe/` | `STRIPE_SECRET_KEY` |
| **Shopify** | `pipeline/ingest/shopify.py` | Every 6 h | `raw/shopify/` | `SHOPIFY_STORE_DOMAIN`, `SHOPIFY_ACCESS_TOKEN` |
| **HubSpot** | `pipeline/ingest/hubspot.py` | Daily 01:00 | `raw/hubspot/` | `HUBSPOT_ACCESS_TOKEN` |
| **PostgreSQL** | `pipeline/ingest/postgres.py` | Hourly | `raw/postgres/` | `POSTGRES_DSN` |
| **Open-Meteo** | `pipeline/ingest/weather.py` | Daily 06:00 | `raw/weather/` | none (free API) |

---

### Stripe — Payments & Revenue

The Stripe pipeline pulls charges, refunds, and subscription events from the Stripe API and lands them as Parquet for financial analysis and cohort reporting without touching your production database.

**What it collects (hourly):**

`raw/stripe_charges/` — one row per charge event:

| Column | Type | Example |
|--------|------|---------|
| `charge_id` | string | `"ch_3Pq..."` |
| `amount_usd` | float | `99.00` |
| `currency` | string | `"usd"` |
| `status` | string | `"succeeded"` |
| `customer_id` | string | `"cus_Qa..."` |
| `customer_email` | string | `"alice@example.com"` |
| `description` | string | `"Pro plan – monthly"` |
| `refunded` | bool | `false` |
| `dispute` | bool | `false` |
| `created_at` | timestamp | `2025-05-27 14:32:00` |

`raw/stripe_subscriptions/` — current subscription state snapshot:

| Column | Type | Example |
|--------|------|---------|
| `subscription_id` | string | `"sub_1P..."` |
| `customer_id` | string | `"cus_Qa..."` |
| `plan_name` | string | `"Pro"` |
| `interval` | string | `"month"` |
| `amount_usd` | float | `99.00` |
| `status` | string | `"active"` |
| `current_period_start` | timestamp | `2025-05-01 00:00:00` |
| `current_period_end` | timestamp | `2025-06-01 00:00:00` |
| `canceled_at` | timestamp | null |
| `created_at` | timestamp | `2024-11-15 09:12:00` |

**Example queries:**

```sql
-- MRR by plan this month
SELECT
    plan_name,
    count(*) AS active_subscribers,
    round(sum(amount_usd), 2) AS mrr_usd
FROM read_parquet('s3://datalake/raw/stripe_subscriptions/**/*.parquet')
WHERE status = 'active'
GROUP BY 1 ORDER BY mrr_usd DESC;
```

```sql
-- Daily revenue and refund rate over the last 30 days
SELECT
    date_trunc('day', created_at) AS day,
    round(sum(amount_usd) FILTER (WHERE status = 'succeeded'), 2) AS gross_revenue,
    round(sum(amount_usd) FILTER (WHERE refunded = true), 2) AS refunds,
    count(*) FILTER (WHERE dispute = true) AS disputes
FROM read_parquet('s3://datalake/raw/stripe_charges/**/*.parquet')
WHERE created_at >= current_date - INTERVAL 30 DAYS
GROUP BY 1 ORDER BY 1;
```

```sql
-- Monthly churn: subscriptions canceled vs. new this month
SELECT
    date_trunc('month', created_at) AS month,
    count(*) FILTER (WHERE status = 'active') AS new_subs,
    count(*) FILTER (WHERE canceled_at IS NOT NULL
        AND date_trunc('month', canceled_at) = date_trunc('month', current_date)) AS canceled
FROM read_parquet('s3://datalake/raw/stripe_subscriptions/**/*.parquet')
GROUP BY 1 ORDER BY 1 DESC LIMIT 6;
```

---

### Shopify — Orders & Inventory

The Shopify pipeline syncs order history, line items, and product inventory from the Shopify Admin API — enabling sales analysis, fulfillment tracking, and product performance reporting outside of Shopify's native reports.

**What it collects (every 6 hours):**

`raw/shopify_orders/` — one row per order:

| Column | Type | Example |
|--------|------|---------|
| `order_id` | string | `"5678901234"` |
| `order_number` | int32 | `1042` |
| `email` | string | `"bob@example.com"` |
| `financial_status` | string | `"paid"` |
| `fulfillment_status` | string | `"fulfilled"` |
| `subtotal_usd` | float | `149.95` |
| `total_discounts_usd` | float | `15.00` |
| `total_usd` | float | `134.95` |
| `line_item_count` | int32 | `3` |
| `source_name` | string | `"web"` |
| `created_at` | timestamp | `2025-05-27 11:04:00` |

`raw/shopify_line_items/` — one row per line item:

| Column | Type | Example |
|--------|------|---------|
| `order_id` | string | `"5678901234"` |
| `product_id` | string | `"7890123456"` |
| `variant_id` | string | `"9012345678"` |
| `title` | string | `"Wireless Keyboard"` |
| `sku` | string | `"KBD-WL-BLK"` |
| `quantity` | int32 | `1` |
| `price_usd` | float | `79.99` |
| `total_discount_usd` | float | `8.00` |
| `fulfillable_quantity` | int32 | `0` |

**Example queries:**

```sql
-- Top products by revenue this quarter
SELECT
    li.title,
    sum(li.quantity) AS units_sold,
    round(sum(li.price_usd * li.quantity - li.total_discount_usd), 2) AS net_revenue
FROM read_parquet('s3://datalake/raw/shopify_line_items/**/*.parquet') li
JOIN read_parquet('s3://datalake/raw/shopify_orders/**/*.parquet') o USING (order_id)
WHERE o.created_at >= date_trunc('quarter', current_date)
GROUP BY 1 ORDER BY net_revenue DESC LIMIT 20;
```

```sql
-- Average order value by traffic source
SELECT
    source_name,
    count(*) AS orders,
    round(avg(total_usd), 2) AS avg_order_value,
    round(sum(total_usd), 2) AS total_revenue
FROM read_parquet('s3://datalake/raw/shopify_orders/**/*.parquet')
WHERE financial_status = 'paid'
  AND created_at >= current_date - INTERVAL 90 DAYS
GROUP BY 1 ORDER BY total_revenue DESC;
```

```sql
-- Unfulfilled orders older than 48 hours
SELECT order_id, order_number, email, total_usd, created_at
FROM read_parquet('s3://datalake/raw/shopify_orders/**/*.parquet')
WHERE fulfillment_status IS NULL
  AND financial_status = 'paid'
  AND created_at < now() - INTERVAL 48 HOURS
ORDER BY created_at;
```

---

### HubSpot — CRM & Pipeline

The HubSpot pipeline syncs contacts, companies, deals, and activity logs daily — giving you a historical record of your sales pipeline that you can join against revenue data from Stripe or web analytics.

**What it collects (daily):**

`raw/hubspot_deals/` — one row per deal snapshot:

| Column | Type | Example |
|--------|------|---------|
| `deal_id` | string | `"12345678"` |
| `deal_name` | string | `"Acme Corp – Enterprise"` |
| `stage` | string | `"contractsent"` |
| `amount_usd` | float | `24000.00` |
| `close_date` | date | `2025-06-30` |
| `owner_name` | string | `"Sarah M."` |
| `pipeline` | string | `"default"` |
| `created_at` | timestamp | `2025-04-12 09:30:00` |
| `last_modified_at` | timestamp | `2025-05-26 16:45:00` |

`raw/hubspot_contacts/` — one row per contact:

| Column | Type | Example |
|--------|------|---------|
| `contact_id` | string | `"98765432"` |
| `email` | string | `"carol@acme.com"` |
| `first_name` | string | `"Carol"` |
| `last_name` | string | `"Jones"` |
| `company` | string | `"Acme Corp"` |
| `lifecycle_stage` | string | `"opportunity"` |
| `lead_source` | string | `"organic search"` |
| `created_at` | timestamp | `2025-03-01 10:00:00` |

**Example queries:**

```sql
-- Open pipeline by stage
SELECT
    stage,
    count(*) AS deals,
    round(sum(amount_usd), 2) AS total_value,
    round(avg(amount_usd), 2) AS avg_deal_size
FROM read_parquet('s3://datalake/raw/hubspot_deals/**/*.parquet')
WHERE stage NOT IN ('closedwon', 'closedlost')
GROUP BY 1 ORDER BY total_value DESC;
```

```sql
-- Win rate and average sales cycle by owner
SELECT
    owner_name,
    count(*) FILTER (WHERE stage = 'closedwon') AS won,
    count(*) FILTER (WHERE stage = 'closedlost') AS lost,
    round(count(*) FILTER (WHERE stage = 'closedwon') * 100.0 / count(*), 1) AS win_rate_pct,
    round(avg(last_modified_at - created_at) FILTER (WHERE stage = 'closedwon'), 0) AS avg_cycle_days
FROM read_parquet('s3://datalake/raw/hubspot_deals/**/*.parquet')
WHERE stage IN ('closedwon', 'closedlost')
GROUP BY 1 ORDER BY won DESC;
```

```sql
-- New contacts by lead source this month
SELECT lead_source, count(*) AS new_contacts
FROM read_parquet('s3://datalake/raw/hubspot_contacts/**/*.parquet')
WHERE created_at >= date_trunc('month', current_date)
GROUP BY 1 ORDER BY new_contacts DESC;
```

---

### PostgreSQL — Application Database

The PostgreSQL pipeline runs configurable incremental queries against your application database and snapshots the results to the lake — useful for capturing user event tables, audit logs, or any operational data you want to analyze without putting load on production.

**What it collects (hourly):** Schema is fully configurable. A typical deployment captures user signups, feature events, and error logs.

**Example queries:**

```sql
-- Join app signups (from Postgres) with closed-won deals (from HubSpot)
SELECT
    date_trunc('week', u.created_at) AS week,
    count(distinct u.user_id) AS signups,
    count(distinct d.deal_id) AS closed_won
FROM read_parquet('s3://datalake/raw/postgres/users/**/*.parquet') u
LEFT JOIN read_parquet('s3://datalake/raw/hubspot_deals/**/*.parquet') d
  ON u.email = d.owner_name AND d.stage = 'closedwon'
GROUP BY 1 ORDER BY 1 DESC LIMIT 12;
```

---

### Open-Meteo — Weather (Join Dimension)

Downloads hourly forecasts for a given location from [Open-Meteo](https://open-meteo.com) — no API key required. Columns include temperature, precipitation, wind speed, UV index, and cloud cover. Useful as a join dimension for any business with location-sensitive demand (retail foot traffic, delivery logistics, outdoor services, etc.).

Set your location in `.env`:
```
WEATHER_LAT=40.7128
WEATHER_LON=-74.0060
```

```sql
-- Do Shopify orders drop on rainy days?
SELECT
    round(w.precipitation_sum, 0) AS rain_mm,
    count(o.order_id) AS orders,
    round(avg(o.total_usd), 2) AS avg_order_value
FROM read_parquet('s3://datalake/raw/shopify_orders/**/*.parquet') o
JOIN read_parquet('s3://datalake/raw/weather/**/*.parquet') w
  ON date_trunc('day', o.created_at) = w.date
GROUP BY 1 ORDER BY 1;
```

---

## Pipeline

After raw data lands in `s3://datalake/raw/`, three downstream pipelines run in sequence each morning:

### 1. Embed (`pipeline/embed/embed_sources.py`) — 06:30 daily

Reads yesterday's Parquet files, builds a text representation of each row, and calls `gemini-embedding-001` to produce a 768-dimensional embedding vector. Results are written to `s3://datalake/embeddings/<source>/` as Parquet. The DuckDB VSS extension (HNSW index) is used for all vector similarity queries; both sides of every `array_cosine_similarity` call must cast to `DOUBLE[768]`.

### 2. Summarize (`pipeline/embed/summarize.py`) — 07:00 daily

Reads yesterday's raw data and generates a concise natural-language summary per source using `gemini-2.5-flash`. Summaries are stored back to MinIO and surfaced in the Lake UI dashboard.

### 3. Anomaly Detection (`pipeline/embed/anomaly_detect.py`) — 08:00 daily

Runs scikit-learn Isolation Forest over numerical columns to flag statistical outliers. Flagged rows are passed to `gemini-2.5-flash` for a plain-English explanation of what the anomaly might mean. Results appear in the Lake UI alerts panel.

### RAG Query (`pipeline/query/rag_query.py`) — on-demand

Given a natural-language question, embeds the query with `gemini-embedding-001`, performs cosine similarity search over the embeddings in DuckDB, retrieves the top-K most relevant rows, and passes them as context to `gemini-2.5-flash` to generate a grounded answer.

---

## Adding a New Data Source

1. Create `pipeline/ingest/my_source.py`. Follow the pattern of an existing ingest script — read from your source, build a list of dicts, write Parquet to `s3://datalake/raw/my_source/year=YYYY/month=MM/day=DD/data.parquet` using the `StorageWriter` helper from `pipeline/shared/storage_writer.py`.

2. Create a DAG in `pipeline/dags/ingest_my_source_dag.py`. Copy an existing DAG and update the schedule interval, script reference, and task IDs.

3. Add your source name to the `SOURCES` list in `pipeline/embed/embed_sources.py` so embeddings are generated for the new data.

4. If your source has a text field worth embedding, define a `row_to_text()` function for it following the existing `stripe_row_to_text` / `hubspot_row_to_text` pattern.

---

## Architecture Notes

- **All data is Parquet.** Files are partitioned by `year=/month=/day=` and written with `pyarrow`. DuckDB's Hive partitioning support makes date-range queries highly efficient.

- **No separate vector database.** Embeddings are stored as `DOUBLE[]` columns in Parquet files. The DuckDB VSS extension builds an in-process HNSW index at query time. This keeps the stack simple — there is no Pinecone, Weaviate, or pgvector to operate.

- **Embedding type discipline.** Parquet stores vectors as `DOUBLE[]` (unbounded list). DuckDB VSS requires fixed-size arrays. Every cosine similarity query must cast both sides: `array_cosine_similarity(embedding::DOUBLE[768], $query_vec::DOUBLE[768])`.

- **MinIO CE only.** The bucket layout uses a single `datalake` bucket with logical prefixes (`raw/`, `embeddings/`). Do not create additional top-level buckets — DuckDB S3 glob patterns depend on this layout.

- **Single-node first.** The Docker Compose deployment is the primary supported path. The Helm chart targets a standard Kubernetes cluster with an NFS-backed StorageClass for the MinIO PVC, but the data model is identical.

---

## Contributing

Contributions are welcome. Please open an issue before starting significant work so we can discuss the approach. Bug fixes and documentation improvements can go straight to a pull request.

1. Fork the repository and create a feature branch.
2. Keep pull requests focused — one feature or fix per PR.
3. Include a brief description of what changed and why.

[Open an issue](https://github.com/hubbertj/lake-of-tears/issues) · [Browse open issues](https://github.com/hubbertj/lake-of-tears/issues)

---

## License

MIT License — see [LICENSE](LICENSE) for details.
