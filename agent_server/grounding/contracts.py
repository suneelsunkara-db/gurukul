"""Typed contracts for grounding evidence.

These contracts make source roles explicit. A web page, a paper, and model
prior knowledge are not interchangeable evidence, so downstream code should
consume typed grounding results rather than raw search snippets.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

SourceType = Literal["scholarly", "web", "model_prior"]
ResolutionStatus = Literal["resolved", "partial", "unresolved"]
RouteType = Literal["specific_entity", "general_concept", "multi_seed", "unresolved"]


@dataclass
class GroundingCandidate:
    source_type: SourceType
    source: str
    title: str
    abstract: str = ""
    url: str | None = None
    year: int | None = None
    external_id: str | None = None
    confidence: float | None = None
    metadata: dict[str, Any] = field(default_factory=dict)

    def text_pair(self) -> tuple[str, str]:
        return self.title, self.abstract or ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_type": self.source_type,
            "source": self.source,
            "title": self.title,
            "url": self.url,
            "year": self.year,
            "external_id": self.external_id,
            "confidence": self.confidence,
            "similarity": self.confidence,  # compatibility for existing evals
            "metadata": self.metadata,
        }


@dataclass
class GroundingResult:
    seed: str
    policy: str
    status: ResolutionStatus
    route: RouteType
    evidence: list[GroundingCandidate] = field(default_factory=list)
    disallowed_sources: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    @property
    def resolved_type(self) -> str:
        return self.route

    @property
    def entities(self) -> list[dict[str, Any]]:
        if self.route not in {"specific_entity", "general_concept"}:
            return []
        return [c.to_dict() for c in self.evidence[:3]]

    def to_record(self) -> dict[str, Any]:
        return {
            "seed": self.seed,
            "policy": self.policy,
            "status": self.status,
            "resolved_type": self.route,
            "route": self.route,
            "entities": self.entities,
            "evidence": {
                "top_candidates": [c.to_dict() for c in self.evidence[:8]],
                "disallowed_sources": self.disallowed_sources,
                "errors": self.errors,
                "notes": self.notes,
            },
        }

