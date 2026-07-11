"""Policy-driven grounding resolver."""

from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from agent_server.embeddings import cosine, embed_papers, embed_seed
from agent_server.grounding.contracts import GroundingCandidate, GroundingResult
from agent_server.grounding.policy import STRICT_SCHOLARLY, GroundingPolicy
from agent_server.grounding.sources.arxiv_source import ArxivSource
from agent_server.grounding.sources.base import GroundingSource
from agent_server.grounding.sources.lakebase_corpus_source import LakebaseCorpusSource
from agent_server.grounding.sources.semantic_scholar_source import SemanticScholarSource

logger = logging.getLogger(__name__)


def split_seed(seed: str) -> list[str]:
    """Split obvious multi-seed inputs without breaking named entities."""
    text = seed.strip()
    if not text:
        return []
    parts = re.split(r"\s*(?:,|;|/|\+|\band\b|\bvs\.?\b|\bversus\b)\s*", text, flags=re.I)
    cleaned = [p.strip(" .") for p in parts if len(p.strip(" .")) >= 3]
    return cleaned if len(cleaned) > 1 else [text]


def _default_sources() -> list[GroundingSource]:
    return [LakebaseCorpusSource(), ArxivSource(), SemanticScholarSource()]


def _dedupe(candidates: list[GroundingCandidate]) -> list[GroundingCandidate]:
    seen_ids: set[str] = set()
    seen_titles: set[str] = set()
    out: list[GroundingCandidate] = []
    for c in candidates:
        title_key = re.sub(r"\s+", " ", c.title.lower()).strip()
        id_key = (c.external_id or "").lower().strip()
        if title_key in seen_titles or (id_key and id_key in seen_ids):
            continue
        seen_titles.add(title_key)
        if id_key:
            seen_ids.add(id_key)
        out.append(c)
    return out


async def _score(seed: str, candidates: list[GroundingCandidate]) -> list[GroundingCandidate]:
    if not candidates:
        return []
    query_vec = await embed_seed(seed)
    paper_vecs = await embed_papers([c.text_pair() for c in candidates])
    for c, vec in zip(candidates, paper_vecs):
        c.confidence = cosine(query_vec, vec)
    return sorted(candidates, key=lambda c: c.confidence or 0, reverse=True)


def _compact(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "", text.lower())


def _has_named_entity_title_match(seed: str, top_candidate: GroundingCandidate) -> bool:
    """Detect exact paper/entity names without overfitting to vector gaps.

    SPECTER2 can surface multiple very-similar papers from the same family,
    which shrinks the top-vs-second gap. If the top title begins with a
    multi-token seed (e.g. `Qwen-AgentWorld: ...`), that is strong entity
    evidence even when the vector gap is small.
    """
    seed_tokens = re.findall(r"[A-Za-z0-9]+", seed)
    if len(seed_tokens) < 2:
        return False
    seed_key = _compact(seed)
    title_key = _compact(top_candidate.title)
    return bool(seed_key and title_key.startswith(seed_key))


def _classify(seed: str, scored: list[GroundingCandidate], policy: GroundingPolicy) -> tuple[str, str, list[str]]:
    notes: list[str] = [f"policy={policy.name}"]
    if not scored:
        notes.append("No allowed scholarly candidates with embedding scores.")
        return "unresolved", "unresolved", notes

    top = scored[0].confidence or 0.0
    second = scored[1].confidence or 0.0 if len(scored) > 1 else 0.0
    gap = top - second
    notes.append(f"top_similarity={top:.3f}; second={second:.3f}; gap={gap:.3f}")

    if top >= 0.55 and _has_named_entity_title_match(seed, scored[0]):
        notes.append("specific_entity=title_prefix_match")
        return "resolved", "specific_entity", notes
    if top >= 0.72 and gap >= 0.06:
        return "resolved", "specific_entity", notes
    if top >= 0.55:
        return "resolved", "general_concept", notes
    return "unresolved", "unresolved", notes


