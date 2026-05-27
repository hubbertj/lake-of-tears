from __future__ import annotations

import math
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import boto3
import botocore.exceptions
import duckdb
from dotenv import load_dotenv
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from google import genai

_env_path = Path(__file__).parent.parent / ".env"
load_dotenv(_env_path)

MINIO_ENDPOINT = os.getenv("MINIO_ENDPOINT", "localhost:9000")
MINIO_ACCESS_KEY = os.getenv("MINIO_ACCESS_KEY", "")
MINIO_SECRET_KEY = os.getenv("MINIO_SECRET_KEY", "")
MINIO_BUCKET = os.getenv("MINIO_BUCKET", "datalake")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MINIO_CONSOLE_URL = os.getenv("MINIO_CONSOLE_URL", "http://localhost:9001")

SOURCES = ["stripe", "shopify", "hubspot", "postgres", "weather"]
SOURCE_LABELS = {
    "stripe": "Stripe",
    "shopify": "Shopify",
    "hubspot": "HubSpot",
    "postgres": "PostgreSQL",
    "weather": "Weather",
}

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
    return f"{num_bytes / (1024 ** exp):.1f} {units[exp]}"


def _source_status(last_modified: datetime | None) -> str:
    if last_modified is None:
        return "red"
    age = datetime.now(timezone.utc) - last_modified
    if age <= timedelta(hours=25):
        return "green"
    if age <= timedelta(hours=48):
        return "yellow"
    return "red"


def _get_bucket_stats() -> dict[str, Any]:
    try:
        s3 = _s3_client()
        paginator = s3.get_paginator("list_objects_v2")
        total_objects = total_size = 0
        prefix_stats: dict[str, dict] = {}
        source_last_modified: dict[str, datetime | None] = {s: None for s in SOURCES}

        for page in paginator.paginate(Bucket=MINIO_BUCKET):
            for obj in page.get("Contents", []):
                key: str = obj["Key"]
                size: int = obj["Size"]
                last_mod: datetime = obj["LastModified"]

                total_objects += 1
                total_size += size

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
    except (
        botocore.exceptions.EndpointConnectionError,
        botocore.exceptions.ConnectionError,
        Exception,
    ) as exc:
        return {"ok": False, "error": str(exc)}


def _get_catalog_data() -> dict[str, Any]:
    try:
        s3 = _s3_client()
        paginator = s3.get_paginator("list_objects_v2")
        groups: dict[str, dict] = {}

        for page in paginator.paginate(Bucket=MINIO_BUCKET):
            for obj in page.get("Contents", []):
                key: str = obj["Key"]
                parts = key.split("/")
                group_key = f"{parts[0]}/{parts[1]}" if len(parts) >= 2 else parts[0]

                if group_key not in groups:
                    groups[group_key] = {
                        "prefix": group_key,
                        "objects": 0,
                        "size": 0,
                        "last_modified": None,
                    }
                groups[group_key]["objects"] += 1
                groups[group_key]["size"] += obj["Size"]
                lm = obj["LastModified"]
                if groups[group_key]["last_modified"] is None or lm > groups[group_key]["last_modified"]:
                    groups[group_key]["last_modified"] = lm

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

        embed_resp = client.models.embed_content(
            model="gemini-embedding-001",
            contents=question,
        )
        q_vec = embed_resp.embeddings[0].values
        dim = len(q_vec)
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

        context_snippets = [
            {
                "source": r[0],
                "ingested_at": str(r[1]),
                "summary": r[2],
                "score": round(float(r[3]), 4),
            }
            for r in rows
        ]
        context_text = "\n\n".join(
            f"[{s['source']} @ {s['ingested_at']} (score={s['score']})]:\n{s['summary']}"
            for s in context_snippets
        )
        prompt = (
            "You are an analyst for a business datalakehouse called Lake of Tears. "
            "Answer the following question using the context snippets below. "
            "Be concise and cite which data sources support your answer.\n\n"
            f"Question: {question}\n\nContext:\n{context_text}"
        )

        answer = client.models.generate_content(
            model="gemini-2.5-flash",
            contents=prompt,
        ).text

        return {"ok": True, "answer": answer, "snippets": context_snippets}
    except Exception as exc:
        return {"ok": False, "error": str(exc)}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

