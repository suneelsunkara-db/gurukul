"""Serverless Databricks Job: corpus build / embedding / indexing.

Long-running corpus work runs remotely as a serverless Job, writes progress to
Lakebase, and owns index creation/repair inside the workspace. The job reads
S2_API_KEY from Databricks Secrets inside the task instead of receiving it as a
parameter, so secrets do not appear in job settings or run history.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Awaitable, TypeVar

import httpx
import psycopg

T = TypeVar("T")


def _run_async(coro: Awaitable[T]) -> T:
    """Run async helpers from Databricks notebook-backed job execution.

    Serverless Jobs execute Workspace Python files through an IPython kernel,
    which may already have an event loop. In that case, run the coroutine in a
    short-lived worker thread with its own loop.
    """
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)

    with ThreadPoolExecutor(max_workers=1) as executor:
        return executor.submit(lambda: asyncio.run(coro)).result()


def _set_env(args: argparse.Namespace) -> None:
    os.environ["PGHOST"] = args.pg_host
    os.environ["PGUSER"] = args.pg_user
    os.environ["PGDATABASE"] = args.pg_database
    os.environ["ENDPOINT_NAME"] = args.lakebase_endpoint
    os.environ["GURUKUL_DB_SCHEMA"] = args.db_schema
    os.environ["S2_SECRET_SCOPE"] = args.s2_secret_scope
    os.environ["S2_SECRET_KEY"] = args.s2_secret_key
    os.environ["EMBEDDING_MODEL"] = args.embedding_model


def _load_s2_api_key(scope: str, key: str) -> str:
    """Load S2 key from env or Databricks Secrets.

    The environment path is for local dry-runs/tests. Serverless Databricks Jobs
    should use dbutils-backed secret lookup.
    """
    if os.getenv("S2_API_KEY"):
        return os.environ["S2_API_KEY"]

    try:
        from pyspark.dbutils import DBUtils
        from pyspark.sql import SparkSession

        value = DBUtils(SparkSession.builder.getOrCreate()).secrets.get(scope, key)
        if value:
            os.environ["S2_API_KEY"] = value
            return value
    except Exception as e:
        raise RuntimeError(
            f"Could not load Semantic Scholar key from Databricks Secrets "
            f"{scope}/{key}: {type(e).__name__}: {e}"
        ) from e

    raise RuntimeError(f"Databricks secret {scope}/{key} was empty")


def _probe_s2(api_key: str, query: str) -> dict:
    url = "https://api.semanticscholar.org/graph/v1/paper/search"
    params = {"query": query, "limit": 1, "fields": "title,year,externalIds"}
    headers = {"x-api-key": api_key}
    with httpx.Client(timeout=20.0) as client:
        resp = None
        for attempt in range(5):
            resp = client.get(url, params=params, headers=headers)
            if resp.status_code == 200:
                break
            if resp.status_code not in {429, 500, 502, 503, 504}:
                raise RuntimeError(f"S2 probe failed with HTTP {resp.status_code}: {resp.text[:200]}")
            retry_after = resp.headers.get("retry-after")
            delay = float(retry_after) if retry_after and retry_after.isdigit() else min(30.0 * (2 ** attempt), 180.0)
            time.sleep(delay)
    if resp is None:
        raise RuntimeError("S2 probe failed without a response")
    if resp.status_code != 200:
        raise RuntimeError(f"S2 probe failed after retries with HTTP {resp.status_code}: {resp.text[:200]}")
    data = resp.json()
    first = (data.get("data") or [{}])[0]
    return {
        "query": query,
        "total": data.get("total"),
        "first_title": first.get("title"),
        "first_year": first.get("year"),
        "first_arxiv": (first.get("externalIds") or {}).get("ArXiv"),
    }


def _queries(raw: str) -> list[str]:
    return [q.strip() for q in raw.split(";") if q.strip()]


def _db_password() -> str:
    from databricks.sdk import WorkspaceClient

    return WorkspaceClient().postgres.generate_database_credential(
        endpoint=os.environ["ENDPOINT_NAME"]
    ).token


def _conninfo() -> str:
    return (
        f"host={os.environ['PGHOST']} port=5432 dbname={os.environ['PGDATABASE']} "
        f"user={os.environ['PGUSER']} sslmode=require"
    )


def _ensure_indexes(schema: str) -> dict:
    """Create/repair Lakebase Search indexes after corpus rows are loaded."""
    with psycopg.connect(_conninfo(), password=_db_password(), autocommit=True) as conn:
        count = conn.execute(f"SELECT COUNT(*) FROM {schema}.corpus_papers").fetchone()[0]
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS corpus_ann "
            f"ON {schema}.corpus_papers USING lakebase_ann (embedding vector_cosine_ops)"
        )
        conn.execute(
            f"CREATE INDEX IF NOT EXISTS corpus_bm25 "
            f"ON {schema}.corpus_papers USING lakebase_bm25 (tsv)"
        )
    return {"corpus_rows": count, "indexes": ["corpus_ann", "corpus_bm25"]}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--pg-host", required=True)
    ap.add_argument("--pg-user", required=True)
    ap.add_argument("--pg-database", default="databricks_postgres")
    ap.add_argument("--lakebase-endpoint", required=True)
    ap.add_argument("--db-schema", default="gurukul")
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--s2-release", default="latest")
    ap.add_argument("--s2-secret-scope", default="gurukul")
    ap.add_argument("--s2-secret-key", default="s2_api_key")
    ap.add_argument("--embedding-model", default="gurukul-specter2-embed")
    ap.add_argument("--probe-query", default="reinforcement learning from human feedback")
    ap.add_argument(
        "--queries",
        default="attention is all you need transformer;large language model architecture;instruction tuning large language models;reinforcement learning from human feedback;direct preference optimization language models;constitutional AI harmlessness;retrieval augmented generation;LLM agents reasoning acting;Toolformer language models tools;ReAct reasoning acting language models;mixture of experts large language models;Mixtral mixture of experts;low rank adaptation LoRA large language models;FlashAttention efficient attention;KV cache optimization large language models;speculative decoding language models;quantization large language models;long context language models;chain of thought reasoning language models;LLM evaluation benchmarks;LLM safety alignment jailbreak;multimodal large language models;Llama technical report;Qwen technical report;DeepSeek large language model technical report",
    )
    ap.add_argument("--limit-per-query", type=int, default=40)
    ap.add_argument("--embedding-batch-size", type=int, default=16)
    args = ap.parse_args()

    if args.source_root not in sys.path:
        sys.path.insert(0, args.source_root)

    from jobs.progress import update_progress
    from agent_server.corpus import (
        clean_existing_corpus,
        embed_corpus_papers,
        search_s2_papers,
        upsert_corpus_papers,
    )

    _set_env(args)
    try:
        update_progress(
            args.run_id,
            "corpus_build",
            "running",
            "preflight",
            5,
            {
                "s2_release": args.s2_release,
                "s2_secret_scope": args.s2_secret_scope,
                "s2_secret_key": args.s2_secret_key,
            },
        )

        update_progress(args.run_id, "corpus_build", "running", "s2_preflight", 15)
        s2_key = _load_s2_api_key(args.s2_secret_scope, args.s2_secret_key)
        s2_probe = _probe_s2(s2_key, args.probe_query)
        update_progress(args.run_id, "corpus_build", "running", "s2_ready", 25, s2_probe)

        queries = _queries(args.queries)
        update_progress(
            args.run_id,
            "corpus_build",
            "running",
            "s2_search",
            35,
            {"queries": queries, "limit_per_query": args.limit_per_query},
        )
        papers = _run_async(search_s2_papers(s2_key, queries, limit_per_query=args.limit_per_query))

        update_progress(
            args.run_id,
            "corpus_build",
            "running",
            "embed_papers",
            55,
            {"paper_count": len(papers), "embedding_batch_size": args.embedding_batch_size},
        )
        papers = _run_async(embed_corpus_papers(papers, batch_size=args.embedding_batch_size))

        update_progress(args.run_id, "corpus_build", "running", "upsert_lakebase", 70, {"paper_count": len(papers)})
        with psycopg.connect(_conninfo(), password=_db_password(), autocommit=True) as conn:
            upserted = upsert_corpus_papers(conn, args.db_schema, papers)
            update_progress(args.run_id, "corpus_build", "running", "clean_corpus", 78)
            cleanup = clean_existing_corpus(conn, args.db_schema)

        update_progress(args.run_id, "corpus_build", "running", "repair_indexes", 80)
        detail = _ensure_indexes(args.db_schema)
        detail["upserted"] = upserted
        detail["cleanup"] = cleanup
        detail["queries"] = queries
        update_progress(args.run_id, "corpus_build", "succeeded", "ready", 100, detail)
    except Exception as e:
        update_progress(
            args.run_id,
            "corpus_build",
            "failed",
            "failed",
            100,
            {"s2_release": args.s2_release},
            error=f"{type(e).__name__}: {e}",
        )
        raise


if __name__ == "__main__":
    main()

