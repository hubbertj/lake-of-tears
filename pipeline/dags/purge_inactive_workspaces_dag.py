from __future__ import annotations

import os
from datetime import datetime

from airflow import DAG
from airflow.operators.python import PythonOperator

default_args = {
    "owner": "airflow",
    "retries": 3,
    "retry_delay": __import__("datetime").timedelta(minutes=5),
}

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "minio:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "datalake")


def _delete_s3_prefix(prefix: str) -> None:
    import boto3

    s3 = boto3.client(
        "s3",
        endpoint_url=f"http://{MINIO_ENDPOINT}",
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name="us-east-1",
    )
    paginator = s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=MINIO_BUCKET, Prefix=prefix):
        objects = [{"Key": o["Key"]} for o in page.get("Contents", [])]
        if objects:
            s3.delete_objects(Bucket=MINIO_BUCKET, Delete={"Objects": objects})


def purge_inactive_workspaces():
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import sessionmaker

    db_url = os.getenv("DATABASE_URL", "postgresql://lake_auth:lake_auth@postgres:5432/lake_auth")
    engine = create_engine(db_url, pool_pre_ping=True)
    Session = sessionmaker(bind=engine)
    db = Session()

    try:
        due = db.execute(
            text(
                "SELECT id, name FROM workspaces "
                "WHERE status = 'inactive' "
                "  AND scheduled_purge_at IS NOT NULL "
                "  AND scheduled_purge_at <= NOW()"
            )
        ).fetchall()

        print(f"Found {len(due)} workspace(s) due for purge")

        for row in due:
            workspace_id, workspace_name = str(row[0]), row[1]
            print(f"Purging workspace '{workspace_name}' ({workspace_id})")

            # Collect all S3 paths from all catalogs owned by this workspace
            catalog_rows = db.execute(
                text("SELECT id, name FROM catalogs WHERE owner_workspace_id = :wid"),
                {"wid": workspace_id},
            ).fetchall()

            for cat_row in catalog_rows:
                catalog_id, catalog_name = str(cat_row[0]), cat_row[1]
                s3_rows = db.execute(
                    text(
                        "SELECT s3_path_pattern FROM catalog_tables "
                        "WHERE schema_id IN ("
                        "  SELECT id FROM catalog_schemas WHERE catalog_id = :cid"
                        ") AND s3_path_pattern IS NOT NULL"
                    ),
                    {"cid": catalog_id},
                ).fetchall()
                for s3_row in s3_rows:
                    prefix = s3_row[0].rstrip("*").rstrip("/")
                    try:
                        _delete_s3_prefix(prefix)
                        print(f"  Deleted S3 prefix: {prefix}")
                    except Exception as exc:
                        print(f"  S3 delete failed for {prefix}: {exc}")
                        raise  # trigger retry

                print(f"  S3 data cleared for catalog '{catalog_name}'")

            # Hard-delete workspace (cascades to catalogs, schemas, tables, access grants, members)
            db.execute(text("DELETE FROM workspaces WHERE id = :id"), {"id": workspace_id})
            db.commit()
            print(f"  Workspace '{workspace_name}' deleted")

    finally:
        db.close()


with DAG(
    dag_id="purge_inactive_workspaces",
    default_args=default_args,
    description="Hourly purge of inactive workspaces past their grace period",
    schedule_interval="0 * * * *",
    start_date=datetime(2026, 1, 1),
    catchup=False,
    tags=["workspace", "maintenance"],
) as dag:
    PythonOperator(
        task_id="purge_inactive_workspaces",
        python_callable=purge_inactive_workspaces,
    )
