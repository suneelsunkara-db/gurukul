"""arXiv API service for reference verification, content enrichment, and literature discovery.

Uses the arXiv REST API (https://export.arxiv.org/api/query) which is free,
requires no authentication, and returns Atom XML feeds.

Rate limiting: arXiv asks for max 1 request per 3 seconds.
We use an async semaphore + sleep to respect this.

Performance optimizations:
  - Shared httpx client with connection pooling
  - In-memory TTL cache for search results and verifications
  - Batch ID verification via id_list parameter
"""

from __future__ import annotations

import asyncio
import logging
import re
import time
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Optional

import httpx

logger = logging.getLogger(__name__)

ARXIV_API = "https://export.arxiv.org/api/query"
ATOM_NS = "{http://www.w3.org/2005/Atom}"
ARXIV_NS = "{http://arxiv.org/schemas/atom}"

_rate_sem = asyncio.Semaphore(1)
_last_request: float = 0.0

_client: httpx.AsyncClient | None = None
_cache: dict[str, tuple[float, object]] = {}
CACHE_TTL = 3600  # 1 hour


def _get_client() -> httpx.AsyncClient:
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(timeout=15.0, follow_redirects=True)
    return _client


def _cache_get(key: str) -> object | None:
    entry = _cache.get(key)
    if entry and (time.monotonic() - entry[0]) < CACHE_TTL:
        return entry[1]
    if entry:
        del _cache[key]
    return None


def _cache_set(key: str, value: object) -> None:
    if len(_cache) > 500:
        cutoff = time.monotonic() - CACHE_TTL
        expired = [k for k, (t, _) in _cache.items() if t < cutoff]
        for k in expired:
            del _cache[k]
    _cache[key] = (time.monotonic(), value)


@dataclass
class ArxivPaper:
    arxiv_id: str
    title: str
    authors: list[str]
    summary: str
    published: str
    updated: str
    categories: list[str]
    pdf_url: str
    abs_url: str
    primary_category: str = ""
    year: int = 0

    def __post_init__(self):
        if not self.year and self.published:
            try:
                self.year = int(self.published[:4])
            except (ValueError, IndexError):
                pass

    def to_reference(self) -> dict:
        return {
            "id": self.arxiv_id.replace(".", "-"),
            "title": self.title,
            "authors": "; ".join(self.authors[:5]) + (
                f" +{len(self.authors) - 5} more" if len(self.authors) > 5 else ""
            ),
            "year": self.year,
            "arxiv": self.arxiv_id,
            "verified": True,
        }


@dataclass
class VerificationResult:
    claimed_title: str
    claimed_arxiv_id: str | None
    found: bool
    paper: ArxivPaper | None = None
    title_match: float = 0.0
    issues: list[str] = field(default_factory=list)


@dataclass
class CitationContext:
    verified_refs: list[VerificationResult]
    invalid_refs: list[VerificationResult]
    missing_important: list[ArxivPaper]
    recent_papers: list[ArxivPaper]
    verification_rate: float = 0.0
    freshness_score: float = 0.0


async def _rate_limited_request(url: str, params: dict) -> str | None:
    global _last_request

    async with _rate_sem:
        now = time.monotonic()
        wait = max(0, 3.0 - (now - _last_request))
        if wait > 0:
            await asyncio.sleep(wait)

        try:
            client = _get_client()
            resp = await client.get(url, params=params)
            _last_request = time.monotonic()
            if resp.status_code == 200:
                return resp.text
            logger.warning("arXiv API returned %d", resp.status_code)
            return None
        except Exception as e:
            logger.warning("arXiv API request failed: %s", e)
            return None


def _parse_feed(xml_text: str) -> list[ArxivPaper]:
    papers = []
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as e:
        logger.warning("Failed to parse arXiv XML: %s", e)
        return []

    for entry in root.findall(f"{ATOM_NS}entry"):
        arxiv_id_raw = (entry.findtext(f"{ATOM_NS}id") or "").strip()
        arxiv_id = arxiv_id_raw.split("/abs/")[-1] if "/abs/" in arxiv_id_raw else arxiv_id_raw
        arxiv_id = re.sub(r"v\d+$", "", arxiv_id)

        title = (entry.findtext(f"{ATOM_NS}title") or "").strip()
        title = re.sub(r"\s+", " ", title)

        authors = []
        for author_el in entry.findall(f"{ATOM_NS}author"):
            name = (author_el.findtext(f"{ATOM_NS}name") or "").strip()
            if name:
                authors.append(name)

        summary = (entry.findtext(f"{ATOM_NS}summary") or "").strip()
        summary = re.sub(r"\s+", " ", summary)

        published = (entry.findtext(f"{ATOM_NS}published") or "")[:10]
        updated = (entry.findtext(f"{ATOM_NS}updated") or "")[:10]

        categories = []
        for cat_el in entry.findall(f"{ATOM_NS}category"):
            term = cat_el.get("term", "")
            if term:
                categories.append(term)

        primary_cat = ""
        prim_el = entry.find(f"{ARXIV_NS}primary_category")
        if prim_el is not None:
            primary_cat = prim_el.get("term", "")

        pdf_url = ""
        abs_url = ""
        for link_el in entry.findall(f"{ATOM_NS}link"):
            href = link_el.get("href", "")
            link_type = link_el.get("type", "")
            if link_type == "application/pdf":
                pdf_url = href
            elif link_el.get("rel") == "alternate":
                abs_url = href

        if not arxiv_id or arxiv_id.startswith("http"):
            continue

        papers.append(ArxivPaper(
            arxiv_id=arxiv_id,
            title=title,
            authors=authors,
            summary=summary[:500],
            published=published,
            updated=updated,
            categories=categories,
            primary_category=primary_cat,
            pdf_url=pdf_url,
            abs_url=abs_url or f"https://arxiv.org/abs/{arxiv_id}",
        ))

    return papers


