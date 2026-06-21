"""Custom MLflow scorers for Gurukul agent evaluation.

These scorers use code-based logic (no LLM call needed) to check
structural and grounding properties of generated content.

Uses mlflow.genai.scorers.Scorer with __call__ returning Feedback objects.
"""

import json
import re
from typing import Any, Optional

from mlflow.genai.scorers import Scorer
from mlflow.genai.scorers.base import Feedback


class GroundingScorer(Scorer):
    """Checks that claims use epistemic markers and don't assert ungrounded facts."""

    name: str = "grounding_quality"

    HEDGING_PATTERNS: list[str] = [
        r"well-established",
        r"widely reported",
        r"widely believed",
        r"speculative",
        r"not officially confirmed",
        r"not been publicly disclosed",
        r"undisclosed",
        r"approximately",
        r"on the order of",
        r"the author's interpretation",
        r"rumored",
        r"it is believed",
        r"evidence suggests",
    ]

    UNGROUNDED_PATTERNS: list[str] = [
        r"achieves \d+(\.\d+)?%",
        r"has \d+[BMT] parameters",
        r"outperforms .+ by \d+",
        r"is the (best|fastest|largest|smallest)",
        r"was trained on \d+[BMTGP] tokens",
    ]

    def __call__(self, *, outputs: Optional[dict] = None, **kwargs) -> Feedback:
        if not outputs:
            return Feedback(value=0.0, rationale="No output to evaluate")

        text = json.dumps(outputs) if isinstance(outputs, dict) else str(outputs)

        hedged_count = sum(
            1 for p in self.HEDGING_PATTERNS if re.search(p, text, re.IGNORECASE)
        )

        ungrounded = []
        for p in self.UNGROUNDED_PATTERNS:
            for match in re.finditer(p, text, re.IGNORECASE):
                context = text[max(0, match.start() - 50):match.end() + 50]
                has_hedge = any(
                    re.search(hp, context, re.IGNORECASE)
                    for hp in self.HEDGING_PATTERNS
                )
                if not has_hedge:
                    ungrounded.append(match.group())

        total_claims = hedged_count + len(ungrounded)
        if total_claims == 0:
            score = 1.0
            rationale = "No specific quantitative claims found (neutral)"
        else:
            score = max(0.0, 1.0 - (len(ungrounded) / total_claims))
            rationale = (
                f"{hedged_count} hedged claims, {len(ungrounded)} ungrounded claims. "
                f"Ungrounded: {ungrounded[:3]}"
            )

        return Feedback(value=round(score, 2), rationale=rationale)


class ReferenceIntegrityScorer(Scorer):
    """Checks that references have real-looking arXiv IDs and no obvious fabrication patterns."""

    name: str = "reference_integrity"

    def __call__(self, *, outputs: Optional[dict] = None, **kwargs) -> Feedback:
        if not outputs or not isinstance(outputs, dict):
            return Feedback(value=True, rationale="No references to check")

        refs = outputs.get("references", [])
        if not refs:
            return Feedback(value=True, rationale="No references (acceptable if content is conceptual)")

        arxiv_pattern = re.compile(r"^\d{4}\.\d{4,5}$")
        issues = []

        for i, r in enumerate(refs):
            title = r.get("title", "")
            arxiv = r.get("arxiv")
            authors = r.get("authors", "")

            if arxiv and not arxiv_pattern.match(str(arxiv)):
                issues.append(f"Ref {i}: invalid arXiv ID '{arxiv}'")

            if not title or len(title) < 10:
                issues.append(f"Ref {i}: suspiciously short title '{title}'")

            if not authors:
                issues.append(f"Ref {i}: missing authors for '{title[:40]}'")

        passed = len(issues) == 0
        rationale = "All references pass integrity checks" if passed else f"{len(issues)} issues: {'; '.join(issues[:5])}"
        return Feedback(value=passed, rationale=rationale)


