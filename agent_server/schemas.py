"""Strict JSON schemas for structured LLM outputs.

These constrain model decoding via the Databricks Foundation Model APIs
`response_format={"type": "json_schema", ...}` mechanism, so every response is
guaranteed to be valid, complete JSON with all keys present and correctly typed.
This fixes the root cause of parse failures (truncation, omitted keys, prose
wrapping) at the source rather than repairing malformed output after the fact.

Databricks strict mode only supports a SUBSET of JSON schema:
  - every object must set ``additionalProperties: false`` and list ALL of its
    properties in ``required``;
  - no ``pattern``, ``anyOf``/``oneOf``/``allOf``, ``$ref``, ``prefixItems``;
  - nullable values use the list form ``["<type>", "null"]`` only.

The helpers below enforce the object rule by construction. Keep schemas flat.
"""

from __future__ import annotations

from typing import Any

# Scalar leaves
STR: dict[str, Any] = {"type": "string"}
INT: dict[str, Any] = {"type": "integer"}
NUM: dict[str, Any] = {"type": "number"}
BOOL: dict[str, Any] = {"type": "boolean"}
NULL: dict[str, Any] = {"type": "null"}
STR_OR_NULL: dict[str, Any] = {"type": ["string", "null"]}
INT_OR_NULL: dict[str, Any] = {"type": ["integer", "null"]}


def obj(props: dict[str, Any]) -> dict[str, Any]:
    """Object schema with all keys required and no extra properties (strict)."""
    return {
        "type": "object",
        "properties": props,
        "required": list(props.keys()),
        "additionalProperties": False,
    }


def arr(items: dict[str, Any]) -> dict[str, Any]:
    return {"type": "array", "items": items}


def response_format(name: str, schema: dict[str, Any]) -> dict[str, Any]:
    """Wrap a schema as a Databricks/OpenAI strict json_schema response_format."""
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": schema},
    }


# ── Teacher: knowledge-graph decomposition ───────────────────────────
DECOMPOSE_SCHEMA = obj({
    "topics": arr(obj({
        "id": STR,
        "title": STR,
        "category": STR,
        "rationale": STR,
        "is_comparison": BOOL,
    })),
    "edges": arr(obj({
        "source": STR,
        "target": STR,
        "type": STR,
        "label": STR,
        "strength": NUM,
    })),
})

# ── Student: topic content (NON-comparison only) ─────────────────────
# Comparison chapters use dynamic model-name keys in model_comparison.cells,
# which strict mode (additionalProperties:false) cannot express — those stay on
# the free-text path by design.
TOPIC_CONTENT_SCHEMA = obj({
    "summary": STR,
    "takeaway": STR,
    "eli5": STR,
    "key_aspects": arr(obj({"title": STR, "intuition": STR, "body": STR})),
    "gists": arr(obj({"caption": STR, "body": STR})),
    "open_problems": arr(obj({"id": STR, "question": STR, "why": STR})),
    "references": arr(obj({
        "id": STR,
        "title": STR,
        "authors": STR,
        "year": INT_OR_NULL,
        "arxiv": STR_OR_NULL,
    })),
    "experiment": obj({
        "title": STR,
        "hypothesis": STR,
        "steps": arr(obj({"id": STR, "text": STR})),
    }),
    "connections": arr(STR),
    "model_comparison": NULL,
})

# ── Examiner: MCQ generation ─────────────────────────────────────────
MCQ_SCHEMA = obj({
    "questions": arr(obj({
        "sub_concept": STR,
        "dimension": STR,
        "question": STR,
        "options": arr(obj({
            "id": STR,
            "text": STR,
            "is_correct": BOOL,
            "explanation": STR,
        })),
    })),
})

# ── Research directions ──────────────────────────────────────────────
RESEARCH_DIRECTIONS_SCHEMA = obj({
    "directions": arr(obj({
        "title": STR,
        "abstract_seed": STR,
        "builds_on": arr(STR),
        "gap_addressed": STR,
        "methodology_hint": STR,
        "difficulty": STR,
        "related_work_topics": arr(STR),
    })),
})

# ── Paper scaffold ───────────────────────────────────────────────────
PAPER_SCAFFOLD_SCHEMA = obj({
    "title": STR,
    "abstract": STR,
    "sections": arr(obj({
        "heading": STR,
        "purpose": STR,
        "key_points": arr(STR),
        "source_topics": arr(STR),
    })),
    "key_arguments": arr(STR),
    "evaluation_strategy": STR,
    "potential_venues": arr(STR),
})