async def resolve_one(
    seed: str,
    *,
    policy: GroundingPolicy = STRICT_SCHOLARLY,
    sources: list[GroundingSource] | None = None,
) -> GroundingResult:
    active_sources = sources or _default_sources()
    allowed = [s for s in active_sources if policy.allows(s.source_type)]
    disallowed = [s.name for s in active_sources if not policy.allows(s.source_type)]

    search_results = await asyncio.gather(
        *(s.search(seed, limit=6) for s in allowed),
        return_exceptions=True,
    )

    candidates: list[GroundingCandidate] = []
    errors: list[str] = []
    notes: list[str] = []
    for source, result in zip(allowed, search_results):
        if isinstance(result, Exception):
            errors.append(f"{source.name}:{type(result).__name__}: {result}")
            continue
        if result.skipped:
            notes.append(f"{source.name}:skipped:{result.skip_reason}")
        errors.extend(result.errors)
        candidates.extend(result.candidates)

    candidates = _dedupe(candidates)
    try:
        scored = await _score(seed, candidates[:10])
    except Exception as e:
        logger.error("Grounding scoring failed for %r: %s", seed, e)
        return GroundingResult(
            seed=seed,
            policy=policy.name,
            status="unresolved",
            route="unresolved",
            evidence=[],
            disallowed_sources=disallowed,
            errors=errors + [f"embedding:{type(e).__name__}: {e}"],
            notes=notes,
        )

    status, route, classify_notes = _classify(seed, scored, policy)
    return GroundingResult(
        seed=seed,
        policy=policy.name,
        status=status,  # type: ignore[arg-type]
        route=route,  # type: ignore[arg-type]
        evidence=scored,
        disallowed_sources=disallowed,
        errors=errors,
        notes=notes + classify_notes,
    )


async def resolve_seed(
    seed: str,
    *,
    policy: GroundingPolicy = STRICT_SCHOLARLY,
) -> dict[str, Any]:
    parts = split_seed(seed)
    results = await asyncio.gather(*(resolve_one(p, policy=policy) for p in parts))
    overall = "multi_seed" if len(parts) > 1 else results[0].route
    status = "partial" if len(parts) > 1 and any(r.status == "unresolved" for r in results) else (
        "resolved" if all(r.status == "resolved" for r in results) else "unresolved"
    )
    return {
        "original_seed": seed,
        "policy": policy.name,
        "status": status,
        "resolved_type": overall,
        "route": overall,
        "parts": [r.to_record() for r in results],
    }


def format_grounding_context(resolution: dict[str, Any]) -> str:
    lines = [
        "SEED RESOLUTION / GROUNDING CONTEXT:",
        f"- Original seed: {resolution.get('original_seed')}",
        f"- Policy: {resolution.get('policy')}",
        f"- Status: {resolution.get('status')}",
        f"- Route: {resolution.get('route') or resolution.get('resolved_type')}",
        "",
        "Evidence source priority:",
        "- lakebase_corpus: curated local scholarly corpus with SPECTER2 + Lakebase retrieval.",
        "- arxiv: live arXiv scholarly metadata.",
        "- semantic_scholar: live Semantic Scholar scholarly metadata.",
        "",
        "Use only the typed evidence below. Do not invent papers/entities for unresolved seed parts.",
        "For multi-seed routes, allocate meaningful coverage to each seed part.",
    ]
    for part in resolution.get("parts", []):
        lines.append("")
        lines.append(
            f"Seed part: {part.get('seed')} -> {part.get('route') or part.get('resolved_type')} "
            f"(status={part.get('status')}, policy={part.get('policy')})"
        )
        evidence = (part.get("evidence") or {})
        candidates = evidence.get("top_candidates", [])
        source_counts: dict[str, int] = {}
        for c in candidates:
            source = c.get("source") or "unknown"
            source_counts[source] = source_counts.get(source, 0) + 1
        if source_counts:
            counts = ", ".join(f"{source}={count}" for source, count in sorted(source_counts.items()))
            lines.append(f"Evidence sources: {counts}")
        for c in candidates[:3]:
            sim = c.get("confidence", c.get("similarity"))
            sim_s = f"{sim:.3f}" if isinstance(sim, (int, float)) else "n/a"
            lines.append(
                f"- [{c.get('source_type')}:{c.get('source')}] {c.get('title')} "
                f"({c.get('year') or 'n.d.'}, confidence={sim_s}) {c.get('url') or ''}".strip()
            )
        errors = evidence.get("errors") or []
        notes = evidence.get("notes") or []
        if errors:
            lines.append("Errors: " + "; ".join(errors[:3]))
        if notes:
            lines.append("Notes: " + "; ".join(notes[:3]))
    return "\n".join(lines)