EXAMPLE_QUERIES = [
    {
        "label": "Recent Stripe charges",
        "sql": "SELECT * FROM read_parquet('s3://datalake/raw/stripe_charges/**/*.parquet') ORDER BY created_at DESC LIMIT 20",
    },
    {
        "label": "Top Shopify products by revenue",
        "sql": "SELECT title, sum(quantity) AS units, round(sum(price_usd * quantity), 2) AS revenue FROM read_parquet('s3://datalake/raw/shopify_line_items/**/*.parquet') GROUP BY title ORDER BY revenue DESC LIMIT 20",
    },
    {
        "label": "Open HubSpot pipeline by stage",
        "sql": "SELECT stage, count(*) AS deals, round(sum(amount_usd), 2) AS value FROM read_parquet('s3://datalake/raw/hubspot_deals/**/*.parquet') WHERE stage NOT IN ('closedwon','closedlost') GROUP BY stage ORDER BY value DESC",
    },
    {
        "label": "Embedding count by source",
        "sql": "SELECT source, COUNT(*) AS cnt FROM read_parquet('s3://datalake/embeddings/**/*.parquet') GROUP BY source ORDER BY cnt DESC",
    },
]

EXAMPLE_QUESTIONS = [
    "What was our revenue trend over the last 30 days?",
    "Which Shopify products are selling the most this quarter?",
    "Are there any unusual spikes in Stripe refunds?",
    "What is the current state of our sales pipeline in HubSpot?",
]


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    stats = _get_bucket_stats()
    return templates.TemplateResponse(
        "home.html",
        {"request": request, "stats": stats, "page": "home"},
    )


@app.get("/catalog", response_class=HTMLResponse)
async def catalog(request: Request):
    data = _get_catalog_data()
    return templates.TemplateResponse(
        "catalog.html",
        {"request": request, "data": data, "page": "catalog"},
    )


@app.get("/storage", response_class=HTMLResponse)
async def storage(request: Request):
    stats = _get_bucket_stats()
    return templates.TemplateResponse(
        "storage.html",
        {
            "request": request,
            "stats": stats,
            "page": "storage",
            "minio_console_url": MINIO_CONSOLE_URL,
        },
    )


@app.get("/sql", response_class=HTMLResponse)
async def sql_get(request: Request):
    return templates.TemplateResponse(
        "sql.html",
        {
            "request": request,
            "page": "sql",
            "result": None,
            "sql": EXAMPLE_QUERIES[0]["sql"],
            "example_queries": EXAMPLE_QUERIES,
        },
    )


@app.post("/sql", response_class=HTMLResponse)
async def sql_post(request: Request, sql: str = Form(...)):
    result = _run_query(sql)
    return templates.TemplateResponse(
        "sql.html",
        {
            "request": request,
            "page": "sql",
            "result": result,
            "sql": sql,
            "example_queries": EXAMPLE_QUERIES,
        },
    )


@app.get("/dashboards", response_class=HTMLResponse)
async def dashboards(request: Request):
    return templates.TemplateResponse(
        "embed.html",
        {
            "request": request,
            "page": "dashboards",
            "embed_url": "/superset/",
            "embed_title": "Dashboards",
            "embed_service": "Apache Superset",
        },
    )


@app.get("/pipelines", response_class=HTMLResponse)
async def pipelines(request: Request):
    return templates.TemplateResponse(
        "embed.html",
        {
            "request": request,
            "page": "pipelines",
            "embed_url": "/airflow/",
            "embed_title": "Pipelines",
            "embed_service": "Apache Airflow",
        },
    )


@app.get("/notebooks", response_class=HTMLResponse)
async def notebooks(request: Request):
    return templates.TemplateResponse(
        "embed.html",
        {
            "request": request,
            "page": "notebooks",
            "embed_url": "/jupyter/",
            "embed_title": "Notebooks",
            "embed_service": "JupyterLab",
        },
    )


@app.get("/ingest", response_class=HTMLResponse)
async def ingest(request: Request):
    stats = _get_bucket_stats()
    return templates.TemplateResponse(
        "ingest.html",
        {"request": request, "stats": stats, "page": "ingest"},
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


@app.get("/anomalies", response_class=HTMLResponse)
async def anomalies(request: Request):
    return templates.TemplateResponse(
        "anomalies.html",
        {"request": request, "page": "anomalies"},
    )


@app.get("/health")
async def health():
    return {"status": "ok"}


# Legacy redirects
@app.get("/dashboard", response_class=RedirectResponse)
async def redirect_dashboard():
    return RedirectResponse(url="/", status_code=301)


@app.get("/browse", response_class=RedirectResponse)
async def redirect_browse():
    return RedirectResponse(url="/catalog", status_code=301)


@app.get("/query", response_class=RedirectResponse)
async def redirect_query():
    return RedirectResponse(url="/sql", status_code=301)
