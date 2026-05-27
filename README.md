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
git clone https://github.com/YOUR_USERNAME/lake-of-tears
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

Lake of Tears ships with four built-in ingest pipelines. All data is written as Parquet files partitioned by `year=/month=/day=` under `s3://datalake/raw/<source>/`.

| Source | Script | Schedule | Output path |
|--------|--------|----------|-------------|
| **TrueNAS** | `pipeline/ingest/truenas.py` | Hourly | `raw/truenas/` |
| **TrueNAS Logs** | `pipeline/ingest/truenas_logs.py` | Daily 00:00 | `raw/truenas_logs/` |
| **Jellyfin** | `pipeline/ingest/jellyfin.py` | Every 6 h | `raw/jellyfin/` |
| **Open-Meteo** | `pipeline/ingest/weather.py` | Daily 06:00 | `raw/weather/` |
| **Alexa** | `pipeline/ingest/alexa.py` | On-demand | `raw/alexa/` |

**TrueNAS** — pulls ZFS pool statistics and per-disk SMART metrics from the TrueNAS REST API. Requires `TRUENAS_HOST` and `TRUENAS_API_KEY`.

**TrueNAS Logs** — collects journal WARNING+ entries and active alerts. Requires the same credentials as the TrueNAS source.

**Jellyfin** — fetches media play history (title, user, duration, timestamp). Requires `JELLYFIN_URL` and `JELLYFIN_API_KEY`.

**Open-Meteo** — downloads hourly weather forecasts for a configurable location using the free Open-Meteo API. No API key required.

**Alexa** — parses an Amazon Alexa data export (JSON). Run on-demand after dropping an export into the configured input directory.

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

[Open an issue](https://github.com/YOUR_USERNAME/lake-of-tears/issues) · [Browse open issues](https://github.com/YOUR_USERNAME/lake-of-tears/issues)

---

## License

MIT License — see [LICENSE](LICENSE) for details.
