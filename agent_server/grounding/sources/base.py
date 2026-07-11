"""Base source adapter contract."""

from __future__ import annotations

from dataclasses import dataclass, field

from agent_server.grounding.contracts import GroundingCandidate


@dataclass
class SourceSearchResult:
    candidates: list[GroundingCandidate] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    skipped: bool = False
    skip_reason: str | None = None


class GroundingSource:
    name: str
    source_type: str

    async def search(self, query: str, limit: int = 6) -> SourceSearchResult:
        raise NotImplementedError

