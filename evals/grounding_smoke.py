"""End-to-end smoke test for grounded retrieval.

This verifies the production grounding path, not just isolated helpers:
- S2 key is loaded
- Semantic Scholar responds
- Lakebase corpus has rows
- hybrid search returns relevant corpus evidence
- seed resolver emits typed source labels including Lakebase corpus evidence
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from typing import Any

import psycopg
from databricks.sdk import WorkspaceClient
from dotenv import load_dotenv

from agent_server.corpus import hybrid_search, search_s2_papers
from agent_server.grounding import format_grounding_context, resolve_seed


def _conninfo() -> str:
    host = os.getenv("PGHOST", "")
    user = os.getenv("PGUSER", "")
    database = os.getenv("PGDATABASE", "databricks_postgres")
    if not host or not user:
        raise RuntimeError("PGHOST and PGUSER are required")
    return f"host={host} port=5432 dbname={database} user={user} sslmode=require"


def _password() -> str:
    endpoint = os.getenv("ENDPOINT_NAME", "")
    if not endpoint:
        raise RuntimeError("ENDPOINT_NAME is required")
    return WorkspaceClient().postgres.generate_database_credential(endpoint=endpoint).token


def _sources(resolution: dict[str, Any]) -> set[str]:
    out: set[str] = set()
    for part in resolution.get("parts", []):
        for c in (part.get("evidence") or {}).get("top_candidates", []):
            if c.get("source"):
                out.add(c["source"])
    return out


async def _run(args: argparse.Namespace) -> int:
    load_dotenv(args.env, override=True)
    schema = os.getenv("GURUKUL_DB_SCHEMA", "gurukul")
    failures: list[str] = []
    warnings: list[str] = []

    s2_key = os.getenv("S2_API_KEY", "")
    if not s2_key:
        failures.append("S2_API_KEY is empty")
    else:
        try:
            s2 = await search_s2_papers(s2_key, [args.query], limit_per_query=1)
            if not s2:
                failures.append("Semantic Scholar returned no papers")
        except RuntimeError as e:
            if args.require_live_s2:
                failures.append(str(e))
            else:
                warnings.append(f"Live Semantic Scholar probe skipped: {e}")

    with psycopg.connect(_conninfo(), password=_password(), autocommit=True) as conn:
        corpus_rows = conn.execute(f"SELECT COUNT(*) FROM {schema}.corpus_papers").fetchone()[0]
        if corpus_rows <= 0:
            failures.append("Lakebase corpus is empty")
        hits = await hybrid_search(conn, schema, args.query, limit=args.limit)
        if not hits:
            failures.append("Hybrid search returned no corpus hits")

    resolution = await resolve_seed(args.query)
    sources = _sources(resolution)
    if "lakebase_corpus" not in sources:
        failures.append(f"Resolver did not include lakebase_corpus evidence; sources={sorted(sources)}")

    context = format_grounding_context(resolution)
    for label in ("lakebase_corpus", "arxiv", "semantic_scholar"):
        if label in sources and label not in context:
            failures.append(f"Grounding context omitted source label {label}")

    print(json.dumps({
        "query": args.query,
        "corpus_rows": corpus_rows,
        "hybrid_top": [h["title"] for h in hits[:3]],
        "resolver_type": resolution.get("resolved_type"),
        "resolver_sources": sorted(sources),
        "grounding_context_preview": context.splitlines()[:12],
        "warnings": warnings,
    }, indent=2))

    if failures:
        print("\nGrounding smoke failures:")
        for failure in failures:
            print(f"- {failure}")
        return 1

    print("\nGrounding smoke passed")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=".env")
    ap.add_argument("--query", default="reinforcement learning from human feedback")
    ap.add_argument("--limit", type=int, default=5)
    ap.add_argument("--require-live-s2", action="store_true")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()

