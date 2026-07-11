"""Semantic Scholar grounding source."""

from __future__ import annotations

import logging
import os

import httpx

from agent_server.grounding.contracts import GroundingCandidate
from agent_server.grounding.sources.base import GroundingSource, SourceSearchResult

logger = logging.getLogger(__name__)

S2_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"
S2_FIELDS = "title,abstract,year,url,venue,citationCount,externalIds,authors"


class SemanticScholarSource(GroundingSource):
    name = "semantic_scholar"
    source_type = "scholarly"

    async def search(self, query: str, limit: int = 6) -> SourceSearchResult:
        api_key = os.getenv("S2_API_KEY", "")
        if not api_key:
            return SourceSearchResult(skipped=True, skip_reason="S2_API_KEY not configured")

        params = {"query": query, "limit": limit, "fields": S2_FIELDS}
        headers = {"x-api-key": api_key}
        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(S2_SEARCH_URL, params=params, headers=headers)
            if resp.status_code != 200:
                msg = f"semantic_scholar:http_{resp.status_code}: {resp.text[:200]}"
                logger.warning(msg)
                return SourceSearchResult(errors=[msg])
            data = resp.json()
        except Exception as e:
            logger.warning("Semantic Scholar search failed for %r: %s", query, e)
            return SourceSearchResult(errors=[f"semantic_scholar:{type(e).__name__}: {e}"])

        candidates: list[GroundingCandidate] = []
        for p in data.get("data", [])[:limit]:
            title = (p.get("title") or "").strip()
            if not title:
                continue
            ext = p.get("externalIds") or {}
            authors = p.get("authors") or []
            candidates.append(GroundingCandidate(
                source_type="scholarly",
                source="semantic_scholar",
                title=title,
                abstract=(p.get("abstract") or "").strip(),
                url=p.get("url") or (f"https://arxiv.org/abs/{ext['ArXiv']}" if ext.get("ArXiv") else None),
                year=p.get("year"),
                external_id=ext.get("ArXiv") or p.get("paperId"),
                metadata={
                    "venue": p.get("venue"),
                    "citation_count": p.get("citationCount"),
                    "authors": [a.get("name") for a in authors[:5] if a.get("name")],
                    "external_ids": ext,
                },
            ))
        return SourceSearchResult(candidates=candidates)

