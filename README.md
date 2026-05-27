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
│  TrueNAS API ├───►│  Airflow  ──►  MinIO CE (S3)          │
│  Jellyfin    ├───►│  Pipeline     s3://datalake/raw/       │
│  Open-Meteo  ├───►│               s3://datalake/embeddings/│
│  Alexa       ├───►│                     │                  │
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
| `TRUENAS_HOST` | no | — | TrueNAS host IP (for TrueNAS ingestion) |
| `TRUENAS_API_KEY` | no | — | TrueNAS API Bearer token |
| `JELLYFIN_URL` | no | — | Jellyfin base URL |
| `JELLYFIN_API_KEY` | no | — | Jellyfin API key |
| `DATALAKE_DATA_DIR` | yes | `~` | Host path for Docker volume mounts |

---

## Data Sources

Lake of Tears is built around the data that a home server actually generates. Every pipeline writes Parquet files partitioned by `year=/month=/day=` to `s3://datalake/raw/<source>/`, queryable immediately with DuckDB.

| Source | Script | Schedule | Output path | Requires |
|--------|--------|----------|-------------|----------|
| **TrueNAS** | `pipeline/ingest/truenas.py` | Hourly | `raw/truenas/` | `TRUENAS_HOST`, `TRUENAS_API_KEY` |
| **TrueNAS Logs** | `pipeline/ingest/truenas_logs.py` | Daily 00:00 | `raw/truenas_logs/` | same as above |
| **Jellyfin** | `pipeline/ingest/jellyfin.py` | Every 6 h | `raw/jellyfin/` | `JELLYFIN_URL`, `JELLYFIN_API_KEY` |
| **Open-Meteo** | `pipeline/ingest/weather.py` | Daily 06:00 | `raw/weather/` | none (free API) |
| **Alexa** | `pipeline/ingest/alexa.py` | On-demand | `raw/alexa/` | export file |

---

### TrueNAS — Home NAS Health Monitoring

TrueNAS SCALE is a common choice for home network-attached storage: a box in a closet or rack running ZFS, storing family photos, backups, and media. The TrueNAS pipeline treats that box as a data source — not just a drive to map.

**What it collects (hourly):**

`pipeline/ingest/truenas.py` calls the TrueNAS REST API (`/api/v2.0/pool` and `/api/v2.0/disk`) and writes two datasets:

`raw/truenas/` — pool-level health snapshot per hour:

| Column | Type | Example |
|--------|------|---------|
| `pool_name` | string | `"WD-RAID-Z-18TB"` |
| `status` | string | `"ONLINE"` |
| `size_bytes` | int64 | `19318669312` |
| `allocated_bytes` | int64 | `11274289152` |
| `free_bytes` | int64 | `8044380160` |
| `fragmentation_pct` | float | `3.0` |
| `scan_state` | string | `"finished"` |
| `scan_errors` | int64 | `0` |
| `timestamp` | timestamp | `2025-05-27 06:00:00` |

`raw/truenas_disks/` — per-disk SMART attributes snapshot:

| Column | Type | Example |
|--------|------|---------|
| `disk_name` | string | `"sda"` |
| `model` | string | `"WDC WD80EFZX"` |
| `serial` | string | `"WD-CA1XXXXX"` |
| `temperature_c` | float | `34.0` |
| `reallocated_sectors` | int64 | `0` |
| `pending_sectors` | int64 | `0` |
| `uncorrectable_errors` | int64 | `0` |
| `power_on_hours` | int64 | `14832` |
| `timestamp` | timestamp | `2025-05-27 06:00:00` |

`raw/truenas_logs/` (daily) — WARNING and above journal entries plus any active TrueNAS alerts. Useful for catching scrub errors, degraded vdevs, or failed drives before they become data loss.

**Example queries:**

```sql
-- How full is my pool this week?
SELECT
    date_trunc('day', timestamp) AS day,
    round(max(allocated_bytes) / 1e9, 1) AS used_gb,
    round(max(size_bytes) / 1e9, 1) AS total_gb,
    round(max(allocated_bytes) * 100.0 / max(size_bytes), 1) AS pct_used
FROM read_parquet('s3://datalake/raw/truenas/**/*.parquet')
WHERE timestamp >= current_date - INTERVAL 7 DAYS
GROUP BY 1 ORDER BY 1;
```

```sql
-- Which disks are running hottest?
SELECT disk_name, model, round(avg(temperature_c), 1) AS avg_temp_c, max(temperature_c) AS peak_temp_c
FROM read_parquet('s3://datalake/raw/truenas_disks/**/*.parquet')
WHERE timestamp >= current_date - INTERVAL 30 DAYS
GROUP BY 1, 2 ORDER BY avg_temp_c DESC;
```

