"""Scientific corpus ingestion and retrieval helpers.

The corpus contract is source-agnostic: S2 Graph API search, S2 bulk datasets,
and future arXiv harvesters should all normalize into `CorpusPaper` before
writing to Lakebase. This keeps ingestion strategy separate from retrieval.
"""

from __future__ import annotations

import asyncio
from datetime import date
import hashlib
import json
import re
from dataclasses import dataclass, field
from typing import Any

import httpx
import psycopg
from psycopg.rows import dict_row

from agent_server.embeddings import embed_papers, embed_seed

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = ",".join([
    "paperId",
    "title",
    "abstract",
    "year",
    "url",
    "venue",
    "citationCount",
    "externalIds",
    "authors",
    "s2FieldsOfStudy",
    "references.paperId",
])

MIN_TITLE_CHARS = 8
MAX_TITLE_CHARS = 300
MIN_ABSTRACT_CHARS = 80
MIN_YEAR = 2014
MAX_YEAR = date.today().year + 1


@dataclass
class CorpusPaper:
    corpus_id: int
    title: str
    abstract: str = ""
    arxiv_id: str | None = None
    doi: str | None = None
    authors: list[dict[str, Any]] = field(default_factory=list)
    venue: str | None = None
    year: int | None = None
    fields: list[str] = field(default_factory=list)
    citation_count: int = 0
    references_ids: list[int] = field(default_factory=list)
    url: str | None = None
    source: str = "s2_graph"
    embedding: list[float] | None = None


