# Contributing to Lake of Tears

Thank you for your interest in contributing. Lake of Tears is an open source self-hosted datalakehouse — community contributions make it better for everyone.

## Community

- **Website:** [lakeoftears.ai](https://lakeoftears.ai)
- **Discord:** Join the community via [lakeoftears.ai](https://lakeoftears.ai) (link on the homepage)
- **Issues & PRs:** [github.com/hubbertj/lake-of-tears](https://github.com/hubbertj/lake-of-tears)

---

## Ways to Contribute

- **Bug reports** — open an issue using the bug report template
- **Feature requests** — open an issue using the feature request template
- **New data source connectors** — add a new ingest pipeline (see below)
- **UI improvements** — FastAPI + Tailwind dashboard (`ui/`)
- **Infrastructure** — Helm chart, Terraform modules, Docker Compose
- **Documentation** — README, architecture docs, example queries

---

## Getting Started

### Prerequisites

- Docker and Docker Compose
- Python 3.12
- Helm 3.x (for chart changes)
- Terraform 1.9+ (for infrastructure changes)

### Local Development Setup

```bash
git clone https://github.com/hubbertj/lake-of-tears
cd lake-of-tears
cp .env.example .env
# Fill in required credentials (MINIO_ROOT_USER, MINIO_ROOT_PASSWORD, GEMINI_API_KEY)
docker compose up -d
```

Services: Lake UI at http://localhost:3000 · Airflow at http://localhost:8080 · MinIO at http://localhost:9001

### Install dev tools

```bash
pip install ruff bandit[toml] pre-commit
pre-commit install
```

`pre-commit install` wires the linter and formatter to run automatically before every commit.

---

## Code Style

This project uses [ruff](https://docs.astral.sh/ruff/) for linting and formatting. Configuration is in `pyproject.toml`.

```bash
# Check linting
ruff check .

# Auto-fix safe issues
ruff check --fix .

# Format
ruff format .
```

All CI checks must pass before a PR can be merged. Run them locally first:

| Check | Command |
|-------|---------|
| Lint | `ruff check .` |
| Format | `ruff format --check .` |
| Security | `bandit -r pipeline/ ui/ -ll -c pyproject.toml` |
| Docker build | `docker build ui/` |
| Helm lint | `helm lint deploy/helm/lake-of-tears` |
| Terraform validate | `terraform -chdir=deploy/terraform/foundation validate` |

---

## Submitting a Pull Request

1. **Fork** the repository and create a branch from `main`.
2. **Keep PRs focused** — one feature or fix per PR. Stacked changes are hard to review.
3. **Fill out the PR template** — describe what changed and why.
4. **All CI checks must pass** before review.
5. **Open an issue first** for significant new features so we can align before you invest time writing code.

Branch naming convention:
```
feat/stripe-connector
fix/duckdb-vss-cast
docs/helm-values
```

---

## Adding a New Data Source

The full walkthrough is in the [README](README.md#adding-a-new-data-source). Summary:

1. `pipeline/ingest/my_source.py` — fetch, normalize to DataFrame, write Parquet via `StorageWriter`
2. `pipeline/dags/ingest_my_source_dag.py` — Airflow DAG with schedule
3. Add source name to `SOURCES` in `pipeline/embed/embed_sources.py`
4. Add `_text_my_source(row)` for the embedding text representation
5. Add the source's env vars to `.env.example` and the Configuration table in `README.md`

---

## Reporting Security Issues

Please **do not** open a public issue for security vulnerabilities. See [SECURITY.md](SECURITY.md) for the responsible disclosure process.