```sql
-- Any reallocated sectors appearing? (early sign of drive failure)
SELECT timestamp, disk_name, model, reallocated_sectors, pending_sectors
FROM read_parquet('s3://datalake/raw/truenas_disks/**/*.parquet')
WHERE reallocated_sectors > 0 OR pending_sectors > 0
ORDER BY timestamp DESC;
```

```sql
-- Recent warnings and alerts
SELECT timestamp, message
FROM read_parquet('s3://datalake/raw/truenas_logs/**/*.parquet')
WHERE timestamp >= current_date - INTERVAL 7 DAYS
ORDER BY timestamp DESC LIMIT 50;
```

---

### Jellyfin — Personal Media Server Analytics

Jellyfin is a free, self-hosted media server — the home alternative to Netflix. You rip your Blu-rays, rip your CDs, drop your downloads in a folder, and Jellyfin streams them to your TV, phone, or browser. The Jellyfin pipeline captures your actual watch and listen history from your own server, not a corporation's servers.

**What it collects (every 6 hours):**

`pipeline/ingest/jellyfin.py` calls the Jellyfin REST API (`/Users/{userId}/Items?Recursive=true&Fields=...`) and writes:

`raw/jellyfin/` — one row per play event:

| Column | Type | Example |
|--------|------|---------|
| `item_id` | string | `"a3f9b2c1..."` |
| `title` | string | `"Oppenheimer"` |
| `series_name` | string | `"The Bear"` (null for movies) |
| `season_episode` | string | `"S02E05"` (null for movies) |
| `media_type` | string | `"Movie"` / `"Episode"` / `"Audio"` |
| `genres` | string | `"Drama, History, Thriller"` |
| `year` | int32 | `2023` |
| `duration_min` | float | `181.0` |
| `play_count` | int32 | `2` |
| `last_played` | timestamp | `2025-05-26 21:14:00` |
| `user_name` | string | `"jay"` |
| `rating` | float | `8.9` (community rating) |

**Example queries:**

```sql
-- What have I watched most this month?
SELECT title, media_type, sum(play_count) AS plays, round(sum(play_count * duration_min) / 60, 1) AS hours
FROM read_parquet('s3://datalake/raw/jellyfin/**/*.parquet')
WHERE last_played >= date_trunc('month', current_date)
GROUP BY 1, 2 ORDER BY plays DESC LIMIT 20;
```

```sql
-- How many hours of TV vs movies have I watched this year?
SELECT
    media_type,
    count(distinct item_id) AS titles,
    sum(play_count) AS total_plays,
    round(sum(play_count * duration_min) / 60, 1) AS total_hours
FROM read_parquet('s3://datalake/raw/jellyfin/**/*.parquet')
WHERE last_played >= date_trunc('year', current_date)
GROUP BY 1;
```

```sql
-- My watch activity by day of week (am I a weekend binge-watcher?)
SELECT
    dayname(last_played) AS day_of_week,
    count(*) AS plays,
    round(sum(duration_min) / 60, 1) AS hours
FROM read_parquet('s3://datalake/raw/jellyfin/**/*.parquet')
GROUP BY 1 ORDER BY count(*) DESC;
```

```sql
-- Which genres do I actually watch vs what I have?
SELECT unnest(string_split(genres, ', ')) AS genre, count(*) AS plays
FROM read_parquet('s3://datalake/raw/jellyfin/**/*.parquet')
WHERE play_count > 0
GROUP BY 1 ORDER BY plays DESC LIMIT 15;
```

```sql
-- Combine with weather: do I watch more when it's cold?
SELECT
    round(w.temperature_2m_max, 0) AS temp_c,
    count(j.item_id) AS plays
FROM read_parquet('s3://datalake/raw/jellyfin/**/*.parquet') j
JOIN read_parquet('s3://datalake/raw/weather/**/*.parquet') w
  ON date_trunc('day', j.last_played) = w.date
GROUP BY 1 ORDER BY 1;
```

---

### Open-Meteo — Local Weather

Downloads hourly forecasts for your location from [Open-Meteo](https://open-meteo.com) — no API key required. Columns include temperature, precipitation, wind speed, UV index, and cloud cover. Primarily useful as a join dimension (weather vs. energy use, weather vs. watch habits, etc.).

Set your location in `.env`:
```
WEATHER_LAT=40.7934
WEATHER_LON=-77.8600
```

---

### Alexa — Voice Assistant History

Parses an Amazon Alexa data export (request yours at [privacy.amazon.com](https://privacy.amazon.com)). Drop the export JSON files into the configured input directory and run the pipeline on-demand. Columns include utterance text, device name, timestamp, and intent — useful for seeing patterns in how you actually use voice commands at home.

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

4. If your source has a text field worth embedding, define a `row_to_text()` function for it following the existing `truenas_row_to_text` / `jellyfin_row_to_text` pattern.

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
