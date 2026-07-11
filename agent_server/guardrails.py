"""Runtime guardrails for Gurukul content generation.

Post-generation checks that run on Student/Examiner output before it
reaches the user or database. These are fast, code-based checks (no LLM
calls) that catch obvious hallucination patterns.
"""

from __future__ import annotations

import copy
import logging
import re
from typing import Any

logger = logging.getLogger(__name__)

ARXIV_ID_PATTERN = re.compile(r"^\d{4}\.\d{4,5}$")

KNOWN_FAKE_PATTERNS = [
    re.compile(r"Smith et al\., \d{4}"),
    re.compile(r"Johnson et al\., \d{4}"),
    re.compile(r"Lee et al\., \d{4}"),
]

UNHEDGED_CLAIM_PATTERNS = [
    (re.compile(r"achieves (\d+(\.\d+)?)%"), "benchmark_score"),
    (re.compile(r"has (\d+)[BMT] parameters"), "param_count"),
    (re.compile(r"outperforms .+ by (\d+)"), "perf_comparison"),
    (re.compile(r"trained on (\d+)[BMTGP] tokens"), "training_data"),
    (re.compile(r"was released in (January|February|March|April|May|June|July|August|September|October|November|December) \d{4}"), "release_date"),
]

SPECIFIC_CLAIM_PATTERNS = [
    (re.compile(r"\b\d+(?:\.\d+)?\s?%"), "numeric_metric"),
    (re.compile(r"\b\d+(?:\.\d+)?[BMTGP](?:-[A-Z]\d+(?:\.\d+)?[BMTGP])?\b"), "scale_or_size"),
    (re.compile(r"\b(?:achieves?|outperforms?)\b", re.IGNORECASE), "performance_claim"),
    (re.compile(r"\b(?:benchmark|leaderboard|evaluation result|recall-vs-latency)\b", re.IGNORECASE), "benchmark_claim"),
    (re.compile(r"\btrained (?:on|with|to)\b", re.IGNORECASE), "training_claim"),
    (re.compile(r"\bempirically (?:shown|demonstrated|validated)\b", re.IGNORECASE), "empirical_claim"),
    (re.compile(r"\btransfer across\b", re.IGNORECASE), "transfer_claim"),
    (re.compile(r"\bstate-of-the-art\b", re.IGNORECASE), "sota_claim"),
]

HEDGING_MARKERS = [
    "well-established", "widely reported", "speculative",
    "not officially confirmed", "undisclosed", "approximately",
    "on the order of", "it is believed", "evidence suggests",
    "reportedly", "according to", "source:",
]

STOPWORDS = {
    "a", "an", "and", "are", "as", "at", "be", "by", "for", "from", "has",
    "in", "into", "is", "it", "its", "of", "on", "or", "that", "the", "their",
    "this", "to", "with", "without", "via", "what", "when", "where", "why",
}


