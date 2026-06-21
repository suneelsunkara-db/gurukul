"""Gurukul evaluation harness using MLflow GenAI evaluation framework.

Evaluates all three agent roles against grounding, correctness, and
domain-specific quality criteria. Run with:
    GURUKUL_ENABLE_TRACING=1 uv run python evals/run_eval.py

Scorers used:
  Built-in:  Correctness, Guidelines
  Custom:    GroundingScorer, ReferenceIntegrityScorer, ConsistencyScorer,
             ExaminerFairnessScorer
"""

import logging
import os
import sys
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).resolve().parent.parent / ".env", override=True)

import mlflow
from mlflow.genai.scorers import (
    Correctness,
    Guidelines,
)

from evals.scorers import (
    GroundingScorer,
    ReferenceIntegrityScorer,
    EpistemicMarkerScorer,
    ContentStructureScorer,
    ExaminerFairnessScorer,
)
from evals.datasets import (
    build_student_eval_dataset,
    build_teacher_eval_dataset,
    build_examiner_eval_dataset,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

JUDGE_MODEL = f"databricks:/{os.getenv('TEACHER_MODEL', 'databricks-gpt-5-5')}"


def run_student_eval():
    """Evaluate Student agent content quality."""
    logger.info("=== Student Agent Evaluation ===")

    dataset = build_student_eval_dataset()
    if not dataset:
        logger.warning("No student eval data found. Generate topics first.")
        return

    results = mlflow.genai.evaluate(
        data=dataset,
        scorers=[
            GroundingScorer(),
            ReferenceIntegrityScorer(),
            EpistemicMarkerScorer(),
            ContentStructureScorer(),
            Guidelines(
                name="no_hallucinated_numbers",
                guidelines=(
                    "The response must not contain specific benchmark scores, "
                    "parameter counts, or performance metrics unless they are "
                    "explicitly attributed to a named source. Approximate ranges "
                    "with hedging language (e.g. 'approximately', 'on the order of') "
                    "are acceptable."
                ),
                model=JUDGE_MODEL,
            ),
            Guidelines(
                name="layered_accessibility",
                guidelines=(
                    "The response must provide content at multiple accessibility levels: "
                    "an ELI5 explanation using everyday analogies with zero jargon, "
                    "a technical summary for ML practitioners, and detailed key aspects "
                    "that build from intuition to technical depth."
                ),
                model=JUDGE_MODEL,
            ),
        ],
    )

    logger.info("Student eval complete. Results:")
    logger.info(results.metrics)
    return results


def run_teacher_eval():
    """Evaluate Teacher agent graph decomposition quality."""
    logger.info("=== Teacher Agent Evaluation ===")

    dataset = build_teacher_eval_dataset()
    if not dataset:
        logger.warning("No teacher eval data found.")
        return

    results = mlflow.genai.evaluate(
        data=dataset,
        scorers=[
            Guidelines(
                name="graph_connectivity",
                guidelines=(
                    "The generated knowledge graph must be connected — every topic "
                    "should be reachable from every other topic via edges. There should "
                    "be at least 1.5x as many edges as topics. Edge types must be from "
                    "the set: prerequisite, builds_on, contrasts, applies, related."
                ),
                model=JUDGE_MODEL,
            ),
            Guidelines(
                name="topic_coverage",
                guidelines=(
                    "The decomposition must cover the breadth of the field, including "
                    "foundational concepts, architectural innovations, training techniques, "
                    "and specific model families. It should not be a simple sequential list "
                    "of subtopics."
                ),
                model=JUDGE_MODEL,
            ),
            Guidelines(
                name="real_concepts_only",
                guidelines=(
                    "Every topic must correspond to a real, well-documented concept, "
                    "technique, or model family in the ML/AI literature. No invented "
                    "or plausible-sounding but non-standard terms."
                ),
                model=JUDGE_MODEL,
            ),
        ],
    )

    logger.info("Teacher eval complete. Results:")
    logger.info(results.metrics)
    return results


def run_examiner_eval():
    """Evaluate Examiner agent question/evaluation quality."""
    logger.info("=== Examiner Agent Evaluation ===")

    dataset = build_examiner_eval_dataset()
    if not dataset:
        logger.warning("No examiner eval data found.")
        return

    results = mlflow.genai.evaluate(
        data=dataset,
        scorers=[
            ExaminerFairnessScorer(),
            Guidelines(
                name="reasoning_not_recall",
                guidelines=(
                    "Questions must require reasoning, analysis, or synthesis — not "
                    "simple factual recall. A question like 'What is attention?' fails. "
                    "A question like 'Why does attention scale poorly with sequence "
                    "length, and what approaches address this?' passes."
                ),
                model=JUDGE_MODEL,
            ),
            Guidelines(
                name="evaluation_specificity",
                guidelines=(
                    "Evaluation feedback must cite specific parts of the learner's "
                    "answer (quoting their words) and provide specific corrections "
                    "with the correct information. Generic feedback like 'good job' "
                    "or 'needs work' without specifics fails."
                ),
                model=JUDGE_MODEL,
            ),
        ],
    )

    logger.info("Examiner eval complete. Results:")
    logger.info(results.metrics)
    return results


def main():
    """Entry point for `gurukul-eval` CLI and direct execution."""
    os.environ["GURUKUL_ENABLE_TRACING"] = "1"

    eval_type = sys.argv[1] if len(sys.argv) > 1 else "all"

    results = {}
    if eval_type in ("student", "all"):
        results["student"] = run_student_eval()
    if eval_type in ("teacher", "all"):
        results["teacher"] = run_teacher_eval()
    if eval_type in ("examiner", "all"):
        results["examiner"] = run_examiner_eval()

    ran = [k for k, v in results.items() if v is not None]
    skipped = [k for k, v in results.items() if v is None]

    logger.info("=" * 50)
    logger.info("Evaluation complete: %s", ", ".join(ran) if ran else "no data")
    if skipped:
        logger.info("Skipped (no data): %s", ", ".join(skipped))
    logger.info("View results in Gurukul UI: Eval Dashboard tab")
    logger.info("=" * 50)

    return results


if __name__ == "__main__":
    main()
