from __future__ import annotations

import os
import math
from pathlib import Path
from datetime import datetime, timezone, timedelta
from typing import Any

import boto3
import botocore.exceptions
import duckdb
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from google import genai
from google.genai import types

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path)

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "datalake")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")

SOURCES = ["truenas", "jellyfin", "weather", "truenas_logs", "alexa"]
SOURCE_LABELS = {
    "truenas": "TrueNAS",
    "jellyfin": "Jellyfin",
    "weather": "Weather",
    "truenas_logs": "TrueNAS Logs",
    "alexa": "Alexa",
}

# ---------------------------------------------------------------------------
# App & templates
# ---------------------------------------------------------------------------

app = FastAPI(title="Lake of Tears")
templates = Jinja2Templates(directory=Path(__file__).parent / "templates")

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _s3_client():
    return boto3.client(
        "s3",
        endpoint_url=f"http://{MINIO_ENDPOINT}",
        aws_access_key_id=MINIO_ACCESS_KEY,
        aws_secret_access_key=MINIO_SECRET_KEY,
        region_name="us-east-1",
    )


def _human_size(num_bytes: int) -> str:
    if num_bytes == 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    exp = min(int(math.log(num_bytes, 1024)), len(units) - 1)
    val = num_bytes / (1024**exp)
    return f"{val:.1f} {units[exp]}"


def _source_status(last_modified: datetime | None) -> str:
    """Return 'green', 'yellow', or 'red' based on staleness."""
    if last_modified is None:
        return "red"
    now = datetime.now(timezone.utc)
    age = now - last_modified
    if age <= timedelta(hours=25):
        return "green"
    if age <= timedelta(hours=48):
        return "yellow"
    return "red"


def _get_bucket_stats() -> dict[str, Any]:
    """Return bucket-level stats from MinIO, or an error dict."""
    try:
        s3 = _s3_client()
        paginator = s3.get_paginator("list_objects_v2")
        total_objects = 0
        total_size = 0
        prefix_stats: dict[str, dict] = {}
        source_last_modified: dict[str, datetime | None] = {s: None for s in SOURCES}

        pages = paginator.paginate(Bucket=MINIO_BUCKET)
        for page in pages:
            for obj in page.get("Contents", []):
                key: str = obj["Key"]
                size: int = obj["Size"]
                last_mod: datetime = obj["LastModified"]

                total_objects += 1
                total_size += size

                # Top-level prefix (raw/ or embeddings/)
                top = key.split("/")[0] if "/" in key else key
                if top not in prefix_stats:
                    prefix_stats[top] = {"objects": 0, "size": 0, "last_modified": None}
                prefix_stats[top]["objects"] += 1
                prefix_stats[top]["size"] += size
                if (
                    prefix_stats[top]["last_modified"] is None
                    or last_mod > prefix_stats[top]["last_modified"]
                ):
                    prefix_stats[top]["last_modified"] = last_mod

                # Source-level last modified
                for src in SOURCES:
                    if f"/{src}/" in key or key.startswith(f"{src}/"):
                        if (
                            source_last_modified[src] is None
                            or last_mod > source_last_modified[src]
                        ):
                            source_last_modified[src] = last_mod

        sources = []
        for src in SOURCES:
            lm = source_last_modified[src]
            sources.append(
                {
                    "key": src,
                    "label": SOURCE_LABELS[src],
                    "last_modified": lm,
                    "last_modified_str": lm.strftime("%Y-%m-%d %H:%M UTC") if lm else "Never",
                    "status": _source_status(lm),
                }
            )

        raw = prefix_stats.get("raw", {"objects": 0, "size": 0})
        embeddings = prefix_stats.get("embeddings", {"objects": 0, "size": 0})

        return {
            "ok": True,
            "total_objects": total_objects,
            "total_size": _human_size(total_size),
            "raw_objects": raw["objects"],
            "raw_size": _human_size(raw["size"]),
            "embeddings_objects": embeddings["objects"],
            "embeddings_size": _human_size(embeddings["size"]),
            "prefix_stats": {
                k: {
                    "objects": v["objects"],
                    "size": _human_size(v["size"]),
                    "last_modified": v["last_modified"],
                    "last_modified_str": (
                        v["last_modified"].strftime("%Y-%m-%d %H:%M UTC")
                        if v["last_modified"]
                        else "—"
                    ),
                }
                for k, v in prefix_stats.items()
            },
            "sources": sources,
        }
    except (botocore.exceptions.EndpointConnectionError, botocore.exceptions.ConnectionError, Exception) as exc:
        return {"ok": False, "error": str(exc)}


