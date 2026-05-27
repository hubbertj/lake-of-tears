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


def write_to_storage(df, source: str, date_str: str):
    con = get_duckdb_con()
    con.register("df", df)
    y, m, d = date_str[:4], date_str[5:7], date_str[8:10]
    con.execute(f"""
        COPY df TO 's3://datalake/raw/{source}/year={y}/month={m}/day={d}/data.parquet'
        (FORMAT PARQUET)
    """)