def stable_corpus_id(value: str) -> int:
    """Return a deterministic signed BIGINT-safe ID."""
    digest = hashlib.sha256(value.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") & ((1 << 63) - 1)


def clean_text(value: str | None) -> str:
    """Normalize text before embedding/indexing."""
    if not value:
        return ""
    text = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", " ", value)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def title_key(title: str) -> str:
    """Stable key for duplicate paper titles across sources/IDs."""
    text = clean_text(title).lower()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _valid_year(year: int | None) -> bool:
    return year is None or MIN_YEAR <= year <= MAX_YEAR


def _is_quality_paper(title: str, abstract: str, year: int | None) -> bool:
    if not (MIN_TITLE_CHARS <= len(title) <= MAX_TITLE_CHARS):
        return False
    if len(abstract) < MIN_ABSTRACT_CHARS:
        return False
    if not _valid_year(year):
        return False
    lowered = title.lower()
    if lowered.startswith(("withdrawn:", "erratum:", "comment on")):
        return False
    return True


def _field_names(raw: list[dict[str, Any]] | None) -> list[str]:
    names: list[str] = []
    for item in raw or []:
        name = clean_text(item.get("category") or item.get("source") or item.get("name"))
        if name and name not in names:
            names.append(name)
    return names


def _normalize_s2_paper(raw: dict[str, Any]) -> CorpusPaper | None:
    title = clean_text(raw.get("title"))
    abstract = clean_text(raw.get("abstract"))
    year = raw.get("year")
    if not _is_quality_paper(title, abstract, year):
        return None

    external = raw.get("externalIds") or {}
    paper_key = raw.get("paperId") or external.get("ArXiv") or external.get("DOI") or title
    references = raw.get("references") or []
    authors = [
        {"author_id": a.get("authorId"), "name": a.get("name")}
        for a in (raw.get("authors") or [])[:20]
        if a.get("name")
    ]
    return CorpusPaper(
        corpus_id=stable_corpus_id(str(paper_key)),
        arxiv_id=external.get("ArXiv"),
        doi=external.get("DOI"),
        title=title,
        abstract=abstract,
        authors=authors,
        venue=clean_text(raw.get("venue")),
        year=year,
        fields=_field_names(raw.get("s2FieldsOfStudy")),
        citation_count=int(raw.get("citationCount") or 0),
        references_ids=[
            stable_corpus_id(r["paperId"])
            for r in references
            if isinstance(r, dict) and r.get("paperId")
        ],
        url=raw.get("url") or (f"https://arxiv.org/abs/{external['ArXiv']}" if external.get("ArXiv") else None),
    )


async def search_s2_papers(api_key: str, queries: list[str], limit_per_query: int = 25) -> list[CorpusPaper]:
    """Search Semantic Scholar and normalize papers for corpus insertion."""
    headers = {"x-api-key": api_key}
    papers: dict[int, CorpusPaper] = {}
    by_title: dict[str, int] = {}
    async with httpx.AsyncClient(timeout=30.0) as client:
        for query in queries:
            params = {"query": query, "limit": limit_per_query, "fields": S2_FIELDS}
            resp = None
            for attempt in range(5):
                resp = await client.get(S2_SEARCH_URL, params=params, headers=headers)
                if resp.status_code == 200:
                    break
                if resp.status_code not in {429, 500, 502, 503, 504}:
                    raise RuntimeError(
                        f"S2 search failed for {query!r}: HTTP {resp.status_code}: {resp.text[:200]}"
                    )
                retry_after = resp.headers.get("retry-after")
                delay = float(retry_after) if retry_after and retry_after.isdigit() else min(8.0 * (2 ** attempt), 60.0)
                await asyncio.sleep(delay)
            if resp is None or resp.status_code != 200:
                status = resp.status_code if resp is not None else "none"
                text = resp.text[:200] if resp is not None else ""
                raise RuntimeError(f"S2 search failed for {query!r} after retries: HTTP {status}: {text}")
            for raw in resp.json().get("data", []):
                paper = _normalize_s2_paper(raw)
                if paper:
                    key = title_key(paper.title)
                    existing_id = by_title.get(key)
                    if existing_id is None:
                        papers[paper.corpus_id] = paper
                        by_title[key] = paper.corpus_id
                    else:
                        existing = papers[existing_id]
                        if (paper.citation_count, len(paper.abstract)) > (
                            existing.citation_count,
                            len(existing.abstract),
                        ):
                            papers.pop(existing_id, None)
                            papers[paper.corpus_id] = paper
                            by_title[key] = paper.corpus_id
            await asyncio.sleep(3.0)
    return list(papers.values())


async def embed_corpus_papers(papers: list[CorpusPaper], batch_size: int = 16) -> list[CorpusPaper]:
    """Attach SPECTER2 proximity embeddings to papers."""
    for i in range(0, len(papers), batch_size):
        batch = papers[i:i + batch_size]
        vectors = await embed_papers([(p.title, p.abstract) for p in batch])
        for paper, vector in zip(batch, vectors):
            paper.embedding = vector
    return papers


def _vector_literal(vector: list[float] | None) -> str | None:
    if vector is None:
        return None
    return "[" + ",".join(f"{x:.8f}" for x in vector) + "]"


_TITLE_STOPWORDS = {
    "a", "an", "and", "are", "as", "for", "from", "in", "is", "of", "on", "the", "to", "with", "you"
}


def _retrieval_tokens(text: str) -> set[str]:
    return {
        t
        for t in re.findall(r"[a-z0-9]+", text.lower())
        if len(t) > 1 and t not in _TITLE_STOPWORDS
    }


def _title_overlap_score(query: str, title: str) -> float:
    query_tokens = _retrieval_tokens(query)
    title_tokens = _retrieval_tokens(title)
    if not query_tokens or not title_tokens:
        return 0.0
    return len(query_tokens & title_tokens) / len(title_tokens)


def upsert_corpus_papers(conn: psycopg.Connection, schema: str, papers: list[CorpusPaper]) -> int:
    """Upsert normalized corpus papers into Lakebase."""
    if not papers:
        return 0
    rows = [
        (
            p.corpus_id,
            p.arxiv_id,
            p.doi,
            p.title,
            p.abstract,
            json.dumps(p.authors),
            p.venue,
            p.year,
            p.fields,
            p.citation_count,
            p.references_ids,
            p.url,
            p.source,
            _vector_literal(p.embedding),
        )
        for p in papers
    ]
    with conn.cursor() as cur:
        cur.executemany(
            f"""
            INSERT INTO {schema}.corpus_papers
                (corpus_id, arxiv_id, doi, title, abstract, authors, venue, year,
                 fields, citation_count, references_ids, url, source, embedding, tsv)
            VALUES
                (%s, %s, %s, %s, %s, %s::jsonb, %s, %s,
                 %s, %s, %s, %s, %s, %s::vector,
                 setweight(to_tsvector('english', coalesce(%s, '')), 'A') ||
                 setweight(to_tsvector('english', coalesce(%s, '')), 'B'))
            ON CONFLICT (corpus_id) DO UPDATE SET
                arxiv_id = EXCLUDED.arxiv_id,
                doi = EXCLUDED.doi,
                title = EXCLUDED.title,
                abstract = EXCLUDED.abstract,
                authors = EXCLUDED.authors,
                venue = EXCLUDED.venue,
                year = EXCLUDED.year,
                fields = EXCLUDED.fields,
                citation_count = EXCLUDED.citation_count,
                references_ids = EXCLUDED.references_ids,
                url = EXCLUDED.url,
                source = EXCLUDED.source,
                embedding = EXCLUDED.embedding,
                tsv = EXCLUDED.tsv,
                ingested_at = NOW()
            """,
            [row + (row[3], row[4]) for row in rows],
        )
    return len(rows)


def clean_existing_corpus(conn: psycopg.Connection, schema: str) -> dict[str, int]:
    """Normalize and remove low-quality/duplicate corpus rows already in Lakebase."""
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            f"""
            SELECT corpus_id, title, abstract, year, citation_count,
                   embedding IS NOT NULL AS has_embedding
            FROM {schema}.corpus_papers
            """
        ).fetchall()

    normalized_updates: list[tuple[str, str, int]] = []
    delete_ids: set[int] = set()
    best_by_title: dict[str, dict[str, Any]] = {}

    for row in rows:
        corpus_id = int(row["corpus_id"])
        title = clean_text(row["title"])
        abstract = clean_text(row["abstract"])
        normalized_updates.append((title, abstract, corpus_id))

        if not row["has_embedding"] or not _is_quality_paper(title, abstract, row["year"]):
            delete_ids.add(corpus_id)
            continue

        key = title_key(title)
        existing = best_by_title.get(key)
        if existing is None:
            best_by_title[key] = {
                "corpus_id": corpus_id,
                "citation_count": row["citation_count"] or 0,
                "abstract_len": len(abstract),
            }
            continue

        current_score = (row["citation_count"] or 0, len(abstract))
        existing_score = (existing["citation_count"] or 0, existing["abstract_len"] or 0)
        if current_score > existing_score:
            delete_ids.add(existing["corpus_id"])
            best_by_title[key] = {
                "corpus_id": corpus_id,
                "citation_count": row["citation_count"] or 0,
                "abstract_len": len(abstract),
            }
        else:
            delete_ids.add(corpus_id)

    with conn.cursor() as cur:
        cur.executemany(
            f"""
            UPDATE {schema}.corpus_papers
            SET title = %s,
                abstract = %s,
                tsv = setweight(to_tsvector('english', coalesce(%s, '')), 'A') ||
                      setweight(to_tsvector('english', coalesce(%s, '')), 'B')
            WHERE corpus_id = %s
            """,
            [(title, abstract, title, abstract, corpus_id) for title, abstract, corpus_id in normalized_updates],
        )
        if delete_ids:
            cur.execute(
                f"DELETE FROM {schema}.corpus_papers WHERE corpus_id = ANY(%s)",
                (list(delete_ids),),
            )

    return {
        "normalized": len(normalized_updates),
        "deleted": len(delete_ids),
        "remaining": len(rows) - len(delete_ids),
    }