class EpistemicMarkerScorer(Scorer):
    """Checks density of epistemic markers (confidence signals) in content."""

    name: str = "epistemic_markers"

    MARKERS: list[str] = [
        "well-established", "widely reported", "speculative",
        "not officially confirmed", "undisclosed", "approximately",
        "it is believed", "evidence suggests", "widely believed",
        "the exact details", "has not been publicly",
        "high confidence", "medium confidence", "low confidence", "unknown",
    ]

    def __call__(self, *, outputs: Optional[dict] = None, **kwargs) -> Feedback:
        if not outputs:
            return Feedback(value=0.0, rationale="No output")

        text = json.dumps(outputs) if isinstance(outputs, dict) else str(outputs)
        word_count = len(text.split())

        marker_count = sum(
            1 for m in self.MARKERS if m.lower() in text.lower()
        )

        density = marker_count / max(1, word_count / 100)

        if density >= 1.5:
            score = 1.0
        elif density >= 0.5:
            score = 0.7
        elif density > 0:
            score = 0.4
        else:
            score = 0.1

        return Feedback(
            value=round(score, 2),
            rationale=(
                f"{marker_count} epistemic markers in {word_count} words "
                f"(density: {density:.2f} per 100 words)"
            ),
        )


class ContentStructureScorer(Scorer):
    """Validates that Student output has all required fields with correct types."""

    name: str = "content_structure"

    def __call__(self, *, outputs: Optional[dict] = None, **kwargs) -> Feedback:
        if not outputs or not isinstance(outputs, dict):
            return Feedback(value=False, rationale="Output is not a valid dict")

        required = {"summary": str, "takeaway": str, "key_aspects": list}
        issues = []
        checks = 0

        for field, expected_type in required.items():
            checks += 1
            if field not in outputs:
                issues.append(f"Missing required: {field}")
            elif not isinstance(outputs[field], expected_type):
                issues.append(f"Wrong type for {field}: expected {expected_type.__name__}")

        if "key_aspects" in outputs:
            checks += 1
            aspects = outputs["key_aspects"]
            if len(aspects) < 1:
                issues.append("No key_aspects provided")
            for i, a in enumerate(aspects):
                if not isinstance(a, dict) or "title" not in a or "body" not in a:
                    issues.append(f"key_aspects[{i}] missing title or body")
                    break

        passed = len(issues) == 0
        rationale = f"{checks} checks passed" if passed else f"{len(issues)} issues: {'; '.join(issues[:5])}"
        return Feedback(value=passed, rationale=rationale)


class ExaminerFairnessScorer(Scorer):
    """Evaluates whether the Examiner's scoring is consistent with its feedback."""

    name: str = "examiner_fairness"

    def __call__(self, *, outputs: Optional[dict] = None, **kwargs) -> Feedback:
        if not outputs or not isinstance(outputs, dict):
            return Feedback(value=False, rationale="Cannot evaluate non-dict output")

        issues = []

        accuracy = outputs.get("accuracy", -1)
        depth = outputs.get("depth", -1)
        reasoning = outputs.get("reasoning", -1)
        level = outputs.get("level", "")
        feedback = outputs.get("feedback", "")

        for name, val, lo, hi in [("accuracy", accuracy, 0, 3), ("depth", depth, 0, 3), ("reasoning", reasoning, 0, 2)]:
            if not (lo <= val <= hi):
                issues.append(f"{name}={val} out of [{lo},{hi}]")

        expected_level = self._compute_level(accuracy, depth, reasoning)
        if level and level != expected_level:
            issues.append(
                f"Level '{level}' inconsistent with scores "
                f"(acc={accuracy}, dep={depth}, reas={reasoning}) -> expected '{expected_level}'"
            )

        if len(feedback) < 50:
            issues.append(f"Feedback too short ({len(feedback)} chars)")

        if feedback and "good job" in feedback.lower() and accuracy < 3:
            issues.append("Says 'good job' but accuracy < 3")

        passed = len(issues) == 0
        rationale = "Scoring is consistent and specific" if passed else f"{len(issues)} issues: {'; '.join(issues)}"
        return Feedback(value=passed, rationale=rationale)

    @staticmethod
    def _compute_level(accuracy: float, depth: float, reasoning: float) -> str:
        if accuracy < 2 or depth < 1:
            return "surface"
        if accuracy >= 3 and depth >= 3 and reasoning >= 2:
            return "creative"
        if accuracy >= 2 and depth >= 2 and reasoning >= 1:
            return "deep"
        return "structural"