def validate_references(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Check references for obvious fabrication signals."""
    issues = []
    refs = payload.get("references", [])

    for i, ref in enumerate(refs):
        title = ref.get("title", "")
        arxiv = ref.get("arxiv")
        authors = ref.get("authors", "")

        if arxiv and not ARXIV_ID_PATTERN.match(str(arxiv)):
            issues.append({
                "type": "invalid_arxiv",
                "severity": "high",
                "message": f"Reference {i}: arXiv ID '{arxiv}' has invalid format",
                "fix": "remove",
            })

        for pattern in KNOWN_FAKE_PATTERNS:
            if pattern.search(authors):
                issues.append({
                    "type": "suspicious_authors",
                    "severity": "medium",
                    "message": f"Reference {i}: author pattern '{authors}' looks generic",
                    "fix": "flag",
                })

        if title and len(title.split()) < 4:
            issues.append({
                "type": "short_title",
                "severity": "medium",
                "message": f"Reference {i}: title '{title}' suspiciously short",
                "fix": "flag",
            })

    return issues


def validate_claims(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Check for unhedged quantitative claims."""
    issues = []
    text = _payload_to_text(payload)

    for pattern, claim_type in UNHEDGED_CLAIM_PATTERNS:
        for match in pattern.finditer(text):
            context_start = max(0, match.start() - 100)
            context_end = min(len(text), match.end() + 100)
            context = text[context_start:context_end]

            is_hedged = any(m in context.lower() for m in HEDGING_MARKERS)
            if not is_hedged:
                issues.append({
                    "type": f"unhedged_{claim_type}",
                    "severity": "medium",
                    "message": f"Unhedged {claim_type}: '{match.group()}'",
                    "context": context.strip()[:200],
                    "fix": "flag",
                })

    return issues


def validate_structure(payload: dict[str, Any]) -> list[dict[str, str]]:
    """Validate output structure matches schema."""
    issues = []
    required = ["summary", "takeaway", "key_aspects"]

    for field in required:
        if field not in payload:
            issues.append({
                "type": "missing_field",
                "severity": "high",
                "message": f"Missing required field: {field}",
                "fix": "regenerate",
            })

    aspects = payload.get("key_aspects", [])
    if isinstance(aspects, list):
        for i, a in enumerate(aspects):
            if not isinstance(a, dict):
                issues.append({"type": "bad_aspect", "severity": "high", "message": f"key_aspects[{i}] is not a dict", "fix": "regenerate"})
            elif "title" not in a or "body" not in a:
                issues.append({"type": "bad_aspect", "severity": "medium", "message": f"key_aspects[{i}] missing title or body", "fix": "flag"})

    return issues


def validate_evidence_alignment(
    payload: dict[str, Any],
    *,
    topic_title: str = "",
    source_titles: list[str] | None = None,
    source_evidence: list[str] | None = None,
) -> list[dict[str, str]]:
    """Check whether specific claims and references are supported by supplied evidence.

    This is intentionally conservative. It does not prove truth, but it prevents
    polished model-prior details from being published when the prompt evidence is
    too thin to support them.
    """
    issues: list[dict[str, str]] = []
    evidence = [e for e in (source_evidence or []) if e]
    titles = [t for t in (source_titles or []) if t]
    refs = payload.get("references", [])

    evidence_text = "\n".join([*titles, *evidence, *[str(r.get("title", "")) for r in refs]])
    evidence_tokens = _tokens(evidence_text)

    topic_tokens = _tokens(topic_title)
    prose_tokens = _tokens(_payload_to_text(payload))
    relevance_basis = topic_tokens | set(list(prose_tokens)[:120])

    for i, ref in enumerate(refs):
        title = str(ref.get("title") or "")
        if not title:
            continue
        ref_tokens = _tokens(title)
        topic_overlap = _overlap(ref_tokens, relevance_basis)
        source_overlap = max((_overlap(ref_tokens, _tokens(t)) for t in titles), default=0.0)
        if topic_overlap < 0.12 and source_overlap < 0.35:
            issues.append({
                "type": "weak_reference_relevance",
                "severity": "medium",
                "message": f"Reference {i}: '{title[:80]}' is weakly related to topic '{topic_title}'",
                "fix": "flag",
            })

    if not evidence_tokens and refs:
        evidence_tokens = _tokens(" ".join(str(r.get("title", "")) for r in refs))

    for sentence in _sentences(_payload_to_text(payload)):
        if not _is_specific_claim(sentence):
            continue
        if _is_uncertainty_statement(sentence) and not _has_hard_numeric_claim(sentence):
            continue
        if _has_inline_support(sentence, refs):
            continue
        if evidence_tokens and _overlap(_tokens(sentence), evidence_tokens) >= 0.35:
            continue
        issues.append({
            "type": "unsupported_specific_claim",
            "severity": "high",
            "message": f"Unsupported specific claim: '{sentence[:180]}'",
            "context": sentence[:220],
            "fix": "regenerate",
        })
        if len([i for i in issues if i["type"] == "unsupported_specific_claim"]) >= 5:
            break

    return issues


def sanitize_payload(
    payload: dict[str, Any],
    *,
    topic_title: str = "",
    source_titles: list[str] | None = None,
    source_evidence: list[str] | None = None,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    """Run all guardrails and auto-fix what we can.

    Returns (sanitized_payload, all_issues).
    Issues with fix="remove" are automatically corrected.
    Issues with fix="flag" are left in but reported.
    Issues with fix="regenerate" indicate content should be retried.
    """
    all_issues = []

    all_issues.extend(validate_structure(payload))
    all_issues.extend(validate_references(payload))
    all_issues.extend(validate_claims(payload))
    all_issues.extend(validate_evidence_alignment(
        payload,
        topic_title=topic_title,
        source_titles=source_titles,
        source_evidence=source_evidence,
    ))

    sanitized = dict(payload)
    refs = sanitized.get("references", [])
    if refs:
        clean_refs = []
        for i, ref in enumerate(refs):
            should_remove = any(
                issue["type"] == "invalid_arxiv" and f"Reference {i}:" in issue["message"]
                for issue in all_issues
            )
            if should_remove:
                logger.warning("Removed reference with invalid arXiv ID: %s", ref.get("title", ""))
            else:
                clean_refs.append(ref)
        sanitized["references"] = clean_refs

    high_severity = sum(1 for i in all_issues if i["severity"] == "high")
    if high_severity > 0:
        logger.warning(
            "Content has %d high-severity issues: %s",
            high_severity,
            [i["message"] for i in all_issues if i["severity"] == "high"],
        )

    return sanitized, all_issues


def redact_unsupported_claims(payload: dict[str, Any], issues: list[dict[str, str]]) -> dict[str, Any]:
    """Replace unsupported specific-claim sentences with an explicit evidence note."""
    unsupported = [
        (i.get("context") or _extract_claim_from_message(i.get("message", ""))).strip()
        for i in issues
        if i.get("type") == "unsupported_specific_claim"
    ]
    unsupported = [u for u in unsupported if u]
    if not unsupported:
        return payload

    redacted = copy.deepcopy(payload)
    replacement = (
        "The available evidence is insufficient to support a more specific claim here, "
        "so Gurukul does not assert it."
    )

    def redact_text(text: str) -> str:
        out = text
        for claim in unsupported:
            for sentence in _sentences(out):
                if _same_sentence(sentence, claim):
                    out = out.replace(sentence, replacement)
        return out

    for key in ("summary", "takeaway", "eli5"):
        if isinstance(redacted.get(key), str):
            redacted[key] = redact_text(redacted[key])

    for aspect in redacted.get("key_aspects", []):
        if isinstance(aspect, dict):
            for key in ("intuition", "body"):
                if isinstance(aspect.get(key), str):
                    aspect[key] = redact_text(aspect[key])

    for problem in redacted.get("open_problems", []):
        if isinstance(problem, dict):
            for key in ("question", "why"):
                if isinstance(problem.get(key), str):
                    problem[key] = redact_text(problem[key])

    experiment = redacted.get("experiment")
    if isinstance(experiment, dict):
        for key in ("title", "hypothesis"):
            if isinstance(experiment.get(key), str):
                experiment[key] = redact_text(experiment[key])
        for step in experiment.get("steps", []):
            if isinstance(step, dict) and isinstance(step.get("text"), str):
                step["text"] = redact_text(step["text"])

    return redacted


def validate_examiner_output(output: dict[str, Any]) -> list[dict[str, str]]:
    """Validate Examiner evaluation output for internal consistency."""
    issues = []

    accuracy = output.get("accuracy", -1)
    depth = output.get("depth", -1)
    reasoning = output.get("reasoning", -1)
    level = output.get("level", "")
    feedback = output.get("feedback", "")

    for name, val, lo, hi in [("accuracy", accuracy, 0, 3), ("depth", depth, 0, 3), ("reasoning", reasoning, 0, 2)]:
        if not (lo <= val <= hi):
            issues.append({"type": "score_range", "severity": "high", "message": f"{name}={val} out of [{lo},{hi}]"})

    expected = _compute_level(accuracy, depth, reasoning)
    if level and level != expected:
        issues.append({
            "type": "level_mismatch",
            "severity": "high",
            "message": f"Level '{level}' inconsistent with scores → expected '{expected}'",
        })
        output["level"] = expected

    if len(feedback) < 30:
        issues.append({"type": "vague_feedback", "severity": "medium", "message": "Feedback too short to be specific"})

    return issues


def _payload_to_text(payload: dict[str, Any]) -> str:
    """Flatten payload to searchable text."""
    parts = []
    for key in ("summary", "takeaway", "eli5"):
        if key in payload:
            parts.append(str(payload[key]))
    for aspect in payload.get("key_aspects", []):
        if isinstance(aspect, dict):
            parts.append(str(aspect.get("body", "")))
            parts.append(str(aspect.get("intuition", "")))
    return " ".join(parts)


def _tokens(text: str) -> set[str]:
    words = re.findall(r"[a-z0-9][a-z0-9\-]{2,}", text.lower())
    return {w for w in words if w not in STOPWORDS}


def _overlap(a: set[str], b: set[str]) -> float:
    if not a or not b:
        return 0.0
    return len(a & b) / max(1, min(len(a), len(b)))


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [p.strip() for p in parts if len(p.strip()) >= 40]


def _is_specific_claim(sentence: str) -> bool:
    return any(pattern.search(sentence) for pattern, _ in SPECIFIC_CLAIM_PATTERNS)


def _has_hard_numeric_claim(sentence: str) -> bool:
    hard_patterns = [
        r"\b\d+(?:\.\d+)?\s?%",
        r"\b\d+(?:\.\d+)?[BMTGP](?:-[A-Z]\d+(?:\.\d+)?[BMTGP])?\b",
        r"\b\d+x\b",
    ]
    return any(re.search(p, sentence, re.IGNORECASE) for p in hard_patterns)


def _is_uncertainty_statement(sentence: str) -> bool:
    lowered = sentence.lower()
    markers = [
        "available evidence",
        "community consensus",
        "details are undisclosed",
        "exact details",
        "limited to",
        "likely",
        "not available",
        "not claimed",
        "not directly support",
        "not disclosed",
        "not fully disclosed",
        "not unique",
        "not yet resolved",
        "open empirical question",
        "requires reading",
        "should be interpreted",
        "suggests",
        "speculative",
        "unclear",
        "unknown",
    ]
    return any(m in lowered for m in markers)


def _has_inline_support(sentence: str, refs: list[dict[str, Any]]) -> bool:
    lowered = sentence.lower()
    if (
        "source:" in lowered
        or "arxiv:" in lowered
        or "according to" in lowered
        or "authors report" in lowered
        or "reported by" in lowered
        or "own reporting" in lowered
    ):
        return True
    if re.search(r"\([A-Z][A-Za-z\-]+(?: et al\.)?,? \d{4}\)", sentence):
        return True
    if re.search(r"\b[A-Z][A-Za-z\-]+ et al\.? \(\d{4}\)", sentence):
        return True
    for ref in refs:
        title = str(ref.get("title") or "").lower()
        if title and len(title) > 12 and title in lowered:
            return True
        arxiv_id = str(ref.get("arxiv") or "")
        if arxiv_id and arxiv_id in sentence:
            return True
    return False


def _extract_claim_from_message(message: str) -> str:
    match = re.search(r"Unsupported specific claim: '(.+)'", message)
    return match.group(1) if match else ""


def _same_sentence(sentence: str, claim: str) -> bool:
    if not sentence or not claim:
        return False
    s = re.sub(r"\s+", " ", sentence).strip()
    c = re.sub(r"\s+", " ", claim).strip()
    return s.startswith(c[:80]) or c.startswith(s[:80])


def _compute_level(accuracy: float, depth: float, reasoning: float) -> str:
    if accuracy < 2 or depth < 1:
        return "surface"
    if accuracy >= 3 and depth >= 3 and reasoning >= 2:
        return "creative"
    if accuracy >= 2 and depth >= 2 and reasoning >= 1:
        return "deep"
    return "structural"
