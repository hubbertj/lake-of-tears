import os
import pathlib

import duckdb


def _load_env():
    for candidate in (
        pathlib.Path(__file__).parent / ".env",
        pathlib.Path(__file__).parent.parent.parent / ".env",
    ):
        if candidate.exists():
            with open(candidate) as f:
                for line in f:
                    line = line.strip()
                    if line and not line.startswith("#") and "=" in line:
                        k, _, v = line.partition("=")
                        os.environ.setdefault(k.strip(), v.strip())
            break


_load_env()

MINIO_ENDPOINT = os.environ.get("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "")


def get_duckdb_con() -> duckdb.DuckDBPyConnection:
    con = duckdb.connect()
    con.execute(f"""
        INSTALL httpfs; LOAD httpfs;
        SET s3_endpoint='{MINIO_ENDPOINT}';
        SET s3_access_key_id='{MINIO_ACCESS_KEY}';
        SET s3_secret_access_key='{MINIO_SECRET_KEY}';
        SET s3_use_ssl=false;
        SET s3_url_style='path';
    """)
    return con


def write_to_storage(
    df,
    source: str | None = None,
    date_str: str | None = None,
    *,
    catalog: str | None = None,
    schema: str | None = None,
    table: str | None = None,
):
    """Write a DataFrame to MinIO as Parquet.

    Legacy usage: write_to_storage(df, source="stripe_charges", date_str="2024-01-01")
      → s3://datalake/raw/stripe_charges/year=.../

    Catalog usage: write_to_storage(df, date_str="2024-01-01", catalog="production", schema="revenue", table="stripe_charges")
      → s3://datalake/production/revenue/stripe_charges/year=.../
    """
    if date_str is None:
        from datetime import date

        date_str = date.today().isoformat()

    y, m, d = date_str[:4], date_str[5:7], date_str[8:10]

    if catalog and schema and table:
        path = f"s3://datalake/{catalog}/{schema}/{table}/year={y}/month={m}/day={d}/data.parquet"
    elif source:
        path = f"s3://datalake/raw/{source}/year={y}/month={m}/day={d}/data.parquet"
    else:
        raise ValueError("Either 'source' or all of 'catalog', 'schema', 'table' must be provided")

    con = get_duckdb_con()
    con.register("df", df)
    con.execute(f"COPY df TO '{path}' (FORMAT PARQUET)")
