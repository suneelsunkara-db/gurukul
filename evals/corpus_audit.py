"""Corpus quality and coverage audit.

This is the pre-deploy data gate for grounded retrieval. It checks:
- corpus size and basic field completeness
- duplicate normalized titles
- missing/short abstracts and embeddings
- year/source/field distributions
- golden query retrieval coverage
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from collections import Counter
from pathlib import Path
from typing import Any

import psycopg
from databricks.sdk import WorkspaceClient
from dotenv import load_dotenv
from psycopg.rows import dict_row

from agent_server.corpus import hybrid_search, title_key


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


def _matches_any(text: str, needles: list[str], mode: str = "contains") -> bool:
    hay = title_key(text)
    if mode == "exact":
        return any(title_key(n) == hay for n in needles)
    return any(title_key(n) in hay for n in needles)


def _summarize_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    title_counts = Counter(title_key(r["title"]) for r in rows)
    duplicate_titles = {k: v for k, v in title_counts.items() if k and v > 1}
    years = Counter(str(r["year"] or "unknown") for r in rows)
    sources = Counter(r["source"] or "unknown" for r in rows)
    fields = Counter()
    for r in rows:
        for field in r.get("fields") or []:
            fields[field] += 1
    return {
        "row_count": len(rows),
        "missing_abstract": sum(1 for r in rows if not r.get("abstract")),
        "short_abstract": sum(1 for r in rows if len(r.get("abstract") or "") < 80),
        "missing_embedding": sum(1 for r in rows if not r.get("has_embedding")),
        "missing_year": sum(1 for r in rows if r.get("year") is None),
        "duplicate_title_groups": len(duplicate_titles),
        "duplicate_title_examples": sorted(duplicate_titles.items(), key=lambda x: x[1], reverse=True)[:10],
        "year_top": years.most_common(12),
        "source_top": sources.most_common(8),
        "field_top": fields.most_common(12),
    }


async def _golden_coverage(conn: psycopg.Connection, schema: str, cases: list[dict[str, Any]], k: int) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    for case in cases:
        hits = await hybrid_search(conn, schema, case["query"], limit=k)
        titles = [h["title"] for h in hits]
        matched = any(_matches_any(title, case["must_match"], case.get("match_mode", "contains")) for title in titles)
        results.append({
            "query": case["query"],
            "matched": matched,
            "must_match": case["must_match"],
            "top_titles": titles[:5],
        })
    return results


async def _run(args: argparse.Namespace) -> int:
    load_dotenv(args.env, override=True)
    schema = os.getenv("GURUKUL_DB_SCHEMA", "gurukul")
    cases = json.loads(Path(args.cases).read_text())
    failures: list[str] = []

    with psycopg.connect(_conninfo(), password=_password(), autocommit=True) as conn:
        with conn.cursor(row_factory=dict_row) as cur:
            rows = cur.execute(
                f"""
                SELECT corpus_id, title, abstract, year, source, fields,
                       embedding IS NOT NULL AS has_embedding
                FROM {schema}.corpus_papers
                """
            ).fetchall()
        row_dicts = [dict(r) for r in rows]
        summary = _summarize_rows(row_dicts)
        golden = await _golden_coverage(conn, schema, cases, args.top_k)

    if summary["row_count"] < args.min_rows:
        failures.append(f"row_count {summary['row_count']} < min_rows {args.min_rows}")
    if summary["missing_embedding"] > 0:
        failures.append(f"missing_embedding={summary['missing_embedding']}")
    if summary["short_abstract"] > args.max_short_abstracts:
        failures.append(f"short_abstract={summary['short_abstract']} > {args.max_short_abstracts}")
    if summary["duplicate_title_groups"] > args.max_duplicate_groups:
        failures.append(f"duplicate_title_groups={summary['duplicate_title_groups']} > {args.max_duplicate_groups}")

    missed = [g for g in golden if not g["matched"]]
    if missed:
        failures.append(f"golden_misses={len(missed)}/{len(golden)}")

    report = {
        "summary": summary,
        "golden": golden,
        "failures": failures,
    }
    print(json.dumps(report, indent=2))

    if failures:
        print("\nCorpus audit failed")
        return 1
    print("\nCorpus audit passed")
    return 0


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--env", default=".env")
    ap.add_argument("--cases", default="evals/corpus_golden_cases.json")
    ap.add_argument("--top-k", type=int, default=10)
    ap.add_argument("--min-rows", type=int, default=500)
    ap.add_argument("--max-short-abstracts", type=int, default=0)
    ap.add_argument("--max-duplicate-groups", type=int, default=0)
    args = ap.parse_args()
    raise SystemExit(asyncio.run(_run(args)))


if __name__ == "__main__":
    main()