def _get_browse_data() -> dict[str, Any]:
    """Return per-prefix listing for browse view."""
    try:
        s3 = _s3_client()
        paginator = s3.get_paginator("list_objects_v2")

        # Group by source prefix inside raw/ and embeddings/
        groups: dict[str, dict] = {}

        pages = paginator.paginate(Bucket=MINIO_BUCKET)
        for page in pages:
            for obj in page.get("Contents", []):
                key: str = obj["Key"]
                size: int = obj["Size"]
                last_mod: datetime = obj["LastModified"]

                parts = key.split("/")
                # Determine grouping key: top-level/source
                if len(parts) >= 2:
                    group_key = f"{parts[0]}/{parts[1]}"
                else:
                    group_key = parts[0]

                if group_key not in groups:
                    groups[group_key] = {
                        "prefix": group_key,
                        "objects": 0,
                        "size": 0,
                        "last_modified": None,
                    }
                groups[group_key]["objects"] += 1
                groups[group_key]["size"] += size
                if (
                    groups[group_key]["last_modified"] is None
                    or last_mod > groups[group_key]["last_modified"]
                ):
                    groups[group_key]["last_modified"] = last_mod

        rows = []
        for g in sorted(groups.values(), key=lambda x: x["prefix"]):
            lm = g["last_modified"]
            rows.append(
                {
                    "prefix": g["prefix"],
                    "objects": g["objects"],
                    "size": _human_size(g["size"]),
                    "last_modified": lm.strftime("%Y-%m-%d %H:%M UTC") if lm else "—",
                    "s3_path": f"s3://{MINIO_BUCKET}/{g['prefix']}/**/*.parquet",
                }
            )

        return {"ok": True, "rows": rows}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _duckdb_connection():
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


def _run_query(sql: str) -> dict[str, Any]:
    try:
        con = _duckdb_connection()
        rel = con.execute(sql)
        columns = [desc[0] for desc in rel.description]
        rows = rel.fetchall()
        con.close()
        return {"ok": True, "columns": columns, "rows": rows, "row_count": len(rows)}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


def _ai_query(question: str) -> dict[str, Any]:
    if not GEMINI_API_KEY:
        return {"ok": False, "error": "GEMINI_API_KEY is not set."}
    try:
        client = genai.Client(api_key=GEMINI_API_KEY)

        # Embed the question
        embed_resp = client.models.embed_content(
            model="gemini-embedding-001",
            contents=question,
        )
        q_vec = embed_resp.embeddings[0].values
        dim = len(q_vec)

        # VSS search in DuckDB
        vec_literal = "[" + ", ".join(str(v) for v in q_vec) + "]"
        sql = f"""
            INSTALL httpfs; LOAD httpfs; INSTALL vss; LOAD vss;
            SET s3_endpoint='{MINIO_ENDPOINT}';
            SET s3_access_key_id='{MINIO_ACCESS_KEY}';
            SET s3_secret_access_key='{MINIO_SECRET_KEY}';
            SET s3_use_ssl=false;
            SET s3_url_style='path';
            SELECT
                source,
                ingested_at,
                summary,
                array_cosine_similarity(
                    embedding::DOUBLE[{dim}],
                    {vec_literal}::DOUBLE[{dim}]
                ) AS score
            FROM read_parquet('s3://{MINIO_BUCKET}/embeddings/**/*.parquet')
            ORDER BY score DESC
            LIMIT 5
        """
        con = duckdb.connect()
        rel = con.execute(sql)
        rows = rel.fetchall()
        columns = [desc[0] for desc in rel.description]
        con.close()

        context_snippets = []
        for row in rows:
            row_dict = dict(zip(columns, row))
            snippet = {
                "source": row_dict.get("source", ""),
                "ingested_at": str(row_dict.get("ingested_at", "")),
                "summary": row_dict.get("summary", ""),
                "score": round(float(row_dict.get("score", 0)), 4),
            }
            context_snippets.append(snippet)

        context_text = "\n\n".join(
            f"[{s['source']} @ {s['ingested_at']} (score={s['score']})]:\n{s['summary']}"
            for s in context_snippets
        )

        prompt = (
            f"You are an assistant for a home lab datalakehouse called Lake of Tears. "
            f"Answer the following question using the context snippets below. "
            f"Be concise and cite which data sources support your answer.\n\n"
            f"Question: {question}\n\n"
            f"Context:\n{context_text}"
        )

        chat_resp = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        )
        answer = chat_resp.text

        return {"ok": True, "answer": answer, "snippets": context_snippets}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