def _title_similarity(a: str, b: str) -> float:
    wa = set(a.lower().split())
    wb = set(b.lower().split())
    stopwords = {"a", "an", "the", "of", "for", "in", "on", "to", "and", "with", "by", "is", "are"}
    wa -= stopwords
    wb -= stopwords
    if not wa or not wb:
        return 0.0
    overlap = len(wa & wb)
    return overlap / max(len(wa), len(wb))


async def search(query: str, max_results: int = 10, sort_by: str = "relevance",
                 date_range: tuple[str, str] | None = None) -> list[ArxivPaper]:
    """Search arXiv for papers matching a query."""
    cache_key = f"search:{query}:{max_results}:{sort_by}:{date_range}"
    cached = _cache_get(cache_key)
    if cached is not None:
        return cached  # type: ignore

    words = query.strip().split()
    search_query = " AND ".join(f"all:{w}" for w in words[:8])

    if date_range:
        start, end = date_range
        search_query += f" AND submittedDate:[{start} TO {end}]"

    sort_map = {
        "relevance": "relevance",
        "date": "lastUpdatedDate",
        "submitted": "submittedDate",
    }

    params = {
        "search_query": search_query,
        "start": 0,
        "max_results": min(max_results, 30),
        "sortBy": sort_map.get(sort_by, "relevance"),
        "sortOrder": "descending",
    }

    xml = await _rate_limited_request(ARXIV_API, params)
    if not xml:
        return []

    results = _parse_feed(xml)
    _cache_set(cache_key, results)
    return results


async def verify_batch(arxiv_ids: list[str]) -> dict[str, ArxivPaper | None]:
    """Verify multiple papers in a single arXiv API call using id_list."""
    clean_ids = []
    for aid in arxiv_ids:
        clean = re.sub(r"v\d+$", "", aid.strip())
        if re.match(r"^\d{4}\.\d{4,5}$", clean):
            clean_ids.append(clean)

    if not clean_ids:
        return {}

    uncached = []
    result: dict[str, ArxivPaper | None] = {}
    for cid in clean_ids:
        cached = _cache_get(f"verify:{cid}")
        if cached is not None:
            result[cid] = cached  # type: ignore
        else:
            uncached.append(cid)

    if uncached:
        id_list = ",".join(uncached)
        params = {"id_list": id_list, "max_results": len(uncached)}
        xml = await _rate_limited_request(ARXIV_API, params)
        papers = _parse_feed(xml) if xml else []

        found_ids = set()
        for p in papers:
            result[p.arxiv_id] = p
            _cache_set(f"verify:{p.arxiv_id}", p)
            found_ids.add(p.arxiv_id)

        for uid in uncached:
            if uid not in found_ids:
                result[uid] = None
                _cache_set(f"verify:{uid}", None)

    return result


async def verify(arxiv_id: str) -> ArxivPaper | None:
    """Verify a specific paper exists on arXiv by its ID."""
    results = await verify_batch([arxiv_id])
    return results.get(re.sub(r"v\d+$", "", arxiv_id.strip()))


async def verify_reference(claimed_title: str, claimed_arxiv_id: str | None) -> VerificationResult:
    """Verify a reference — first by ID, then by title search."""
    result = VerificationResult(
        claimed_title=claimed_title,
        claimed_arxiv_id=claimed_arxiv_id,
        found=False,
    )

    if claimed_arxiv_id:
        paper = await verify(claimed_arxiv_id)
        if paper:
            result.found = True
            result.paper = paper
            result.title_match = _title_similarity(claimed_title, paper.title)
            if result.title_match < 0.3:
                result.issues.append(
                    f"arXiv ID exists but title doesn't match. "
                    f"Claimed: '{claimed_title[:50]}', Actual: '{paper.title[:50]}'"
                )
            return result
        else:
            result.issues.append(f"arXiv ID {claimed_arxiv_id} does not exist")

    if claimed_title and len(claimed_title) > 15:
        title_words = " ".join(claimed_title.split()[:8])
        papers = await search(title_words, max_results=3)
        for p in papers:
            sim = _title_similarity(claimed_title, p.title)
            if sim >= 0.5:
                result.found = True
                result.paper = p
                result.title_match = sim
                if claimed_arxiv_id:
                    result.issues.append(
                        f"Wrong arXiv ID ({claimed_arxiv_id}), "
                        f"correct ID is {p.arxiv_id}"
                    )
                return result

        if not result.found:
            result.issues.append(
                f"Could not find '{claimed_title[:50]}' on arXiv. "
                f"Paper may not exist or may be published elsewhere."
            )

    return result


