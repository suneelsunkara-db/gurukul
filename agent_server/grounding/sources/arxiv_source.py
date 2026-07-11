"""arXiv grounding source."""

from __future__ import annotations

import logging

from agent_server import arxiv
from agent_server.grounding.contracts import GroundingCandidate
from agent_server.grounding.sources.base import GroundingSource, SourceSearchResult

logger = logging.getLogger(__name__)


class ArxivSource(GroundingSource):
    name = "arxiv"
    source_type = "scholarly"

    async def search(self, query: str, limit: int = 6) -> SourceSearchResult:
        try:
            papers = await arxiv.search(query, max_results=limit)
        except Exception as e:
            logger.warning("arXiv search failed for %r: %s", query, e)
            return SourceSearchResult(errors=[f"arxiv:{type(e).__name__}: {e}"])

        return SourceSearchResult(candidates=[
            GroundingCandidate(
                source_type="scholarly",
                source="arxiv",
                title=p.title,
                abstract=p.summary,
                url=p.abs_url,
                year=p.year,
                external_id=p.arxiv_id,
                metadata={"categories": p.categories, "authors": p.authors[:5]},
            )
            for p in papers
        ])