EXAMPLE_QUERIES = [
    {
        "label": "Recent TrueNAS stats",
        "sql": "SELECT * FROM read_parquet('s3://datalake/raw/truenas/**/*.parquet') ORDER BY ingested_at DESC LIMIT 10",
    },
    {
        "label": "Latest weather forecast",
        "sql": "SELECT * FROM read_parquet('s3://datalake/raw/weather/**/*.parquet') ORDER BY ingested_at DESC LIMIT 10",
    },
    {
        "label": "Embedding count by source",
        "sql": "SELECT source, COUNT(*) AS cnt FROM read_parquet('s3://datalake/embeddings/**/*.parquet') GROUP BY source ORDER BY cnt DESC",
    },
    {
        "label": "Recent Jellyfin plays",
        "sql": "SELECT * FROM read_parquet('s3://datalake/raw/jellyfin/**/*.parquet') ORDER BY ingested_at DESC LIMIT 10",
    },
]

EXAMPLE_QUESTIONS = [
    "What TrueNAS alerts have fired in the last week?",
    "What movies or shows have been watched recently on Jellyfin?",
    "What is the current weather forecast summary?",
    "Are there any disk health warnings?",
]


@app.get("/", response_class=RedirectResponse)
async def root():
    return RedirectResponse(url="/dashboard", status_code=302)


@app.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request):
    stats = _get_bucket_stats()
    return templates.TemplateResponse(
        "dashboard.html",
        {"request": request, "stats": stats, "page": "dashboard"},
    )


@app.get("/browse", response_class=HTMLResponse)
async def browse(request: Request):
    data = _get_browse_data()
    return templates.TemplateResponse(
        "browse.html",
        {"request": request, "data": data, "page": "browse"},
    )


@app.get("/query", response_class=HTMLResponse)
async def query_get(request: Request):
    return templates.TemplateResponse(
        "query.html",
        {
            "request": request,
            "page": "query",
            "result": None,
            "sql": EXAMPLE_QUERIES[0]["sql"],
            "example_queries": EXAMPLE_QUERIES,
        },
    )


@app.post("/query", response_class=HTMLResponse)
async def query_post(request: Request, sql: str = Form(...)):
    result = _run_query(sql)
    return templates.TemplateResponse(
        "query.html",
        {
            "request": request,
            "page": "query",
            "result": result,
            "sql": sql,
            "example_queries": EXAMPLE_QUERIES,
        },
    )


@app.get("/ai", response_class=HTMLResponse)
async def ai_get(request: Request):
    return templates.TemplateResponse(
        "ai.html",
        {
            "request": request,
            "page": "ai",
            "result": None,
            "question": "",
            "example_questions": EXAMPLE_QUESTIONS,
        },
    )


@app.post("/ai", response_class=HTMLResponse)
async def ai_post(request: Request, question: str = Form(...)):
    result = _ai_query(question)
    return templates.TemplateResponse(
        "ai.html",
        {
            "request": request,
            "page": "ai",
            "result": result,
            "question": question,
            "example_questions": EXAMPLE_QUESTIONS,
        },
    )