async def verify_references_batch(refs: list[dict]) -> list[VerificationResult]:
    """Verify multiple references efficiently — batch ID lookups first, then title search."""
    ids_to_verify = []
    for ref in refs:
        aid = ref.get("arxiv")
        if aid and re.match(r"^\d{4}\.\d{4,5}(v\d+)?$", str(aid).strip()):
            ids_to_verify.append(str(aid).strip())

    batch_results = await verify_batch(ids_to_verify) if ids_to_verify else {}

    results: list[VerificationResult] = []
    for ref in refs:
        title = ref.get("title", "")
        aid = ref.get("arxiv")
        clean_aid = re.sub(r"v\d+$", "", str(aid).strip()) if aid else None

        vr = VerificationResult(claimed_title=title, claimed_arxiv_id=str(aid) if aid else None, found=False)

        if clean_aid and clean_aid in batch_results:
            paper = batch_results[clean_aid]
            if paper:
                vr.found = True
                vr.paper = paper
                vr.title_match = _title_similarity(title, paper.title)
                if vr.title_match < 0.3:
                    vr.issues.append(
                        f"arXiv ID exists but title mismatch. "
                        f"Claimed: '{title[:50]}', Actual: '{paper.title[:50]}'"
                    )
            else:
                vr.issues.append(f"arXiv ID {aid} does not exist")
                if title and len(title) > 15:
                    title_words = " ".join(title.split()[:8])
                    papers = await search(title_words, max_results=3)
                    for p in papers:
                        sim = _title_similarity(title, p.title)
                        if sim >= 0.5:
                            vr.found = True
                            vr.paper = p
                            vr.title_match = sim
                            vr.issues.append(f"Wrong ID ({aid}), correct: {p.arxiv_id}")
                            break
        elif title and len(title) > 15:
            title_words = " ".join(title.split()[:8])
            papers = await search(title_words, max_results=3)
            for p in papers:
                sim = _title_similarity(title, p.title)
                if sim >= 0.5:
                    vr.found = True
                    vr.paper = p
                    vr.title_match = sim
                    break
            if not vr.found:
                vr.issues.append(f"Could not find '{title[:50]}' on arXiv.")

        results.append(vr)

    return results


async def find_related(title: str, summary: str, max_results: int = 8) -> list[ArxivPaper]:
    """Find real papers related to a topic using title + summary keywords."""
    query = title
    if summary:
        important_words = [w for w in summary.split()[:80]
                          if len(w) > 4 and w.isalpha()]
        if important_words:
            query = f"{title} {' '.join(important_words[:4])}"
    return await search(query, max_results=max_results, sort_by="relevance")


async def get_recent(topic: str, months_back: int = 12, max_results: int = 10) -> list[ArxivPaper]:
    """Get recent papers on a topic from the last N months."""
    now = datetime.now(timezone.utc)
    start = (now - timedelta(days=months_back * 30)).strftime("%Y%m%d0000")
    end = now.strftime("%Y%m%d2359")

    return await search(
        topic,
        max_results=max_results,
        sort_by="submitted",
        date_range=(start, end),
    )


async def build_citation_context(
    topic_title: str,
    topic_summary: str,
    claimed_references: list[dict],
    months_back: int = 12,
) -> CitationContext:
    """Build a complete citation context for a topic."""
    verification_results = await verify_references_batch(claimed_references)

    verified = [v for v in verification_results if v.found]
    invalid = [v for v in verification_results if not v.found]

    related = await find_related(topic_title, topic_summary)

    verified_ids = {v.paper.arxiv_id for v in verified if v.paper}
    claimed_titles = {ref.get("title", "").lower() for ref in claimed_references}
    missing = [
        p for p in related
        if p.arxiv_id not in verified_ids
        and _title_similarity(p.title, " ".join(claimed_titles)) < 0.3
    ][:5]

    recent = await get_recent(topic_title, months_back=months_back)

    total_refs = len(claimed_references)
    verification_rate = len(verified) / max(1, total_refs) if total_refs > 0 else 0.0

    cutoff_year = datetime.now(timezone.utc).year - 1
    recent_count = sum(1 for p in recent if p.year >= cutoff_year)
    freshness = min(1.0, recent_count / 3.0)

    return CitationContext(
        verified_refs=verified,
        invalid_refs=invalid,
        missing_important=missing,
        recent_papers=recent,
        verification_rate=verification_rate,
        freshness_score=freshness,
    )
