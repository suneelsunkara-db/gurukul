"""Runtime guardrails for Gurukul content generation.

Post-generation checks that run on Student/Examiner output before it
reaches the user or database. These are fast, code-based checks (no LLM
calls) that catch obvious hallucination patterns.
"""

from __future__ import annotations

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

HEDGING_MARKERS = [
    "well-established", "widely reported", "speculative",
    "not officially confirmed", "undisclosed", "approximately",
    "on the order of", "it is believed", "evidence suggests",
    "reportedly", "according to", "source:",
]


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


def sanitize_payload(payload: dict[str, Any]) -> tuple[dict[str, Any], list[dict[str, str]]]:
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


def _compute_level(accuracy: float, depth: float, reasoning: float) -> str:
    if accuracy < 2 or depth < 1:
        return "surface"
    if accuracy >= 3 and depth >= 3 and reasoning >= 2:
        return "creative"
    if accuracy >= 2 and depth >= 2 and reasoning >= 1:
        return "deep"
    return "structural"