async def hybrid_search(conn: psycopg.Connection, schema: str, query: str, limit: int = 8) -> list[dict[str, Any]]:
    """Retrieve grounded corpus context using vector + BM25 signals.

    The query uses exact vector distance over the current corpus table. Lakebase
    ANN/BM25 indexes are still maintained for production-scale retrieval, and
    this helper keeps the result contract stable while index-specific query
    syntax evolves.
    """
    vector = _vector_literal(await embed_seed(query))
    candidate_limit = max(limit * 5, 50)
    with conn.cursor(row_factory=dict_row) as cur:
        rows = cur.execute(
            f"""
            SELECT corpus_id, title, abstract, year, url, source, citation_count,
                   1 - (embedding <=> %s::vector) AS vector_score,
                   ts_rank_cd(tsv, plainto_tsquery('english', %s)) AS text_score,
                   LEAST(1.0, LN(1 + citation_count)::float / 12.0) AS citation_score,
                   (
                    (0.65 * (1 - (embedding <=> %s::vector))) +
                    (0.20 * ts_rank_cd(tsv, plainto_tsquery('english', %s))) +
                    (
                        0.15 *
                        LEAST(1.0, LN(1 + citation_count)::float / 12.0) *
                        LEAST(1.0, 10 * ts_rank_cd(tsv, plainto_tsquery('english', %s)))
                    )
                   ) AS base_score
            FROM {schema}.corpus_papers
            WHERE embedding IS NOT NULL
            ORDER BY base_score DESC
            LIMIT %s
            """,
            (vector, query, vector, query, query, candidate_limit),
        ).fetchall()
    out = [dict(r) for r in rows]
    for row in out:
        row["title_overlap_score"] = _title_overlap_score(query, row["title"])
        row["hybrid_score"] = float(row.get("base_score") or 0.0) + 0.20 * row["title_overlap_score"]
    out.sort(key=lambda r: r["hybrid_score"], reverse=True)
    return out[:limit]

