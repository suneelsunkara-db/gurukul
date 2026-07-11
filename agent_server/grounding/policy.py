"""Grounding policies.

A policy is the architecture-level answer to "fallbacks": each workflow chooses
which source classes are allowed and what to do when evidence is missing.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class GroundingPolicy:
    name: str
    allowed_source_types: frozenset[str]
    on_no_match: str = "unresolved"
    require_labels: bool = True
    notes: tuple[str, ...] = field(default_factory=tuple)

    def allows(self, source_type: str) -> bool:
        return source_type in self.allowed_source_types


STRICT_SCHOLARLY = GroundingPolicy(
    name="strict_scholarly",
    allowed_source_types=frozenset({"scholarly"}),
    on_no_match="unresolved",
    notes=(
        "Only scholarly evidence is allowed.",
        "Do not use web search as a substitute for paper/entity resolution.",
    ),
)


FRESHNESS_AUGMENTED = GroundingPolicy(
    name="freshness_augmented",
    allowed_source_types=frozenset({"scholarly", "web"}),
    on_no_match="unresolved",
    notes=(
        "Web evidence may provide freshness context.",
        "Web evidence must be labeled and cannot override scholarly evidence.",
    ),
)

