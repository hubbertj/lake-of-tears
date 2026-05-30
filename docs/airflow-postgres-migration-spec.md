# Airflow PostgreSQL Migration Spec

## Overview

Migrate Airflow's metadata database from SQLite (development-only) to the existing
PostgreSQL instance. A new `airflow` database is provisioned alongside `lake_auth` on
the same pod — one Postgres instance, two isolated databases.

---

## Motivation

- SQLite does not support concurrent writes; Airflow warns against it in production.
- SQLite forces `SequentialExecutor` — only one task can run at a time.
- With PostgreSQL, `LocalExecutor` is enabled — multiple DAG tasks run in parallel.
- Airflow state (DAG runs, task instances, variables, connections) becomes durable and
  survives PVC issues.

---

## Current State

| Item | Value |
|------|-------|
| Airflow DB | SQLite at `/opt/airflow/airflow.db` on a 2Gi PVC |
| Executor | `SequentialExecutor` |
| Postgres pod | Running, single database `lake_auth` |

---

## Target State

| Item | Value |
|------|-------|
| Airflow DB | PostgreSQL `airflow` database on existing Postgres pod |
| Executor | `LocalExecutor` |
| Airflow PVC | Retained (smaller — 2Gi → 1Gi) for DAGs and logs only |
| Postgres connection | `postgresql://airflow:<password>@<postgres-svc>:5432/airflow` |

---

## Changes Required

### 1. Postgres — Create `airflow` Database and User

A ConfigMap (`postgres-initdb`) is mounted at `/docker-entrypoint-initdb.d/` in the
Postgres pod. The script runs automatically on first initialization of a **new**
deployment.

```sql
-- /docker-entrypoint-initdb.d/01-airflow.sql
CREATE USER airflow WITH PASSWORD '<airflow_db_password>';
CREATE DATABASE airflow OWNER airflow;
GRANT ALL PRIVILEGES ON DATABASE airflow TO airflow;
```

For the **existing live deployment**, a one-time Kubernetes Job runs this SQL against
the running Postgres pod (since init scripts only execute on first boot).

### 2. New Helm Values

```yaml
# values.yaml additions
postgres:
  airflow:
    username: airflow
    password: ""   # REQUIRED — set in values-secret.yaml
```

### 3. New Secret Key

`secrets.yaml` adds:

```yaml
AIRFLOW_DB_PASSWORD: {{ required "postgres.airflow.password is required" .Values.postgres.airflow.password | b64enc }}
```

### 4. Airflow Deployment — Environment Variables

Replace SQLite with Postgres connection and upgrade executor:

```yaml
# Remove (SQLite no longer used):
# AIRFLOW__CORE__EXECUTOR: SequentialExecutor

# Add:
- name: AIRFLOW__CORE__EXECUTOR
  value: LocalExecutor

- name: AIRFLOW__DATABASE__SQL_ALCHEMY_CONN
  valueFrom:
    secretKeyRef:
      name: <release>-credentials
      key: AIRFLOW_DB_CONN
```

The connection string secret is rendered as:
```
postgresql+psycopg2://airflow:<password>@<release>-postgres:5432/airflow
```

### 5. Airflow PVC

Reduced from 2Gi to 1Gi — no longer stores the SQLite database file. Still needed for:
- `/opt/airflow/dags/` — DAG Python files
- `/opt/airflow/logs/` — task execution logs
- `/opt/airflow/webserver_config.py` — FAB config (`AUTH_ROLE_PUBLIC`)

---

## Migration Path (Live Deployment)

Because the Postgres init script only runs on a fresh data directory, existing
deployments need a one-time manual step before deploying the new Helm chart.

**Step 1** — Run the provisioning job on the VM:

```bash
kubectl exec -n lake-of-tears <postgres-pod> -- psql -U lake_auth -d lake_auth -c \
  "CREATE USER airflow WITH PASSWORD '<password>'; \
   CREATE DATABASE airflow OWNER airflow; \
   GRANT ALL PRIVILEGES ON DATABASE airflow TO airflow;"
```

**Step 2** — Add the new secret to `/etc/lake-of-tears/values-secret.yaml` on the VM:

```yaml
postgres:
  airflow:
    password: "<password>"
```

**Step 3** — Deploy via Jenkins (normal push to main triggers build).

Airflow will run `airflow db migrate` on startup, create all its schema tables in the
new Postgres database, and boot cleanly with no SQLite warning.

**Step 4** — After confirming Airflow is healthy, delete the old SQLite file:

```bash
kubectl exec -n lake-of-tears <airflow-pod> -- rm /opt/airflow/airflow.db
```

---

## What Is NOT Migrated

Existing Airflow history (DAG runs, task logs, variables, connections from SQLite) is
not migrated. Airflow starts with a clean metadata database. Task logs on the PVC are
retained.

This is acceptable because:
- The current SQLite DB is from a development/testing phase.
- No production DAG history exists yet.

---

## Decisions

| Decision | Choice | Reason |
|----------|--------|--------|
| Same Postgres pod vs separate | Same pod | Home lab; operational simplicity outweighs isolation benefit |
| Migrate existing SQLite data | No | Dev-phase data only; clean start is simpler |
| Executor after migration | `LocalExecutor` | Postgres supports concurrent writes; parallel tasks beneficial |
| Keep Airflow PVC | Yes (reduced) | Still needed for DAGs and logs |
| Airflow DB user | Separate `airflow` user | Least privilege; `lake_auth` user cannot access `airflow` DB |
