"""Custom FastAPI routes for the Gurukul knowledge graph API.

These run alongside the MLflow AgentServer's /responses endpoint.
The frontend uses these for graph state, topic content, exploration
triggers, annotation persistence, and Socratic challenge sessions.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any

from agents import Agent, ModelSettings, Runner
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, Field

import agent_server.schemas as _schemas
from agent_server.db import GurukuDB
from agent_server.guardrails import (
    sanitize_payload,
    validate_claims,
    validate_examiner_output,
    validate_references,
    validate_structure,
)
from agent_server.prompts import EXAMINER_SYSTEM
from agent_server.sse import broadcast, sse_endpoint
from agent_server.utils import extract_json

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api")

db = GurukuDB()

EXAMINER_MODEL = os.getenv("STUDENT_MODEL", "")
_active_exploration_task: asyncio.Task | None = None


async def _cancel_pending_topics() -> int:
    graph = await db.get_graph_state()
    cancelled = 0
    for node in graph.get("nodes", {}).values():
        if node.get("status") in {"queued", "generating"}:
            await db.update_topic_status(node["id"], "failed", "Cancelled by user")
            broadcast("status", {"id": node["id"], "status": "failed", "error": "Cancelled by user"})
            cancelled += 1
    return cancelled


# ── SSE ─────────────────────────────────────────────────────────────

router.add_api_route("/events", sse_endpoint, methods=["GET"])


# ── Graph state ─────────────────────────────────────────────────────

@router.get("/tree")
async def get_graph():
    return await db.get_graph_state()


# ── Topic content ───────────────────────────────────────────────────

@router.get("/topic/{topic_id}")
async def get_topic(topic_id: str):
    topic = await db.get_topic(topic_id)
    if not topic:
        raise HTTPException(404, "Not found")

    if topic["status"] != "done":
        return {"status": topic["status"], "error": topic.get("error")}

    node = {
        "id": topic["id"],
        "title": topic["title"],
        "category": topic["category"],
        "status": topic["status"],
        "isComparison": topic["is_comparison"],
        "rationale": topic["rationale"],
        "position": topic["position"],
        "error": topic.get("error"),
    }

    edges = await db.get_edges_for_topic(topic_id)
    node["connectsTo"] = edges

    return {"node": node, "payload": topic.get("payload")}


# ── Explore ─────────────────────────────────────────────────────────

class ExploreRequest(BaseModel):
    seed: str = Field(..., min_length=2)
    parentId: str | None = None


@router.post("/explore")
async def explore(req: ExploreRequest):
    """Trigger exploration via the agent's decompose + generate functions."""
    from agent_server.agent import do_decompose, do_generate_all_queued
    global _active_exploration_task

    if _active_exploration_task and not _active_exploration_task.done():
        raise HTTPException(409, "An exploration is already running. Stop it before starting another.")

    broadcast("thought", {
        "step": "explore_start",
        "message": f"Starting exploration: '{req.seed}'...",
    })

    async def run_exploration():
        global _active_exploration_task
        try:
            await do_decompose(seed=req.seed, parent_id=req.parentId)
            await do_generate_all_queued()
        except asyncio.CancelledError:
            logger.info("Exploration cancelled for seed %r", req.seed)
            await _cancel_pending_topics()
            broadcast("thought", {
                "step": "explore_cancelled",
                "message": "Exploration stopped by user.",
                "parentId": req.parentId,
            })
            broadcast("explore:done", {"parentId": req.parentId, "cancelled": True})
            raise
        except Exception as e:
            logger.error("Explore error: %s", e, exc_info=True)
            broadcast("error", {
                "message": f"Explore failed: {str(e)[:300]}",
                "parentId": req.parentId,
            })
        finally:
            if asyncio.current_task() is _active_exploration_task:
                _active_exploration_task = None

    _active_exploration_task = asyncio.create_task(run_exploration())
    return {"ok": True, "seed": req.seed, "parentId": req.parentId}


@router.post("/explore/cancel")
async def cancel_explore():
    """Stop the active exploration/generation run, if one is running."""
    global _active_exploration_task
    if not _active_exploration_task or _active_exploration_task.done():
        cancelled_topics = await _cancel_pending_topics()
        if cancelled_topics:
            broadcast("thought", {
                "step": "explore_cancelled",
                "message": f"Stopped {cancelled_topics} queued/generating topic(s).",
            })
            broadcast("explore:done", {"cancelled": True})
            return {"ok": True, "cancelled": True, "topics": cancelled_topics}
        return {"ok": True, "cancelled": False, "message": "No active exploration"}
    _active_exploration_task.cancel()
    broadcast("thought", {
        "step": "explore_cancel_requested",
        "message": "Stopping active exploration...",
    })
    return {"ok": True, "cancelled": True}


# ── Reset ───────────────────────────────────────────────────────────

@router.post("/reset")
async def reset():
    await db.reset()
    broadcast("reset", {})
    return {"ok": True}


# ── Annotations ─────────────────────────────────────────────────────

@router.get("/annotations")
async def get_annotations():
    return await db.get_annotations()


class AnnotationUpdate(BaseModel):
    topic_id: str
    annotation_type: str
    data: dict


@router.put("/annotations")
async def put_annotation(req: AnnotationUpdate):
    await db.upsert_annotation(req.topic_id, req.annotation_type, req.data)
    broadcast("annotation", {
        "topicId": req.topic_id,
        "type": req.annotation_type,
        "data": req.data,
    })
    return {"ok": True}


# ── Learning path ───────────────────────────────────────────────────

@router.get("/path/{from_id}/{to_id}")
async def get_path(from_id: str, to_id: str):
    path = await db.find_prerequisite_path(from_id, to_id)
    if path is None:
        return {"path": None, "message": "No prerequisite path found"}
    return {"path": path}


# ── Learning summary ───────────────────────────────────────────────

@router.get("/learning/summary")
async def learning_summary():
    """Return confidence distribution and gap analysis."""
    graph = await db.get_graph_state()
    annotations = await db.get_annotations()
    nodes = list(graph["nodes"].values())

    confidence_map: dict[str, int] = {}
    for key, data in annotations.items():
        if ":confidence" in key:
            topic_id = key.split(":confidence")[0]
            confidence_map[topic_id] = data.get("level", 0) if isinstance(data, dict) else 0

    total = len(nodes)
    rated = sum(1 for n in nodes if confidence_map.get(n["id"], 0) > 0)
    strong = sum(1 for n in nodes if confidence_map.get(n["id"], 0) >= 4)

    by_category: dict[str, dict] = {}
    for n in nodes:
        cat = n["category"]
        if cat not in by_category:
            by_category[cat] = {"total": 0, "rated": 0, "avg_confidence": 0, "sum_confidence": 0}
        by_category[cat]["total"] += 1
        c = confidence_map.get(n["id"], 0)
        if c > 0:
            by_category[cat]["rated"] += 1
            by_category[cat]["sum_confidence"] += c
    for cat_data in by_category.values():
        if cat_data["rated"] > 0:
            cat_data["avg_confidence"] = round(cat_data["sum_confidence"] / cat_data["rated"], 1)
        del cat_data["sum_confidence"]

    # Find gaps: topics with low confidence that have prerequisite edges to others
    gaps = []
    for n in nodes:
        conf = confidence_map.get(n["id"], 0)
        if 0 < conf < 3:
            gaps.append({"id": n["id"], "title": n["title"], "confidence": conf, "category": n["category"]})

    return {
        "total": total,
        "rated": rated,
        "strong": strong,
        "progress_pct": round((strong / total) * 100, 1) if total > 0 else 0,
        "by_category": by_category,
        "gaps": sorted(gaps, key=lambda g: g["confidence"]),
        "confidence_map": confidence_map,
    }


# ── MCQ Challenge ────────────────────────────────────────────────────

MCQ_MODEL = os.getenv("STUDENT_MODEL", "")


@router.post("/challenge/{topic_id}/mcq/generate")
async def generate_mcq(topic_id: str):
    """Generate MCQ questions for a topic from its content."""
    from agent_server.prompts import MCQ_GENERATION_SYSTEM

    topic = await db.get_topic(topic_id)
    if not topic or topic["status"] != "done":
        raise HTTPException(404, "Topic not found or not ready")

    payload = topic.get("payload", {}) or {}
    summary = payload.get("summary", "")
    aspects = payload.get("key_aspects", [])
    open_probs = payload.get("open_problems", [])

    content_text = f"Topic: {topic['title']}\n\nSummary: {summary}\n\n"
    for a in aspects:
        content_text += f"Key Aspect - {a.get('title', '')}: {a.get('body', '')}\n\n"
    for op in open_probs:
        content_text += f"Open Problem: {op.get('question', '')} — {op.get('why', '')}\n\n"

    messages = [
        {"role": "system", "content": MCQ_GENERATION_SYSTEM},
        {"role": "user", "content": f"Generate 5 MCQ questions for this topic:\n\n{content_text}"},
    ]

    examiner = Agent(
        name="MCQ-Generator",
        instructions=MCQ_GENERATION_SYSTEM,
        model=MCQ_MODEL,
        model_settings=ModelSettings(extra_body={
            "response_format": _schemas.response_format("mcq_questions", _schemas.MCQ_SCHEMA),
        }),
    )

    t0 = time.monotonic()
    result = await Runner.run(examiner, input=[messages[-1]])
    ms = int((time.monotonic() - t0) * 1000)
    logger.info("MCQ generation for '%s' completed in %dms", topic["title"][:40], ms)

    response_text = ""
    for item in result.new_items:
        if hasattr(item, "text"):
            response_text = item.text
            break
        raw = item.to_input_item()
        if isinstance(raw, dict) and raw.get("type") == "message":
            for c in raw.get("content", []):
                if isinstance(c, dict) and c.get("type") == "output_text":
                    response_text = c.get("text", "")
                    break

    parsed = extract_json(response_text)
    questions = parsed.get("questions", [])

    if not questions:
        raise HTTPException(500, "Failed to generate MCQ questions")

    await db.delete_mcq_questions(topic_id)
    ids = await db.store_mcq_questions(topic_id, questions)

    return {
        "ok": True,
        "topic_id": topic_id,
        "count": len(ids),
        "question_ids": ids,
        "generation_ms": ms,
    }


@router.get("/challenge/{topic_id}/mcq/questions")
async def get_mcq_questions(topic_id: str, reveal: bool = False):
    """Get existing MCQ questions for a topic.

    By default the correct answer is stripped. Pass ``?reveal=true`` to also
    return ``correct_option`` per question — a TESTING AID for moving through
    assessments to reach the Research flow. (Safe to remove later: delete the
    ``reveal`` param and the ``correct_option`` block below.)
    """
    questions = await db.get_mcq_questions(topic_id)
    if not questions:
        return {"questions": [], "count": 0}

    safe_questions = []
    for q in questions:
        opts = q.get("options", [])
        if isinstance(opts, str):
            opts = json.loads(opts)
        safe_opts = [
            {"id": o["id"], "text": o["text"]}
            for o in opts
        ]
        item = {
            "id": q["id"],
            "sub_concept": q["sub_concept"],
            "dimension": q["dimension"],
            "question": q["question"],
            "options": safe_opts,
        }
        if reveal:  # TESTING AID — reveals the answer; remove when no longer needed
            correct = next((o["id"] for o in opts if o.get("is_correct")), None)
            item["correct_option"] = correct
        safe_questions.append(item)

    return {"questions": safe_questions, "count": len(safe_questions)}


class MCQAnswerRequest(BaseModel):
    question_id: int
    selected: str
    time_ms: int | None = None


@router.post("/challenge/{topic_id}/mcq/answer")
async def submit_mcq_answer(topic_id: str, req: MCQAnswerRequest):
    """Submit an MCQ answer. Returns correctness, explanation, and misconception tracking."""
    questions = await db.get_mcq_questions(topic_id)
    question = None
    for q in questions:
        if q["id"] == req.question_id:
            question = q
            break

    if not question:
        raise HTTPException(404, "Question not found")

    opts = question.get("options", [])
    if isinstance(opts, str):
        opts = json.loads(opts)

    correct_opt = next((o for o in opts if o.get("is_correct")), None)
    selected_opt = next((o for o in opts if o["id"] == req.selected), None)

    if not selected_opt:
        raise HTTPException(400, f"Invalid option: {req.selected}")

    is_correct = selected_opt.get("is_correct", False)

    await db.store_mcq_response(
        user_id="default",
        question_id=req.question_id,
        topic_id=topic_id,
        selected=req.selected,
        is_correct=is_correct,
        time_ms=req.time_ms,
    )

    if not is_correct and correct_opt:
        misconception_claim = selected_opt.get("explanation", selected_opt["text"])
        correction = correct_opt.get("explanation", correct_opt["text"])
        await db.track_misconception(
            user_id="default",
            topic_id=topic_id,
            sub_concept=question["sub_concept"],
            claim=misconception_claim,
            correction=correction,
            severity="conceptual" if question["dimension"] in ("mechanism", "tradeoff") else "minor",
        )

    return {
        "is_correct": is_correct,
        "correct_option": correct_opt["id"] if correct_opt else None,
        "explanations": {o["id"]: o.get("explanation", "") for o in opts},
        "dimension": question["dimension"],
        "sub_concept": question["sub_concept"],
    }


@router.get("/challenge/{topic_id}/mcq/results")
async def get_mcq_results(topic_id: str):
    """Get MCQ performance summary for a topic."""
    responses = await db.get_mcq_responses("default", topic_id)
    if not responses:
        return {"attempted": False}

    total = len(responses)
    correct = sum(1 for r in responses if r["is_correct"])
    by_dimension: dict[str, dict] = {}
    for r in responses:
        dim = r.get("dimension", "unknown")
        if dim not in by_dimension:
            by_dimension[dim] = {"total": 0, "correct": 0}
        by_dimension[dim]["total"] += 1
        by_dimension[dim]["correct"] += 1 if r["is_correct"] else 0

    return {
        "attempted": True,
        "total": total,
        "correct": correct,
        "score_pct": round((correct / total) * 100) if total > 0 else 0,
        "by_dimension": by_dimension,
        "ready_for_socratic": (correct / total) >= 0.6 if total > 0 else False,
    }


@router.get("/misconceptions")
async def get_misconceptions():
    """Get all unresolved misconceptions."""
    misconceptions = await db.get_misconceptions("default", resolved=False)
    return {"misconceptions": misconceptions, "count": len(misconceptions)}


# ── Socratic Challenge ("Challenge Me") ─────────────────────────────

MAX_ROUNDS = 5

class ChallengeStartRequest(BaseModel):
    topic_id: str

class ChallengeAnswerRequest(BaseModel):
    answer: str


async def _run_examiner(messages: list[dict]) -> dict:
    """Run the Examiner agent and parse its JSON response."""
    examiner = Agent(
        name="Examiner",
        instructions=EXAMINER_SYSTEM,
        model=EXAMINER_MODEL,
    )
    t0 = time.monotonic()
    result = await Runner.run(examiner, input=messages)
    ms = int((time.monotonic() - t0) * 1000)
    logger.info("Examiner responded in %dms", ms)

    response_text = ""
    for item in result.new_items:
        if hasattr(item, "text"):
            response_text = item.text
            break
        raw = item.to_input_item()
        if isinstance(raw, dict) and raw.get("type") == "message":
            for c in raw.get("content", []):
                if isinstance(c, dict) and c.get("type") == "output_text":
                    response_text = c.get("text", "")
                    break
    return extract_json(response_text)


@router.post("/challenge/{topic_id}/start")
async def challenge_start(topic_id: str):
    """Start a new Socratic challenge session for a topic."""
    topic = await db.get_topic(topic_id)
    if not topic or topic["status"] != "done":
        raise HTTPException(404, "Topic not found or not ready")

    payload = topic.get("payload", {}) or {}
    summary = payload.get("summary", topic["title"])
    aspects = payload.get("key_aspects", [])
    aspect_text = "\n".join(
        f"- {a.get('title', '')}: {a.get('body', '')[:200]}" for a in aspects[:3]
    )

    history = await db.get_challenge_history(topic_id)
    prior_questions = []
    for h in history[:3]:
        session = await db.get_challenge_session(h["id"])
        if session and session.get("rounds"):
            rounds = json.loads(session["rounds"]) if isinstance(session["rounds"], str) else session["rounds"]
            prior_questions.extend(r.get("question", "") for r in rounds)

    avoid_text = ""
    if prior_questions:
        avoid_text = f"\n\nAvoid repeating these questions from prior sessions:\n" + "\n".join(f"- {q}" for q in prior_questions[:6])

    messages = [
        {"role": "user", "content": (
            f"QUESTION MODE\n\n"
            f"Topic: \"{topic['title']}\"\n"
            f"Category: {topic['category']}\n"
            f"Summary: {summary}\n"
            f"Key aspects:\n{aspect_text}\n"
            f"Round: 1 of {MAX_ROUNDS} (first question — start at structural level)\n"
            f"{avoid_text}\n\n"
            f"Generate the first question. Return strict JSON."
        )}
    ]

    parsed = await _run_examiner(messages)
    question = parsed.get("question", "Explain the core mechanism behind this topic and why it matters.")
    mode = parsed.get("mode", "explain")

    session_id = await db.create_challenge_session(topic_id)

    broadcast("thought", {
        "step": "challenge_start",
        "topic_id": topic_id,
        "message": f"Examiner started a challenge on '{topic['title']}'",
    })

    return {
        "session_id": session_id,
        "question": question,
        "mode": mode,
        "round": 1,
        "max_rounds": MAX_ROUNDS,
    }


@router.post("/challenge/{session_id}/answer")
async def challenge_answer(session_id: int, req: ChallengeAnswerRequest):
    """Submit an answer to a challenge and get evaluation + follow-up."""
    session = await db.get_challenge_session(session_id)
    if not session:
        raise HTTPException(404, "Challenge session not found")
    if session.get("completed_at"):
        raise HTTPException(400, "Session already completed")

    topic = await db.get_topic(session["topic_id"])
    if not topic:
        raise HTTPException(404, "Topic not found")

    payload = topic.get("payload", {}) or {}
    summary = payload.get("summary", topic["title"])
    aspects = payload.get("key_aspects", [])
    aspect_text = "\n".join(
        f"- {a.get('title', '')}: {a.get('body', '')[:300]}" for a in aspects[:3]
    )

    rounds = json.loads(session["rounds"]) if isinstance(session["rounds"], str) else session["rounds"]
    current_round = len(rounds) + 1
    is_final = current_round >= MAX_ROUNDS

    # Build conversation history for context
    conversation = []
    for r in rounds:
        conversation.append(f"Q ({r.get('mode', '?')}): {r.get('question', '')}")
        if r.get("answer"):
            conversation.append(f"A: {r['answer']}")
        if r.get("evaluation", {}).get("feedback"):
            conversation.append(f"Feedback: {r['evaluation']['feedback']}")

    last_question = rounds[-1]["question"] if rounds else "the first question"
    last_mode = rounds[-1].get("mode", "explain") if rounds else "explain"

    conv_text = "\n".join(conversation) if conversation else "(first round)"
    final_note = " (FINAL ROUND — do NOT include follow_up_question)" if is_final else ""

    messages = [
        {"role": "user", "content": (
            f"EVALUATE MODE\n\n"
            f"Topic: \"{topic['title']}\"\n"
            f"Summary: {summary}\n"
            f"Key aspects:\n{aspect_text}\n\n"
            f"Conversation so far:\n{conv_text}\n\n"
            f"Latest question ({last_mode}): {last_question}\n"
            f"Learner's answer: {req.answer}\n\n"
            f"Round: {current_round} of {MAX_ROUNDS}{final_note}\n\n"
            f"Evaluate the answer. Return strict JSON."
        )}
    ]

    parsed = await _run_examiner(messages)

    guardrail_issues = validate_examiner_output(parsed)
    if guardrail_issues:
        logger.info(
            "Examiner guardrails: %d issues — %s",
            len(guardrail_issues),
            [i["message"] for i in guardrail_issues],
        )

    accuracy = max(0, min(3, int(parsed.get("accuracy", 1))))
    depth = max(0, min(3, int(parsed.get("depth", 0))))
    reasoning = max(0, min(2, int(parsed.get("reasoning", 0))))
    level = parsed.get("level", _compute_level(accuracy, depth, reasoning))
    feedback = parsed.get("feedback", "")

    round_data = {
        "question": last_question,
        "mode": last_mode,
        "answer": req.answer,
        "evaluation": {
            "accuracy": accuracy,
            "depth": depth,
            "reasoning": reasoning,
            "level": level,
            "feedback": feedback,
        },
    }
    await db.append_challenge_round(session_id, round_data)

    follow_up = parsed.get("follow_up_question") if not is_final else None
    follow_up_mode = parsed.get("follow_up_mode", "explain") if follow_up else None

    # If we have a follow-up, store the question for the next round
    if follow_up:
        next_round = {
            "question": follow_up,
            "mode": follow_up_mode or "explain",
        }
        await db.append_challenge_round(session_id, next_round)

    # Complete session if final round or no follow-up
    completed = is_final or not follow_up
    final_level = None
    final_scores = None

    if completed:
        # Compute aggregate scores from all evaluated rounds
        all_rounds = rounds + [round_data]
        evaluated = [r for r in all_rounds if r.get("evaluation")]
        if evaluated:
            avg_acc = sum(r["evaluation"]["accuracy"] for r in evaluated) / len(evaluated)
            avg_dep = sum(r["evaluation"]["depth"] for r in evaluated) / len(evaluated)
            avg_rea = sum(r["evaluation"]["reasoning"] for r in evaluated) / len(evaluated)
            final_scores = {
                "accuracy": round(avg_acc, 1),
                "depth": round(avg_dep, 1),
                "reasoning": round(avg_rea, 1),
            }
            final_level = _compute_level(avg_acc, avg_dep, avg_rea)
        else:
            final_level = level
            final_scores = {"accuracy": accuracy, "depth": depth, "reasoning": reasoning}

        await db.complete_challenge_session(session_id, final_level, final_scores)

        broadcast("thought", {
            "step": "challenge_done",
            "topic_id": session["topic_id"],
            "message": f"Challenge on '{topic['title']}' completed: {final_level}",
        })

    return {
        "round": current_round,
        "max_rounds": MAX_ROUNDS,
        "evaluation": {
            "accuracy": accuracy,
            "depth": depth,
            "reasoning": reasoning,
            "level": level,
            "feedback": feedback,
        },
        "follow_up_question": follow_up,
        "follow_up_mode": follow_up_mode,
        "completed": completed,
        "final_level": final_level,
        "final_scores": final_scores,
    }


def _compute_level(accuracy: float, depth: float, reasoning: float) -> str:
    if accuracy < 2 or depth < 1:
        return "surface"
    if accuracy >= 3 and depth >= 3 and reasoning >= 2:
        return "creative"
    if accuracy >= 2 and depth >= 2 and reasoning >= 1:
        return "deep"
    return "structural"


@router.get("/challenge/{topic_id}/history")
async def challenge_history(topic_id: str):
    history = await db.get_challenge_history(topic_id)
    return {"sessions": history}


@router.get("/understanding")
async def get_understanding():
    """Return the understanding map: topic_id -> {level, scores, assessed_at}."""
    return await db.get_understanding_map()


# ── Quality Learnings ────────────────────────────────────────────────

@router.get("/quality/learnings")
async def get_quality_learnings():
    """Return all active quality learnings that are applied to generation."""
    learnings = await db.get_active_quality_learnings()
    return {"learnings": learnings, "count": len(learnings)}


# ── Content Quality ──────────────────────────────────────────────────

QUALITY_DIMENSIONS = {
    # Heuristic dimensions
    "grounding": {
        "label": "Grounding",
        "description": "Are claims attributed to sources and properly hedged?",
        "source": "heuristic",
    },
    "references": {
        "label": "References",
        "description": "Are citations real, complete, and sufficient?",
        "source": "heuristic",
    },
    "structure": {
        "label": "Structure",
        "description": "Is content structurally complete with all required fields?",
        "source": "heuristic",
    },
    "epistemic": {
        "label": "Epistemic Markers",
        "description": "Does content signal confidence levels for claims?",
        "source": "heuristic",
    },
    # LLM judge sub-dimensions (each scored independently)
    "factual_accuracy": {
        "label": "Factual Accuracy",
        "description": "Are claims verifiable and correct against known literature?",
        "source": "llm_judge",
    },
    "comprehensiveness": {
        "label": "Comprehensiveness",
        "description": "Does it cover all key aspects, trade-offs, and limitations?",
        "source": "llm_judge",
    },
    "depth": {
        "label": "Technical Depth",
        "description": "Does it explain mechanisms (why, not just what), edge cases, and failure modes?",
        "source": "llm_judge",
    },
    "research_readiness": {
        "label": "Research Readiness",
        "description": "Could someone write a related-works section or identify gaps from this?",
        "source": "llm_judge",
    },
}


def _score_grounding(payload: dict) -> tuple[float, list[str]]:
    """Score grounding — checks claims are attributed, not just absent."""
    import re
    prose = _extract_prose(payload)
    refs = payload.get("references", [])

    attribution_patterns = [
        r"\([A-Z][a-z]+ et al\.?,? \d{4}\)",  # (Author et al., 2024)
        r"[A-Z][a-z]+ et al\.? \(\d{4}\)",     # Author et al. (2024)
        r"proposed (?:by|in) .{5,40}\(\d{4}\)", # proposed by X (2024)
        r"introduced (?:by|in) .{5,40}\(\d{4}\)",
        r"according to .{5,40}\(\d{4}\)",
        r"(?:as )?(?:shown|demonstrated|described) (?:by|in) .{5,40}",
        r"arXiv:\d{4}\.\d{4,5}",
    ]
    source_markers = [
        r"well-established", r"widely reported", r"widely adopted",
        r"well-documented", r"standard (?:approach|technique|method)",
        r"commonly used", r"widely recognized",
    ]

    claim_patterns = [
        (r"achieves? \d+(\.\d+)?%", "benchmark claim"),
        (r"has \d+[BMT]\b.{0,10}parameters?", "parameter count"),
        (r"outperforms? .{3,30} by \d+", "performance comparison"),
        (r"trained on \d+[BMTGP]\b.{0,10}tokens?", "training data claim"),
        (r"reduces? .{3,30} by \d+%", "reduction claim"),
        (r"improves? .{3,30} by \d+%", "improvement claim"),
        (r"\d+x (?:faster|slower|better|larger)", "magnitude claim"),
    ]

    attr_count = sum(len(re.findall(p, prose, re.IGNORECASE)) for p in attribution_patterns)
    source_count = sum(len(re.findall(p, prose, re.IGNORECASE)) for p in source_markers)

    ungrounded = []
    for pattern, claim_type in claim_patterns:
        for m in re.finditer(pattern, prose, re.IGNORECASE):
            ctx = prose[max(0, m.start() - 120):m.end() + 120]
            has_attr = any(re.search(ap, ctx, re.IGNORECASE) for ap in attribution_patterns)
            has_source = any(re.search(sp, ctx, re.IGNORECASE) for sp in source_markers)
            hedged = re.search(
                r"approximately|roughly|around|estimated|speculative|reportedly", ctx, re.IGNORECASE
            )
            if not has_attr and not has_source and not hedged:
                ungrounded.append((m.group(), claim_type))

    suggestions = []

    has_refs = len(refs) > 0
    attr_score = min(1.0, (attr_count + source_count * 0.5) / 5.0)
    claim_score = 1.0 if not ungrounded else max(0.2, 1.0 - len(ungrounded) * 0.15)
    ref_bonus = 0.1 if has_refs else 0.0

    score = attr_score * 0.5 + claim_score * 0.4 + ref_bonus

    if attr_count == 0 and source_count == 0:
        suggestions.append(
            "No inline citations found (e.g. 'Vaswani et al., 2017'). "
            "Research content should attribute key ideas to their source papers."
        )
    for claim, ctype in ungrounded[:3]:
        suggestions.append(
            f"Unattributed {ctype}: \"{claim}\". Add source or hedge with 'approximately'."
        )
    if not has_refs:
        suggestions.append("No references section. Add foundational papers to ground the content.")

    return min(1.0, score), suggestions[:5]


async def _score_references_async(payload: dict) -> tuple[float, list[str], dict]:
    """Score references using batched arXiv verification for ground truth."""
    from agent_server.arxiv import verify_references_batch

    refs = payload.get("references", [])
    suggestions = []
    detail: dict = {"total": 0, "verified": 0, "invalid": 0, "refs": []}

    if not refs:
        suggestions.append(
            "No references provided. Research-grade content must cite foundational "
            "papers. Regenerate to include at least 3-5 key references."
        )
        return 0.2, suggestions, detail

    detail["total"] = len(refs)

    if len(refs) < 3:
        suggestions.append(
            f"Only {len(refs)} reference(s). Aim for 3-5+ citations."
        )

    results = await verify_references_batch(refs)

    verified = 0
    invalid = 0
    ref_results = []

    for ref, result in zip(refs, results):
        title = ref.get("title", "")
        arxiv_id = ref.get("arxiv")

        entry = {
            "claimed_title": title[:60],
            "claimed_arxiv": arxiv_id,
            "verified": result.found,
            "actual_title": result.paper.title[:60] if result.paper else None,
            "actual_id": result.paper.arxiv_id if result.paper else None,
            "title_match": round(result.title_match, 2),
            "issues": result.issues,
        }
        ref_results.append(entry)

        if result.found:
            verified += 1
            if result.title_match < 0.4 and result.paper:
                suggestions.append(
                    f"'{title[:30]}…' — arXiv ID exists but title mismatch. "
                    f"Actual: '{result.paper.title[:40]}…'"
                )
        else:
            invalid += 1
            if result.issues:
                suggestions.append(result.issues[0])

    detail["verified"] = verified
    detail["invalid"] = invalid
    detail["refs"] = ref_results

    verification_rate = verified / max(1, len(refs))
    quantity_factor = min(1.0, len(refs) / 3.0)
    score = verification_rate * 0.6 + quantity_factor * 0.2 + (0.2 if invalid == 0 else 0.0)

    if verified == 0 and len(refs) > 0:
        suggestions.insert(0,
            f"None of the {len(refs)} cited papers could be verified on arXiv. "
            f"Citations may be hallucinated."
        )

    return score, suggestions[:5], detail


def _score_references(payload: dict) -> tuple[float, list[str]]:
    """Sync wrapper — returns heuristic score. Use _score_references_async for full verification."""
    import re
    refs = payload.get("references", [])

    if not refs:
        return 0.2, ["No references. Research content must cite foundational papers."]

    arxiv_re = re.compile(r"^\d{4}\.\d{4,5}(v\d+)?$")
    bad = sum(1 for r in refs if r.get("arxiv") and not arxiv_re.match(str(r["arxiv"])))
    no_title = sum(1 for r in refs if not r.get("title") or len(r.get("title", "")) < 10)

    completeness = max(0, len(refs) - bad - no_title) / max(1, len(refs))
    quantity = min(1.0, len(refs) / 3.0)
    return completeness * 0.7 + quantity * 0.3, []


def _score_structure(payload: dict) -> tuple[float, list[str]]:
    """Score structural completeness and content depth, not just field presence."""
    suggestions = []
    total_points = 0.0
    earned_points = 0.0

    required_fields = {
        "summary": ("Summary", 50),
        "takeaway": ("Key Takeaway", 30),
        "key_aspects": ("Key Aspects", None),
        "eli5": ("ELI5 Explanation", 30),
    }
    for field, (label, min_chars) in required_fields.items():
        total_points += 1.0
        val = payload.get(field)
        if not val:
            suggestions.append(f"Missing '{label}'. Regenerate to fix.")
        elif isinstance(val, str) and min_chars and len(val.strip()) < min_chars:
            earned_points += 0.5
            suggestions.append(f"'{label}' is too short ({len(val)} chars). Expand for depth.")
        else:
            earned_points += 1.0

    aspects = payload.get("key_aspects", [])
    total_points += 1.0
    if isinstance(aspects, list):
        if len(aspects) >= 3:
            earned_points += 1.0
        elif len(aspects) >= 2:
            earned_points += 0.7
            suggestions.append("Only 2 key aspects. Aim for 3+ to cover the topic adequately.")
        elif len(aspects) == 1:
            earned_points += 0.3
            suggestions.append("Only 1 key aspect. Content lacks depth.")

    total_points += 1.0
    experiment = payload.get("experiment")
    if experiment:
        exp_text = json.dumps(experiment) if isinstance(experiment, dict) else str(experiment)
        if len(exp_text) > 100:
            earned_points += 1.0
        else:
            earned_points += 0.5
            suggestions.append("Experiment section is thin. Add concrete steps or code.")
    else:
        suggestions.append("No hands-on experiment. Practical exercises deepen understanding.")

    total_points += 1.0
    refs = payload.get("references", [])
    if len(refs) >= 3:
        earned_points += 1.0
    elif len(refs) >= 1:
        earned_points += 0.5
        suggestions.append(f"Only {len(refs)} reference(s). Research content needs 3+ citations.")
    else:
        suggestions.append("No references. Content cannot be verified without citations.")

    prose = _extract_prose(payload)
    total_points += 1.0
    word_count = len(prose.split())
    if word_count >= 800:
        earned_points += 1.0
    elif word_count >= 400:
        earned_points += 0.6
        suggestions.append(f"Content is only {word_count} words. Aim for 800+ for research depth.")
    else:
        earned_points += 0.3
        suggestions.append(f"Content is very short ({word_count} words). Needs significant expansion.")

    score = earned_points / max(1, total_points)
    return score, suggestions[:5]


def _extract_prose(payload: dict) -> str:
    """Pull actual prose text from payload, ignoring JSON structural tokens."""
    parts: list[str] = []
    def _walk(obj: object) -> None:
        if isinstance(obj, str):
            parts.append(obj)
        elif isinstance(obj, list):
            for item in obj:
                _walk(item)
        elif isinstance(obj, dict):
            for v in obj.values():
                _walk(v)
    _walk(payload)
    return " ".join(parts)


def _score_epistemic(payload: dict) -> tuple[float, list[str]]:
    """Score epistemic marker density in prose text.

    Extracts actual prose (not JSON keys/brackets) and counts three tiers
    of confidence-signalling language that educational/research content
    naturally produces.
    """
    import re

    prose = _extract_prose(payload)
    lower = prose.lower()
    word_count = len(prose.split())

    strong_markers = [
        r"well[- ]established", r"widely reported", r"well[- ]documented",
        r"not (?:officially |publicly )?confirmed", r"undisclosed",
        r"speculative", r"the (?:exact )?details .*?(?:not|haven't) been .*?disclosed",
        r"\bseminal\b", r"\bpioneering\b", r"\binfluential\b",
        r"\bstate[- ]of[- ]the[- ]art\b",
        r"open (?:question|problem|challenge|area)",
    ]
    hedge_markers = [
        r"approximately", r"on the order of", r"it is believed",
        r"evidence suggests?", r"widely believed", r"is thought to",
        r"reportedly", r"hypothesized", r"conjectured",
        r"(?:is |are )?(?:generally|typically|commonly) (?:considered|regarded|viewed)",
        r"remains? (?:open|unclear|uncertain|debated)",
        r"preliminary (?:results?|evidence|findings)",
        r"emerging (?:research|evidence|work)",
        r"\btrade[- ]?offs?\b",
        r"\blimit(?:ation|ed|s)\b",
        r"\bchallenging\b",
        r"\bnotably\b", r"\bsignificantly\b",
        r"\bcritical(?:ly)?\b", r"\bfundamental(?:ly)?\b",
    ]
    natural_hedges = [
        r"\barguably\b", r"\blikely\b", r"\bprobably\b",
        r"\bmay (?:be|have|enable|allow|improve|reduce)\b",
        r"\bmight (?:be|have|enable|allow)\b",
        r"\bcould (?:be|have|enable|allow)\b",
        r"\bsuggests? that\b", r"\bappears? to\b",
        r"\bin practice\b", r"\bempirically\b",
        r"according to", r"proposed (?:by|in)\b", r"demonstrated (?:by|in)\b",
        r"\btypically\b", r"\bgenerally\b", r"\busually\b",
        r"\boften\b", r"\btends? to\b",
        r"\bhowever\b", r"\balthough\b", r"\bdespite\b",
        r"\bwhile\b(?=.*?,)",  # "while X, Y" contrast pattern
        r"\brecent(?:ly)?\b",
        r"\bwidely\b", r"\bestablished\b",
        r"\bproposed\b", r"\bintroduced\b",
        r"(?:such as|for (?:example|instance)|e\.?g\.?)\b",
    ]

    strong_count = sum(len(re.findall(p, lower)) for p in strong_markers)
    hedge_count = sum(len(re.findall(p, lower)) for p in hedge_markers)
    natural_count = sum(len(re.findall(p, lower)) for p in natural_hedges)

    weighted = strong_count * 1.0 + hedge_count * 0.7 + natural_count * 0.3
    density = weighted / max(1, word_count / 200)

    if density >= 3.0:
        score = 1.0
    elif density >= 2.2:
        score = 0.92
    elif density >= 1.5:
        score = 0.85
    elif density >= 1.0:
        score = 0.78
    elif density >= 0.6:
        score = 0.65
    elif weighted > 0:
        score = 0.4
    else:
        score = 0.1

    total = strong_count + hedge_count + natural_count
    suggestions = []
    if score < 0.7:
        suggestions.append(
            f"{total} epistemic signals in {word_count} prose words (density {density:.1f}/200w). "
            "Add qualifiers like 'well-established', 'widely reported', "
            "'speculative', or hedges like 'typically', 'suggests that'."
        )
    if score < 0.4:
        suggestions.append(
            "Content reads as if all claims are equally certain. "
            "Distinguish between verified facts and informed speculation."
        )
    return score, suggestions


JUDGE_DIMS = ("factual_accuracy", "comprehensiveness", "depth", "research_readiness")
JUDGE_SAMPLES = int(os.environ.get("JUDGE_SAMPLES", "3"))

# Strict structured-output schema for the judge. Databricks Foundation Model
# APIs enforce this during decoding (Claude supports json_schema), so every
# response is valid, complete JSON with all keys present and correctly typed —
# eliminating omitted dimensions, non-numeric scores, prose-wrapping, and
# mid-JSON truncation at the source rather than repairing them after the fact.
def _dim_schema(extra_key: str | None) -> dict:
    props = {"score": _schemas.INT, "reasoning": _schemas.STR}
    if extra_key:
        props[extra_key] = _schemas.arr(_schemas.STR)
    return _schemas.obj(props)


JUDGE_RESPONSE_FORMAT = _schemas.response_format(
    "content_quality_evaluation",
    _schemas.obj({
        "factual_accuracy": _dim_schema("issues"),
        "comprehensiveness": _dim_schema("gaps"),
        "depth": _dim_schema(None),
        "research_readiness": _dim_schema("missing_for_research"),
    }),
)


async def _judge_once(client, model, messages) -> dict | None:
    """Run one judge sample with strict structured outputs.

    Returns the parsed JSON dict, or None ONLY on a genuine infra failure
    (network/endpoint error). Because decoding is schema-constrained, a
    successful call always yields valid, complete JSON.
    """
    import asyncio as _aio
    for attempt in range(2):
        try:
            resp = await client.chat.completions.create(
                model=model,
                messages=messages,
                max_tokens=2000,
                response_format=JUDGE_RESPONSE_FORMAT,
            )
            raw = (resp.choices[0].message.content or "").strip()
            if raw:
                return json.loads(raw)
            logger.warning("Judge returned empty content (finish=%s)",
                           resp.choices[0].finish_reason if resp.choices else "?")
        except Exception as e:
            logger.warning("Judge sample failed: %s", e)
        if attempt < 1:
            await _aio.sleep(1.5 * (attempt + 1))
    return None


async def _score_llm_judge(title: str, payload: dict) -> dict[str, tuple[float | None, list[str]]]:
    """Run the LLM judge with self-consistency (median of N samples).

    Returns {dim_key: (score_0_to_1 | None, suggestions)}. Score is None when
    the judge could not produce a usable result — callers must treat None as
    "unscored", never as a real low score. Self-consistency reduces variance;
    a failed sample contributes nothing rather than a fabricated fallback.
    """
    import asyncio as _aio
    import statistics
    from agent_server.llm_client import async_databricks_openai
    from agent_server.prompts import JUDGE_SYSTEM

    client = async_databricks_openai()

    # The judge must evaluate the SAME content the reader sees — not a
    # truncated proxy. Earlier this view dropped gists/connections and capped
    # the serialized JSON at 5k chars, which routinely chopped off the tail
    # fields (open_problems, references) entirely. That made the judge blind to
    # the very signals depth/comprehensiveness/research-readiness measure. A
    # single topic is a few thousand tokens — far below the model's context —
    # so there is no cost/context reason to trim. We include every dimension-
    # relevant field and guard only against pathological size.
    content_view = {
        "summary": payload.get("summary") or "",
        "eli5": payload.get("eli5") or "",
        "takeaway": payload.get("takeaway") or "",
        "key_aspects": [
            {
                "title": a.get("title", ""),
                "intuition": a.get("intuition", ""),
                "body": a.get("body", ""),
            }
            for a in payload.get("key_aspects", []) if isinstance(a, dict)
        ],
        "gists": [
            {"caption": g.get("caption", ""), "body": g.get("body", "")}
            for g in payload.get("gists", []) if isinstance(g, dict)
        ],
        "open_problems": [
            {"question": o.get("question", ""), "why": o.get("why", "")}
            for o in payload.get("open_problems", []) if isinstance(o, dict)
        ],
        "references": [
            {
                "title": r.get("title", ""),
                "authors": r.get("authors", ""),
                "year": r.get("year"),
                "arxiv": r.get("arxiv"),
            }
            for r in payload.get("references", []) if isinstance(r, dict)
        ],
        "connections": payload.get("connections", []),
        "experiment": payload.get("experiment", {}),
    }
    mc = payload.get("model_comparison")
    if mc:
        content_view["model_comparison"] = mc
    # Safety bound only (≈ 10k tokens), never a content cut for normal topics.
    content_text = json.dumps(content_view, indent=2)[:40000]
    model = os.environ.get("TEACHER_MODEL", "databricks-claude-sonnet-4-6")
    user_msg = (
        f"Topic: {title}\n\nContent:\n{content_text}\n\n"
        "Respond with a JSON object only. No markdown fences, no explanation."
    )
    messages = [
        {"role": "system", "content": JUDGE_SYSTEM},
        {"role": "user", "content": user_msg},
    ]

    samples = await _aio.gather(*[
        _judge_once(client, model, messages) for _ in range(JUDGE_SAMPLES)
    ])
    valid = [s for s in samples if isinstance(s, dict)]

    if not valid:
        logger.warning("LLM judge: all %d samples failed for '%s'", JUDGE_SAMPLES, title)
        return {k: (None, ["LLM judge unavailable (all samples failed)"]) for k in JUDGE_DIMS}

    out: dict[str, tuple[float | None, list[str]]] = {}
    for dim_key in JUDGE_DIMS:
        raw_scores: list[float] = []
        best_sug: list[str] = []
        lowest = 101
        for s in valid:
            dd = s.get(dim_key, {})
            sc = dd.get("score")
            if isinstance(sc, (int, float)):
                raw_scores.append(float(sc))
                # Keep suggestions from the harshest sample (most informative).
                if sc < lowest:
                    lowest = sc
                    sug: list[str] = []
                    reasoning = dd.get("reasoning", "")
                    if reasoning and sc < 80:
                        sug.append(reasoning)
                    for issue in dd.get("issues", [])[:2]:
                        sug.append(f"Issue: {issue}")
                    for gap in dd.get("gaps", [])[:2]:
                        sug.append(f"Gap: {gap}")
                    for need in dd.get("missing_for_research", [])[:2]:
                        sug.append(f"For research: {need}")
                    best_sug = sug[:3]
        if raw_scores:
            out[dim_key] = (statistics.median(raw_scores) / 100.0, best_sug)
        else:
            out[dim_key] = (None, ["LLM judge did not score this dimension"])

    return out


ALL_DIM_KEYS = list(QUALITY_DIMENSIONS.keys())

# Weights for the overall score — heuristics + LLM judge sub-dimensions
DIM_WEIGHTS = {
    "grounding": 0.15,
    "references": 0.10,
    "structure": 0.10,
    "epistemic": 0.10,
    "factual_accuracy": 0.15,
    "comprehensiveness": 0.13,
    "depth": 0.15,
    "research_readiness": 0.12,
}


async def _score_all_dimensions(title: str, payload: dict) -> dict[str, tuple[float, list[str]]]:
    """Score all 8 dimensions for a payload. Returns {dim_key: (score_0_to_1, suggestions)}."""
    gs, g_sug = _score_grounding(payload)
    rs, r_sug, _ = await _score_references_async(payload)
    ss, s_sug = _score_structure(payload)
    es, e_sug = _score_epistemic(payload)
    judge_results = await _score_llm_judge(title, payload)

    scores: dict[str, tuple[float, list[str]]] = {
        "grounding": (gs, g_sug),
        "references": (rs, r_sug),
        "structure": (ss, s_sug),
        "epistemic": (es, e_sug),
    }
    scores.update(judge_results)
    return scores


def _compute_overall(scores: dict[str, tuple[float | None, list[str]]]) -> int:
    """Weighted overall score over the dimensions that were actually scored.

    Unscored dimensions (None — e.g. the judge failed) are excluded and the
    remaining weights are renormalized, so a failed judge call neither inflates
    nor deflates the overall with fabricated data.
    """
    present = {
        k: scores.get(k, (None, []))[0]
        for k in DIM_WEIGHTS
        if scores.get(k, (None, []))[0] is not None
    }
    if not present:
        return 0
    total_weight = sum(DIM_WEIGHTS[k] for k in present)
    weighted = sum(present[k] * DIM_WEIGHTS[k] for k in present)
    return round(weighted / total_weight * 100)


# Maps any incoming dimension label/key (from the UI or improvement plan) to a
# canonical dimension key. Used so the feedback loop tracks the RIGHT dimension.
DIM_LABEL_MAP = {
    "grounding": "grounding",
    "epistemic markers": "epistemic", "epistemic": "epistemic",
    "reference integrity": "references", "references": "references",
    "content structure": "structure", "structure": "structure",
    "technical depth": "depth", "depth": "depth",
    "factual accuracy": "factual_accuracy", "factual_accuracy": "factual_accuracy",
    "comprehensiveness": "comprehensiveness",
    "research readiness": "research_readiness", "research_readiness": "research_readiness",
    "research quality": "depth",
}

# Dimensions scored by the LLM judge (subject to calibration gating).
JUDGE_DIM_KEYS = ("factual_accuracy", "comprehensiveness", "depth", "research_readiness")


def _normalize_dim_key(dimension: str) -> str:
    """Normalize a dimension label/key to its canonical key."""
    key = (dimension or "").lower().strip()
    return DIM_LABEL_MAP.get(key, key.replace(" ", "_"))


async def _score_topic_full(title: str, payload: dict) -> dict:
    """Score one topic across all 8 dimensions using the SAME scorer as the
    dashboard (heuristics + LLM judge). Returns {overall, <dim>: 0-100|None}.

    This is what before/after measurement must use — never a heuristic-only
    proxy — so deltas reflect the dimension actually being improved.
    """
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return {"overall": 0, **{dk: None for dk in ALL_DIM_KEYS}}

    scores = await _score_all_dimensions(title, payload)
    out: dict = {"overall": _compute_overall(scores)}
    for dk in ALL_DIM_KEYS:
        v = scores.get(dk, (None, []))[0]
        out[dk] = round(v * 100) if v is not None else None
    return out


async def _latest_per_topic_scores() -> dict[str, dict]:
    """Return {topic_id: {overall, dimensions}} from the most recent eval run.

    This is the authoritative per-topic signal (real LLM-judge scores), reused
    for weak-topic selection and 'before' snapshots without re-running the judge.
    """
    history = await db.get_eval_history(limit=1, include_per_topic=True)
    if not history:
        return {}
    out: dict[str, dict] = {}
    for tp in history[0].get("per_topic", []):
        out[tp["topic_id"]] = {
            "overall": tp.get("overall", 0),
            "dimensions": tp.get("dimensions", {}),
        }
    return out


# ── Judge calibration via ablation self-test ─────────────────────────
# We degrade good content along a specific dimension and verify the judge's
# score for that dimension drops. If it doesn't, the judge is blind to that
# dimension and its scores are not a trustworthy signal — so we refuse to let
# that dimension drive the feedback loop. This validates the judge WITHOUT
# human labels by testing discriminative power.

CALIBRATION_THRESHOLD = 15.0  # min avg score-drop (0-100) to trust a dimension


def _first_sentence(text: str) -> str:
    """Return just the first sentence — strips explanatory depth."""
    import re
    parts = re.split(r"(?<=[.!?])\s+", (text or "").strip(), maxsplit=1)
    return parts[0] if parts else (text or "")


def _ablate_payload(payload: dict, dim_key: str) -> dict:
    """Return a deep-ish copy of payload deliberately degraded for `dim_key`.

    Each ablation removes exactly the signal that dimension is supposed to
    measure, so a working judge should score the ablated version much lower.
    """
    import copy
    p = copy.deepcopy(payload)
    aspects = p.get("key_aspects", []) or []

    if dim_key == "depth":
        # Strip mechanism/math/trade-offs — keep only a one-line description.
        for a in aspects:
            if isinstance(a, dict):
                a["body"] = _first_sentence(a.get("body", ""))
        p["gists"] = []
    elif dim_key == "comprehensiveness":
        # Cover only the single core aspect; drop breadth.
        p["key_aspects"] = aspects[:1]
        p["open_problems"] = []
    elif dim_key == "research_readiness":
        # Remove the things that enable related-works / gap identification.
        p["open_problems"] = []
        p["references"] = []
        p["connections"] = []
    elif dim_key == "factual_accuracy":
        # Inject a blatantly fabricated claim a working judge should catch.
        bogus = (" This approach achieves exactly 100% accuracy on every "
                 "benchmark and uses precisely 999 trillion parameters, as "
                 "proven by Smith et al. (1850).")
        p["summary"] = (p.get("summary") or "") + bogus
        if aspects and isinstance(aspects[0], dict):
            aspects[0]["body"] = (aspects[0].get("body", "") + bogus)

    return p


async def run_calibration(sample_size: int = 5) -> dict:
    """Run the ablation self-test across a sample of topics and persist results."""
    import asyncio as _aio

    topics = await db.get_all_topics()
    done = [t for t in topics if t["status"] == "done" and t.get("payload")]
    if not done:
        return {"error": "No completed topics to calibrate against"}

    # Prefer the highest-scoring topics as the 'good' baseline if we know them.
    latest = await _latest_per_topic_scores()
    done.sort(key=lambda t: latest.get(t["id"], {}).get("overall", 0), reverse=True)
    sample = done[:sample_size]

    broadcast("thought", {
        "step": "calibration_start",
        "message": f"Running judge ablation self-test on {len(sample)} topic(s)...",
    })

    drops: dict[str, list[float]] = {dk: [] for dk in JUDGE_DIM_KEYS}
    detail: dict[str, list[dict]] = {dk: [] for dk in JUDGE_DIM_KEYS}

    for t in sample:
        payload = t["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                continue
        title = t["title"]

        base = await _score_llm_judge(title, payload)

        async def _ablate_and_score(dk: str):
            ablated = _ablate_payload(payload, dk)
            return dk, await _score_llm_judge(title, ablated)

        results = await _aio.gather(*[_ablate_and_score(dk) for dk in JUDGE_DIM_KEYS])

        for dk, abl in results:
            base_v = base.get(dk, (None, []))[0]
            abl_v = abl.get(dk, (None, []))[0]
            if base_v is not None and abl_v is not None:
                drop = (base_v - abl_v) * 100
                drops[dk].append(drop)
                detail[dk].append({
                    "topic": title[:40],
                    "base": round(base_v * 100),
                    "ablated": round(abl_v * 100),
                    "drop": round(drop, 1),
                })

    out = {}
    for dk in JUDGE_DIM_KEYS:
        ds = drops[dk]
        power = sum(ds) / len(ds) if ds else 0.0
        calibrated = power >= CALIBRATION_THRESHOLD
        await db.save_calibration(
            dimension=dk,
            discriminative_power=round(power, 1),
            calibrated=calibrated,
            sample_size=len(ds),
            detail={"samples": detail[dk], "threshold": CALIBRATION_THRESHOLD},
        )
        out[dk] = {
            "label": QUALITY_DIMENSIONS[dk]["label"],
            "discriminative_power": round(power, 1),
            "calibrated": calibrated,
            "sample_size": len(ds),
        }

    broadcast("thought", {
        "step": "calibration_done",
        "message": "Judge calibration complete.",
    })

    return {"dimensions": out, "threshold": CALIBRATION_THRESHOLD, "sample_size": len(sample)}


@router.post("/eval/calibrate")
async def calibrate_judge(body: dict | None = None):
    """Run the ablation self-test to validate the judge's discriminative power."""
    sample_size = int((body or {}).get("sample_size", 5))
    return await run_calibration(sample_size=max(1, min(sample_size, 10)))


@router.get("/eval/calibration")
async def get_judge_calibration():
    """Return the latest calibration status per judge dimension."""
    cal = await db.get_calibration()
    return {
        "calibration": {
            dk: {
                "label": QUALITY_DIMENSIONS[dk]["label"],
                "discriminative_power": cal.get(dk, {}).get("discriminative_power", 0),
                "calibrated": cal.get(dk, {}).get("calibrated", False),
                "sample_size": cal.get(dk, {}).get("sample_size", 0),
                "updated_at": cal.get(dk, {}).get("updated_at"),
            }
            for dk in JUDGE_DIM_KEYS
        },
        "threshold": CALIBRATION_THRESHOLD,
    }


@router.get("/quality/{topic_id}")
async def get_topic_quality(topic_id: str):
    """Run heuristic + LLM quality scorers on a topic."""
    topic = await db.get_topic(topic_id)
    if not topic or topic["status"] != "done":
        raise HTTPException(404, "Topic not ready")

    payload = topic.get("payload", {}) or {}
    scores = await _score_all_dimensions(topic.get("title", topic_id), payload)
    overall = _compute_overall(scores)

    dimensions = {}
    for dk in ALL_DIM_KEYS:
        score_val, sug = scores.get(dk, (0.65, []))
        dimensions[dk] = {
            **QUALITY_DIMENSIONS[dk],
            "score": round(score_val * 100),
            "suggestions": sug,
        }

    all_suggestions = []
    for dim in dimensions.values():
        all_suggestions.extend(dim["suggestions"])

    if overall >= 85:
        verdict = "strong"
        verdict_text = "Content quality is strong. Claims are well-grounded and properly attributed."
    elif overall >= 60:
        verdict = "moderate"
        verdict_text = "Content quality is moderate. Some claims need better attribution or hedging."
    else:
        verdict = "weak"
        verdict_text = "Content quality needs improvement. Multiple grounding or accuracy issues detected."

    return {
        "topic_id": topic_id,
        "overall": overall,
        "verdict": verdict,
        "verdict_text": verdict_text,
        "dimensions": dimensions,
        "suggestions": all_suggestions,
        "can_regenerate": overall < 70,
    }


@router.post("/topic/{topic_id}/regenerate")
async def regenerate_topic(topic_id: str):
    """Re-run Student generation for a topic with hardened prompts."""
    from agent_server.agent import do_generate_topic_content

    topic = await db.get_topic(topic_id)
    if not topic:
        raise HTTPException(404, "Topic not found")

    await db.update_topic_status(topic_id, "queued")
    broadcast("status", {"id": topic_id, "status": "queued", "error": None})

    async def regen():
        try:
            await do_generate_topic_content(topic_id)
        except Exception as e:
            logger.error("Regenerate error for %s: %s", topic_id, e, exc_info=True)
            await db.update_topic_status(topic_id, "failed", error=str(e)[:300])
            broadcast("status", {"id": topic_id, "status": "failed", "error": str(e)[:300]})

    asyncio.create_task(regen())
    return {"ok": True, "topic_id": topic_id, "message": "Regenerating with hardened prompts..."}


@router.get("/quality")
async def get_quality_overview():
    """Aggregate quality scores across all completed topics — all 8 dimensions."""
    import asyncio as _asyncio

    topics = await db.get_all_topics()
    done_topics = [t for t in topics if t["status"] == "done" and t.get("payload")]

    if not done_topics:
        return {"topics": [], "aggregate": None, "worst": [], "suggestions": []}

    results = []
    dim_totals: dict[str, list[float]] = {dk: [] for dk in ALL_DIM_KEYS}

    async def _score_one(t: dict) -> dict | None:
        payload = t["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                return None

        scores = await _score_all_dimensions(t.get("title", t["id"]), payload)
        overall = _compute_overall(scores)
        verdict = "strong" if overall >= 80 else "moderate" if overall >= 60 else "weak"

        all_sug = []
        for _, (_, sug) in scores.items():
            all_sug.extend(sug)

        def _dim_pct(dk: str):
            v = scores.get(dk, (None, []))[0]
            return round(v * 100) if v is not None else None

        return {
            "topic_id": t["id"],
            "title": t["title"],
            "category": t["category"],
            "overall": overall,
            "verdict": verdict,
            "dimensions": {dk: _dim_pct(dk) for dk in ALL_DIM_KEYS},
            "suggestion_count": len(all_sug),
            "top_suggestion": all_sug[0] if all_sug else None,
            "_raw_scores": {dk: scores.get(dk, (None, []))[0] for dk in ALL_DIM_KEYS},
        }

    sem = _asyncio.Semaphore(4)

    async def _bounded(t: dict):
        async with sem:
            return await _score_one(t)

    scored = await _asyncio.gather(*[_bounded(t) for t in done_topics])

    for r in scored:
        if not r:
            continue
        results.append(r)
        raw = r.pop("_raw_scores")
        for dk in ALL_DIM_KEYS:
            val = raw.get(dk)
            if val is not None:
                dim_totals[dk].append(val)

    n = len(results)
    aggregate = {
        "overall": round(sum(r["overall"] for r in results) / n),
        "topic_count": n,
        "strong": sum(1 for r in results if r["verdict"] == "strong"),
        "moderate": sum(1 for r in results if r["verdict"] == "moderate"),
        "weak": sum(1 for r in results if r["verdict"] == "weak"),
        "dimensions": {
            k: {
                "label": QUALITY_DIMENSIONS[k]["label"],
                "mean": round(sum(v) / len(v) * 100) if v else 0,
                "min": round(min(v) * 100) if v else 0,
                "max": round(max(v) * 100) if v else 0,
            }
            for k, v in dim_totals.items()
        },
    }

    worst = sorted(results, key=lambda r: r["overall"])[:5]

    global_suggestions = []
    for dim_key, dim_scores in dim_totals.items():
        mean = sum(dim_scores) / len(dim_scores) if dim_scores else 1.0
        if mean < 0.5:
            global_suggestions.append({
                "dimension": QUALITY_DIMENSIONS[dim_key]["label"],
                "severity": "high",
                "message": f"{QUALITY_DIMENSIONS[dim_key]['label']} is weak across topics (avg {round(mean*100)}%). "
                           f"Regenerate content with targeted prompts.",
                "action": "regenerate_all",
            })
        elif mean < 0.75:
            global_suggestions.append({
                "dimension": QUALITY_DIMENSIONS[dim_key]["label"],
                "severity": "medium",
                "message": f"{QUALITY_DIMENSIONS[dim_key]['label']} is moderate (avg {round(mean*100)}%). "
                           f"Newer content will score higher with updated prompts.",
                "action": "monitor",
            })

    topics_needing_regen = [r for r in results if r["overall"] < 70]
    if topics_needing_regen:
        global_suggestions.append({
            "dimension": "Content",
            "severity": "medium",
            "message": f"{len(topics_needing_regen)} topic(s) score below 70. "
                       f"Regenerate them to improve quality.",
            "action": "regenerate_weak",
            "topic_ids": [r["topic_id"] for r in topics_needing_regen],
        })

    return {
        "topics": sorted(results, key=lambda r: r["overall"]),
        "aggregate": aggregate,
        "worst": worst,
        "suggestions": global_suggestions,
    }


@router.post("/quality/regenerate-weak")
async def regenerate_weak_topics():
    """Regenerate all topics scoring below 70."""
    from agent_server.agent import do_generate_topic_content

    topics = await db.get_all_topics()
    to_regen = []

    for t in topics:
        if t["status"] != "done" or not t.get("payload"):
            continue
        payload = t["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                continue

        gs, _ = _score_grounding(payload)
        rs, _ = _score_references(payload)
        ss, _ = _score_structure(payload)
        es, _ = _score_epistemic(payload)
        overall = round((gs * 0.35 + rs * 0.2 + ss * 0.2 + es * 0.25) * 100)
        if overall < 70:
            to_regen.append(t["id"])

    async def regen_batch():
        for tid in to_regen:
            await db.update_topic_status(tid, "queued")
            broadcast("status", {"id": tid, "status": "queued", "error": None})

        sem = asyncio.Semaphore(4)
        async def _gen(tid: str):
            async with sem:
                try:
                    await do_generate_topic_content(tid)
                except Exception as e:
                    logger.error("Regenerate error for %s: %s", tid, e)

        await asyncio.gather(*[_gen(tid) for tid in to_regen])

    if to_regen:
        asyncio.create_task(regen_batch())

    return {"ok": True, "regenerating": len(to_regen), "topic_ids": to_regen}


# ── Eval Lifecycle ────────────────────────────────────────────────

def _compute_improvements(current: dict, previous: dict | None) -> dict | None:
    """Compare two eval snapshots and compute deltas."""
    if not previous:
        return None

    deltas = {}
    deltas["overall"] = current["overall"] - previous["overall"]
    deltas["strong"] = current["strong"] - previous["strong"]
    deltas["weak"] = current["weak"] - previous["weak"]

    deltas["dimensions"] = {}
    for dk in ALL_DIM_KEYS:
        c_mean = current["dimensions"].get(dk, {}).get("mean", 0)
        p_mean = previous.get("dimensions", {}).get(dk, {}).get("mean", 0)
        deltas["dimensions"][dk] = c_mean - p_mean

    curr_ids = {t["topic_id"] for t in current.get("_per_topic", [])}
    prev_ids = {t["topic_id"] for t in previous.get("per_topic", [])}
    deltas["new_topics"] = len(curr_ids - prev_ids)
    deltas["removed_topics"] = len(prev_ids - curr_ids)

    improved = []
    prev_by_id = {t["topic_id"]: t for t in previous.get("per_topic", [])}
    for t in current.get("_per_topic", []):
        pt = prev_by_id.get(t["topic_id"])
        if pt and t["overall"] > pt["overall"]:
            improved.append({
                "topic_id": t["topic_id"],
                "title": t["title"],
                "before": pt["overall"],
                "after": t["overall"],
                "delta": t["overall"] - pt["overall"],
            })
    deltas["improved_topics"] = sorted(improved, key=lambda x: -x["delta"])

    degraded = []
    for t in current.get("_per_topic", []):
        pt = prev_by_id.get(t["topic_id"])
        if pt and t["overall"] < pt["overall"]:
            degraded.append({
                "topic_id": t["topic_id"],
                "title": t["title"],
                "before": pt["overall"],
                "after": t["overall"],
                "delta": t["overall"] - pt["overall"],
            })
    deltas["degraded_topics"] = sorted(degraded, key=lambda x: x["delta"])

    return deltas


def _generate_insights(aggregate: dict, suggestions: list, improvements: dict | None) -> list[dict]:
    """Generate human-readable insights from eval data."""
    insights = []

    overall = aggregate["overall"]
    if overall >= 85:
        insights.append({
            "type": "positive",
            "title": "Content quality is strong",
            "message": (
                f"Overall score of {overall}/100 means generated content is well-grounded "
                f"and properly structured. Claims are attributed, references are valid, "
                f"and the content follows the required schema."
            ),
        })
    elif overall >= 60:
        insights.append({
            "type": "attention",
            "title": "Content quality needs attention",
            "message": (
                f"Overall score of {overall}/100 indicates moderate quality. Some content "
                f"may contain unhedged claims or missing epistemic markers. This means "
                f"a learner could mistake speculation for established fact."
            ),
        })
    else:
        insights.append({
            "type": "warning",
            "title": "Content quality requires improvement",
            "message": (
                f"Overall score of {overall}/100 means significant quality gaps. "
                f"Regenerate weak topics before using this content for research."
            ),
        })

    dims = aggregate.get("dimensions", {})
    epi = dims.get("epistemic", {}).get("mean", 100)
    if epi < 50:
        insights.append({
            "type": "learning_impact",
            "title": "Low epistemic markers affect your learning",
            "message": (
                f"Epistemic marker density is {epi}%. This means the content doesn't "
                f"clearly signal which claims are well-established vs. speculative. "
                f"When studying these topics, you can't tell what's proven from what's "
                f"hypothesized. Regenerating adds markers like 'well-established', "
                f"'speculative', 'approximately' so you know what to trust."
            ),
        })

    depth = dims.get("depth", {}).get("mean", 100)
    if depth > 0 and depth < 65:
        insights.append({
            "type": "learning_impact",
            "title": "Content lacks technical depth",
            "message": (
                f"Technical depth score is {depth}%. Content describes WHAT things are "
                f"but doesn't explain HOW or WHY they work. For research prep, you need "
                f"to understand mechanisms, trade-offs, and failure modes — not just definitions. "
                f"Regenerating with the depth-targeted prompt will force deeper explanations."
            ),
        })

    factual = dims.get("factual_accuracy", {}).get("mean", 100)
    if factual > 0 and factual < 70:
        insights.append({
            "type": "warning",
            "title": "Factual accuracy concerns",
            "message": (
                f"Factual accuracy is {factual}%. Some claims may be incorrect or unverifiable. "
                f"Do NOT cite any numbers from this content without independent verification."
            ),
        })

    grounding = dims.get("grounding", {}).get("mean", 100)
    if grounding < 70:
        insights.append({
            "type": "learning_impact",
            "title": "Ungrounded claims risk false understanding",
            "message": (
                f"Grounding score is {grounding}%. Some content asserts specific numbers "
                f"(benchmark scores, parameter counts) without attribution. If you memorize "
                f"these for a research paper, you may cite fabricated statistics. "
                f"Regeneration will either add sources or hedge uncertain claims."
            ),
        })

    refs = dims.get("references", {}).get("mean", 100)
    if refs < 80:
        insights.append({
            "type": "learning_impact",
            "title": "Reference integrity issues",
            "message": (
                f"Reference integrity is {refs}%. Some citations may have invalid "
                f"arXiv IDs or suspiciously short titles. Don't cite these references "
                f"in your own work without verifying them independently."
            ),
        })

    if improvements:
        delta = improvements.get("overall", 0)
        if delta > 0:
            improved = improvements.get("improved_topics", [])
            insights.append({
                "type": "trend",
                "title": f"Quality improved by {delta} points since last run",
                "message": (
                    f"Overall score went up {delta} points. "
                    f"{len(improved)} topic(s) improved. "
                    f"{'The hardened prompts are working.' if delta >= 5 else 'Incremental progress — keep regenerating weak topics.'}"
                ),
            })
        elif delta < 0:
            degraded = improvements.get("degraded_topics", [])
            insights.append({
                "type": "warning",
                "title": f"Quality dropped by {abs(delta)} points",
                "message": (
                    f"{len(degraded)} topic(s) scored lower than before. "
                    f"This can happen when content is regenerated with a different model "
                    f"or when new topics dilute the average."
                ),
            })
        elif delta == 0:
            insights.append({
                "type": "neutral",
                "title": "No change in overall quality",
                "message": "Scores are stable. Focus on the weakest dimension to drive improvement.",
            })

        new_ct = improvements.get("new_topics", 0)
        if new_ct > 0:
            insights.append({
                "type": "info",
                "title": f"{new_ct} new topic(s) since last evaluation",
                "message": "New topics were added. Their quality is included in this run's aggregate.",
            })

    return insights


def _dim_score(t: dict, dk: str) -> float | None:
    """Per-topic dimension score, or None if missing/unscored (judge failure)."""
    v = t.get("dimensions", {}).get(dk)
    return v if isinstance(v, (int, float)) else None


def _generate_improvement_plan(
    aggregate: dict,
    suggestions: list,
    improvements: dict | None,
    per_topic: list[dict] | None = None,
) -> list[dict]:
    """Generate targeted improvement actions based on per-topic score analysis."""
    plan = []
    dims = aggregate.get("dimensions", {})
    per_topic = per_topic or []

    ranked_dims = sorted(
        [(k, v.get("mean", 100)) for k, v in dims.items()],
        key=lambda x: x[1],
    )

    for rank, (dk, mean) in enumerate(ranked_dims):
        if mean >= 95:
            continue

        label = QUALITY_DIMENSIONS.get(dk, {}).get("label", dk)

        # None dimension values mean "unscored" (e.g. judge rate-limited) — they
        # are not weak, so they must be excluded from threshold comparisons.
        weak_topics = [
            t for t in per_topic
            if (_dim_score(t, dk) is not None and _dim_score(t, dk) < 70)
        ]
        weak_topics.sort(key=lambda t: _dim_score(t, dk) if _dim_score(t, dk) is not None else 100)

        if mean >= 85:
            actions = [{
                "action": "info",
                "description": (
                    f"Average {mean}% — strong. "
                    + (f"{len(weak_topics)} topic(s) still below 70%: "
                       f"{', '.join(t['title'][:30] for t in weak_topics[:3])}"
                       + (f" +{len(weak_topics)-3} more" if len(weak_topics) > 3 else "")
                       if weak_topics else "All topics above threshold.")
                ),
                "impact": "low",
            }]
            status = "monitor"
        elif mean >= 70:
            if weak_topics:
                actions = [{
                    "action": "regenerate",
                    "description": (
                        f"Regenerate {len(weak_topics)} weak topic(s) to raise average from {mean}%: "
                        f"{', '.join(t['title'][:25] for t in weak_topics[:4])}"
                        + (f" +{len(weak_topics)-4} more" if len(weak_topics) > 4 else "")
                    ),
                    "impact": "medium",
                    "topic_ids": [t["topic_id"] for t in weak_topics],
                }]
            else:
                actions = [{
                    "action": "info",
                    "description": f"Average {mean}% with no individual topics below 70%. Minor refinements only.",
                    "impact": "low",
                }]
            status = "improvement"
        else:
            actions = _dim_specific_actions(dk, mean, weak_topics)
            status = "critical" if mean < 50 else "improvement"

        projected = mean
        if weak_topics and len(per_topic) > 0:
            boost = len(weak_topics) * 15 / len(per_topic)
            projected = min(round(mean + boost), 95)

        item: dict[str, Any] = {
            "priority": rank + 1,
            "dimension": label,
            "current_score": mean,
            "target_score": projected if projected > mean else min(mean + 10, 100),
            "status": status,
            "actions": actions,
            "weak_topic_count": len(weak_topics),
        }

        if weak_topics:
            item["weak_topics"] = [
                {"id": t["topic_id"], "title": t["title"], "score": _dim_score(t, dk) or 0}
                for t in weak_topics[:6]
            ]

        plan.append(item)

    weak_count = aggregate.get("weak", 0)
    if weak_count > 0:
        weak_overall = [
            t for t in per_topic
            if isinstance(t.get("overall"), (int, float)) and t["overall"] < 60
        ]
        plan.append({
            "priority": len(plan) + 1,
            "dimension": "Weak Topics",
            "current_score": None,
            "target_score": None,
            "status": "action",
            "actions": [{
                "action": "regenerate_weak",
                "description": (
                    f"Regenerate {weak_count} weak topic(s): "
                    + ", ".join(t["title"][:30] for t in weak_overall[:4])
                    + (f" +{len(weak_overall)-4} more" if len(weak_overall) > 4 else "")
                ),
                "impact": "high",
                "topic_ids": [t["topic_id"] for t in weak_overall],
            }],
        })

    if improvements and improvements.get("degraded_topics"):
        plan.append({
            "priority": len(plan) + 1,
            "dimension": "Regressions",
            "current_score": None,
            "target_score": None,
            "status": "investigate",
            "actions": [{
                "action": "investigate",
                "description": (
                    f"Investigate {len(improvements['degraded_topics'])} topic(s) "
                    f"that scored lower than the previous run"
                ),
                "impact": "medium",
                "topic_ids": [t["topic_id"] for t in improvements["degraded_topics"]],
            }],
        })

    return plan


def _dim_specific_actions(dim_key: str, score: int, weak_topics: list[dict]) -> list[dict]:
    """Return targeted improvement actions with specific topic names."""
    topic_list = ", ".join(t["title"][:25] for t in weak_topics[:4])
    extra = f" +{len(weak_topics)-4} more" if len(weak_topics) > 4 else ""
    impact = "high" if score < 50 else "medium"

    TARGETED_DESCRIPTIONS: dict[str, tuple[str, str]] = {
        "epistemic": (
            "Regenerate {n} topic(s) lacking epistemic markers: {topics}",
            "Regenerate to add confidence markers (well-established, speculative, approximately)",
        ),
        "grounding": (
            "Regenerate {n} topic(s) with unattributed claims: {topics}",
            "Regenerate to ensure all claims are attributed or hedged",
        ),
        "references": (
            "Fix citations in {n} topic(s): {topics}",
            "Regenerate to fix invalid references and add proper citations",
        ),
        "structure": (
            "Fix schema in {n} topic(s): {topics}",
            "Regenerate to ensure all required fields are present and content is complete",
        ),
        "depth": (
            "Deepen {n} topic(s) with shallow explanations: {topics}",
            "Regenerate to explain mechanisms, edge cases, failure modes — not just descriptions",
        ),
        "factual_accuracy": (
            "Fix factual issues in {n} topic(s): {topics}",
            "Regenerate to correct inaccurate or unverifiable claims",
        ),
        "comprehensiveness": (
            "Expand coverage in {n} topic(s): {topics}",
            "Regenerate to cover missing aspects, trade-offs, and limitations",
        ),
        "research_readiness": (
            "Improve research readiness in {n} topic(s): {topics}",
            "Regenerate to position in literature, identify open questions, and enable related-works writing",
        ),
    }

    with_topics, without_topics = TARGETED_DESCRIPTIONS.get(
        dim_key,
        ("Regenerate {n} topic(s): {topics}", "Regenerate to improve quality"),
    )

    desc = (
        with_topics.format(n=len(weak_topics), topics=topic_list + extra)
        if weak_topics else without_topics
    )

    return [{
        "action": "regenerate",
        "description": desc,
        "impact": impact,
        "topic_ids": [t["topic_id"] for t in weak_topics],
    }]


@router.post("/eval/run")
async def run_eval_and_record(trigger: str = "manual", notes: str | None = None):
    """Run a full quality evaluation, record the snapshot, and return results with insights."""
    overview = await get_quality_overview()
    aggregate = overview.get("aggregate")

    if not aggregate:
        return {"error": "No topics to evaluate", "recorded": False}

    history = await db.get_eval_history(limit=1)
    previous = history[0] if history else None

    improvements = _compute_improvements(
        {**aggregate, "_per_topic": overview["topics"]},
        previous,
    )

    run_id = await db.record_eval_run(
        overall=aggregate["overall"],
        topic_count=aggregate["topic_count"],
        strong=aggregate["strong"],
        moderate=aggregate["moderate"],
        weak=aggregate["weak"],
        dimensions=aggregate["dimensions"],
        per_topic=overview["topics"],
        suggestions=overview["suggestions"],
        trigger=trigger,
        notes=notes,
        improvements=improvements,
    )

    insights = _generate_insights(aggregate, overview["suggestions"], improvements)
    plan = _generate_improvement_plan(aggregate, overview["suggestions"], improvements, overview["topics"])

    return {
        "run_id": run_id,
        "recorded": True,
        "aggregate": aggregate,
        "topics": overview["topics"],
        "suggestions": overview["suggestions"],
        "insights": insights,
        "improvement_plan": plan,
        "improvements": improvements,
        "previous_run": {
            "id": previous["id"],
            "run_at": previous["run_at"],
            "overall": previous["overall"],
        } if previous else None,
    }


@router.get("/eval/latest")
async def get_eval_latest():
    """Return the latest eval snapshot from DB cache — no re-scoring.

    If no runs exist, falls back to heuristic-only scores (fast, no LLM).
    Full scoring only happens on explicit POST /eval/run.
    """
    history = await db.get_eval_history(limit=2, include_per_topic=True)
    if history:
        latest = history[0]
        previous = history[1] if len(history) > 1 else None

        aggregate = {
            "overall": latest["overall"],
            "topic_count": latest["topic_count"],
            "strong": latest["strong"],
            "moderate": latest["moderate"],
            "weak": latest["weak"],
            "dimensions": latest.get("dimensions", {}),
        }
        topics = latest.get("per_topic", [])
        suggestions = latest.get("suggestions", [])

        improvements = _compute_improvements(
            {**aggregate, "_per_topic": topics},
            previous,
        )
        insights = _generate_insights(aggregate, suggestions, improvements)
        plan = _generate_improvement_plan(aggregate, suggestions, improvements, topics)

        return {
            "run_id": latest["id"],
            "recorded": True,
            "aggregate": aggregate,
            "topics": topics,
            "suggestions": suggestions,
            "insights": insights,
            "improvement_plan": plan,
            "improvements": improvements,
        }

    topics = await db.get_all_topics()
    done_topics = [t for t in topics if t["status"] == "done" and t.get("payload")]
    if not done_topics:
        return {"error": "No topics to evaluate"}

    results = []
    heuristic_dims = ("grounding", "references", "structure", "epistemic")
    dim_totals: dict[str, list[float]] = {dk: [] for dk in heuristic_dims}
    for t in done_topics:
        payload = t["payload"]
        if isinstance(payload, str):
            try:
                payload = json.loads(payload)
            except Exception:
                continue
        gs, _ = _score_grounding(payload)
        rs, _ = _score_references(payload)
        ss, _ = _score_structure(payload)
        es, _ = _score_epistemic(payload)
        overall = round((gs * 0.30 + rs * 0.20 + ss * 0.25 + es * 0.25) * 100)
        dim_scores = {
            "grounding": round(gs * 100), "references": round(rs * 100),
            "structure": round(ss * 100), "epistemic": round(es * 100),
            "factual_accuracy": 0, "comprehensiveness": 0, "depth": 0, "research_readiness": 0,
        }
        results.append({
            "topic_id": t["id"], "title": t["title"], "category": t["category"],
            "overall": overall,
            "verdict": "strong" if overall >= 80 else "moderate" if overall >= 60 else "weak",
            "dimensions": dim_scores,
        })
        dim_totals["grounding"].append(gs)
        dim_totals["references"].append(rs)
        dim_totals["structure"].append(ss)
        dim_totals["epistemic"].append(es)

    n = len(results)
    agg_dims = {
        dk: {
            "label": QUALITY_DIMENSIONS.get(dk, {}).get("label", dk),
            "mean": round(sum(vs) / len(vs) * 100) if vs else 0,
            "min": round(min(vs, default=0) * 100),
            "max": round(max(vs, default=0) * 100),
        }
        for dk, vs in dim_totals.items()
    }
    for dk in ("factual_accuracy", "comprehensiveness", "depth", "research_readiness"):
        agg_dims[dk] = {"label": QUALITY_DIMENSIONS[dk]["label"], "mean": 0, "min": 0, "max": 0}

    aggregate = {
        "overall": round(sum(r["overall"] for r in results) / n) if n else 0,
        "topic_count": n,
        "strong": sum(1 for r in results if r["verdict"] == "strong"),
        "moderate": sum(1 for r in results if r["verdict"] == "moderate"),
        "weak": sum(1 for r in results if r["verdict"] == "weak"),
        "dimensions": agg_dims,
    }

    insights = _generate_insights(aggregate, [], None)
    plan = _generate_improvement_plan(aggregate, [], None, results)
    return {
        "run_id": None,
        "recorded": False,
        "aggregate": aggregate,
        "topics": results,
        "suggestions": [],
        "insights": insights,
        "improvement_plan": plan,
        "improvements": None,
    }


@router.get("/eval/history")
async def get_eval_history():
    """Return the history of eval runs for trend tracking."""
    runs = await db.get_eval_history(limit=50)
    return {"runs": runs}


@router.get("/eval/run/{run_id}")
async def get_eval_run(run_id: int):
    """Return detailed results for a specific eval run."""
    run = await db.get_eval_run_detail(run_id)
    if not run:
        raise HTTPException(404, "Eval run not found")
    return run


@router.get("/eval/compare/{run_a}/{run_b}")
async def compare_eval_runs(run_a: int, run_b: int):
    """Compare two eval runs side by side."""
    a = await db.get_eval_run_detail(run_a)
    b = await db.get_eval_run_detail(run_b)
    if not a or not b:
        raise HTTPException(404, "One or both runs not found")

    delta = {
        "overall": a["overall"] - b["overall"],
        "topic_count": a["topic_count"] - b["topic_count"],
        "strong": a["strong"] - b["strong"],
        "weak": a["weak"] - b["weak"],
        "dimensions": {},
    }

    a_dims = a.get("dimensions", {})
    b_dims = b.get("dimensions", {})
    for dk in ("grounding", "epistemic", "references", "structure"):
        a_mean = a_dims.get(dk, {}).get("mean", 0) if isinstance(a_dims.get(dk), dict) else 0
        b_mean = b_dims.get(dk, {}).get("mean", 0) if isinstance(b_dims.get(dk), dict) else 0
        delta["dimensions"][dk] = a_mean - b_mean

    return {"run_a": a, "run_b": b, "delta": delta}


# ── Eval Actions (feedback loop) ─────────────────────────────────


def _score_topic(payload: dict) -> dict:
    """Score a single topic's payload (heuristic-only, fast) for Apply Fix before/after."""
    if isinstance(payload, str):
        try:
            payload = json.loads(payload)
        except Exception:
            return {"overall": 0, **{dk: 0 for dk in ALL_DIM_KEYS}}

    gs, _ = _score_grounding(payload)
    rs, _ = _score_references(payload)
    ss, _ = _score_structure(payload)
    es, _ = _score_epistemic(payload)
    heuristic_overall = round((gs * 0.30 + rs * 0.20 + ss * 0.25 + es * 0.25) * 100)
    return {
        "overall": heuristic_overall,
        "grounding": round(gs * 100),
        "references": round(rs * 100),
        "structure": round(ss * 100),
        "epistemic": round(es * 100),
    }


@router.post("/eval/action/apply")
async def apply_eval_action(body: dict):
    """Apply a single improvement action from the plan.

    Body: {eval_run_id, dimension, action_type, description, topic_ids?}
    Captures before-scores, triggers regeneration, and returns the action ID.
    """
    from agent_server.agent import do_generate_topic_content

    eval_run_id = body.get("eval_run_id")
    dimension = body.get("dimension", "")
    action_type = body.get("action_type", "regenerate")
    description = body.get("description", "")
    topic_ids = body.get("topic_ids", [])

    if not eval_run_id:
        logger.info("No eval_run_id provided — auto-recording eval run before applying action")
        result = await run_eval_and_record(trigger="auto_before_apply")
        eval_run_id = result.get("run_id")
        if not eval_run_id:
            raise HTTPException(400, "Could not create eval run")

    # Canonical dimension key drives selection, delta tracking, and learning.
    dk = _normalize_dim_key(dimension)

    # Latest eval run holds the authoritative per-topic LLM-judge scores.
    latest = await _latest_per_topic_scores()

    if not topic_ids:
        if action_type == "regenerate_weak":
            topic_ids = [tid for tid, s in latest.items() if s["overall"] < 70]
        elif action_type == "regenerate":
            # Find topics weak in the TARGETED dimension using real scores.
            def _weak(threshold: int) -> list[str]:
                picks = []
                for tid, s in latest.items():
                    v = s["dimensions"].get(dk)
                    if isinstance(v, (int, float)) and v < threshold:
                        picks.append((tid, v))
                picks.sort(key=lambda x: x[1])
                return [tid for tid, _ in picks]

            topic_ids = _weak(70) or _weak(85)

    if not topic_ids:
        return {"ok": False, "message": "No topics need improvement for this dimension"}

    # 'Before' snapshot: prefer the real scores from the latest eval run (what
    # the user saw); fall back to full scoring if a topic wasn't in that run.
    before_scores = {}
    for tid in topic_ids:
        t = await db.get_topic(tid)
        title = t["title"] if t else tid
        if tid in latest:
            before_scores[tid] = {
                "title": title,
                "overall": latest[tid]["overall"],
                **{k: latest[tid]["dimensions"].get(k) for k in ALL_DIM_KEYS},
            }
        elif t and t.get("payload"):
            before_scores[tid] = {"title": title, **(await _score_topic_full(title, t["payload"]))}

    action_id = await db.create_eval_action(
        eval_run_id=eval_run_id,
        dimension=dimension,
        action_type=action_type,
        description=description,
        topic_ids=topic_ids,
        before_scores=before_scores,
    )

    await db.update_eval_action_status(action_id, "running")

    quality_hints = {
        "epistemic markers": (
            "This content scored LOW on epistemic markers. You MUST:\n"
            "- Qualify every factual claim with confidence language\n"
            "- Use phrases like 'well-established', 'widely reported', 'speculative',\n"
            "  'not officially confirmed', 'undisclosed', 'approximately'\n"
            "- Every paragraph should have at least one epistemic signal\n"
            "- Distinguish verified facts from community consensus from speculation"
        ),
        "epistemic": (
            "This content scored LOW on epistemic markers. You MUST:\n"
            "- Qualify every factual claim with confidence language\n"
            "- Use phrases like 'well-established', 'widely reported', 'speculative'\n"
            "- Every paragraph should have at least one epistemic signal"
        ),
        "grounding": (
            "This content scored LOW on grounding. You MUST:\n"
            "- Attribute every factual claim to a specific published source\n"
            "- Use '(Source: Paper Name, Year)' format for attributions\n"
            "- If you cannot name the source, frame it as 'widely reported' or omit"
        ),
        "references": (
            "This content scored LOW on references. You MUST:\n"
            "- Include 2-3 real, verifiable references with actual paper titles\n"
            "- Never invent paper titles, authors, or arXiv IDs\n"
            "- Only cite papers you are certain exist"
        ),
        "structure": (
            "This content scored LOW on structural completeness. You MUST:\n"
            "- Include all required fields: summary, takeaway, eli5, key_aspects,\n"
            "  references, open_problems, experiment, connections\n"
            "- Ensure key_aspects has 2-3 entries with title, intuition, and body"
        ),
        "technical depth": (
            "This content scored LOW on TECHNICAL DEPTH. You MUST:\n"
            "- Explain the MECHANISM — HOW it works, not just WHAT it is\n"
            "- Cover edge cases and failure modes\n"
            "- Discuss WHY certain design choices were made and their trade-offs\n"
            "- Include mathematical intuition where relevant\n"
            "- Go beyond textbook-level description into analytical depth"
        ),
        "depth": (
            "This content scored LOW on TECHNICAL DEPTH. You MUST:\n"
            "- Explain the MECHANISM — HOW it works, not just WHAT it is\n"
            "- Cover edge cases and failure modes\n"
            "- Discuss WHY certain design choices were made and their trade-offs\n"
            "- Include mathematical intuition where relevant\n"
            "- Go beyond textbook-level description into analytical depth"
        ),
        "factual accuracy": (
            "This content scored LOW on FACTUAL ACCURACY. You MUST:\n"
            "- Verify every claim against established literature before stating it\n"
            "- Do not invent benchmark numbers, parameter counts, or dates\n"
            "- If unsure, hedge with 'reportedly' or 'approximately'\n"
            "- Attribute claims to specific papers where possible"
        ),
        "factual_accuracy": (
            "This content scored LOW on FACTUAL ACCURACY. You MUST:\n"
            "- Verify every claim against established literature\n"
            "- Do not invent benchmark numbers or dates\n"
            "- Hedge uncertain claims; attribute where possible"
        ),
        "comprehensiveness": (
            "This content scored LOW on COMPREHENSIVENESS. You MUST:\n"
            "- Cover ALL key aspects of the topic, not just the core idea\n"
            "- Include trade-offs and limitations, not just advantages\n"
            "- Discuss related approaches and how they compare\n"
            "- Address open problems and current limitations"
        ),
        "research readiness": (
            "This content scored LOW on RESEARCH READINESS. You MUST:\n"
            "- Position the topic clearly in the research landscape\n"
            "- Identify specific open questions and unsolved problems\n"
            "- Include enough detail for someone writing a related-works section\n"
            "- Suggest concrete research directions arising from gaps"
        ),
        "research_readiness": (
            "This content scored LOW on RESEARCH READINESS. You MUST:\n"
            "- Position the topic in the research landscape\n"
            "- Identify open questions and unsolved problems\n"
            "- Enable related-works writing and suggest research directions"
        ),
    }
    hint = quality_hints.get(dimension.lower(), None)

    async def run_regen():
        try:
            for tid in topic_ids:
                await db.update_topic_status(tid, "queued")
                broadcast("status", {"id": tid, "status": "queued", "error": None})

            sem = asyncio.Semaphore(4)
            async def _gen_one(tid: str):
                async with sem:
                    try:
                        await do_generate_topic_content(tid, quality_hint=hint)
                    except Exception as e:
                        logger.error("Regen error for %s: %s", tid, e)
                        await db.update_topic_status(tid, "failed", error=str(e)[:300])

            await asyncio.gather(*[_gen_one(tid) for tid in topic_ids])

            # 'After' is measured with the SAME full scorer (heuristics + judge).
            after_scores = {}
            for tid in topic_ids:
                t = await db.get_topic(tid)
                if t and t.get("payload"):
                    after_scores[tid] = {
                        "title": t["title"],
                        **(await _score_topic_full(t["title"], t["payload"])),
                    }

            # Delta over ALL dimensions. None when either side is unscored.
            def _d(a, b):
                return (a - b) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None

            delta = {}
            for tid in topic_ids:
                b = before_scores.get(tid, {})
                a = after_scores.get(tid, {})
                row = {
                    "title": a.get("title", b.get("title", "")),
                    "before": b.get("overall", 0),
                    "after": a.get("overall", 0),
                    "overall": _d(a.get("overall"), b.get("overall")),
                }
                for k in ALL_DIM_KEYS:
                    row[k] = _d(a.get(k), b.get(k))
                delta[tid] = row

            await db.update_eval_action_status(
                action_id, "done",
                after_scores=after_scores,
                delta=delta,
            )

            # Learning gate: only persist a hint if (1) the judge can actually
            # discriminate this dimension (calibrated), and (2) the TARGETED
            # dimension genuinely improved — measured on the real dimension,
            # not a heuristic proxy.
            if hint:
                calibration = await db.get_calibration()
                is_judge_dim = dk in JUDGE_DIM_KEYS
                calibrated = (not is_judge_dim) or calibration.get(dk, {}).get("calibrated", False)

                dim_deltas = [
                    row[dk] for row in delta.values()
                    if isinstance(row.get(dk), (int, float))
                ]
                if not calibrated:
                    logger.warning(
                        "Skipping learning for '%s': judge not calibrated for this dimension "
                        "(run the ablation self-test first)", dk,
                    )
                elif dim_deltas:
                    avg_delta = sum(dim_deltas) / len(dim_deltas)
                    if avg_delta > 0:
                        await db.save_quality_learning(
                            dimension=dk,
                            hint=hint,
                            delta_pct=avg_delta,
                            source_action_id=action_id,
                        )
                        logger.info(
                            "Saved quality learning for '%s' (avg delta on %s: +%.1f%%)",
                            dimension, dk, avg_delta,
                        )
                    else:
                        logger.info(
                            "No learning saved for '%s': %s did not improve (avg delta %.1f%%)",
                            dimension, dk, avg_delta,
                        )
                else:
                    logger.info(
                        "No learning saved for '%s': dimension %s was unscored",
                        dimension, dk,
                    )

            broadcast("eval_action", {
                "action_id": action_id,
                "status": "done",
                "topic_count": len(topic_ids),
                "delta": delta,
            })
        except Exception as e:
            logger.error("Action %d failed: %s", action_id, e, exc_info=True)
            await db.update_eval_action_status(action_id, "failed")
            broadcast("eval_action", {"action_id": action_id, "status": "failed"})

    asyncio.create_task(run_regen())

    return {
        "ok": True,
        "action_id": action_id,
        "topic_count": len(topic_ids),
        "topic_ids": topic_ids,
        "before_scores": before_scores,
    }


@router.get("/eval/actions/{eval_run_id}")
async def get_eval_actions(eval_run_id: int):
    """Get all actions taken for an eval run."""
    actions = await db.get_eval_actions(eval_run_id)
    return {"actions": actions}


@router.get("/eval/actions")
async def get_all_actions():
    """Get recent eval actions across all runs."""
    actions = await db.get_all_eval_actions(limit=50)
    return {"actions": actions}


@router.post("/eval/action/{action_id}/review")
async def review_eval_action(action_id: int):
    """Re-score the affected topics and compute fresh deltas for a completed action."""
    actions = await db.get_all_eval_actions(limit=100)
    action = next((a for a in actions if a["id"] == action_id), None)
    if not action:
        raise HTTPException(404, "Action not found")

    if action["status"] not in ("done", "failed"):
        return {"ok": False, "message": "Action is still running", "status": action["status"]}

    topic_ids = action.get("topic_ids", [])
    before_scores = action.get("before_scores", {})

    current_scores = {}
    for tid in topic_ids:
        t = await db.get_topic(tid)
        if t and t.get("payload"):
            current_scores[tid] = {
                "title": t["title"],
                **(await _score_topic_full(t["title"], t["payload"])),
            }

    def _d(a, b):
        return (a - b) if isinstance(a, (int, float)) and isinstance(b, (int, float)) else None

    delta = {}
    for tid in topic_ids:
        b = before_scores.get(tid, {})
        c = current_scores.get(tid, {})
        row = {
            "title": c.get("title", b.get("title", "")),
            "before": b.get("overall", 0),
            "after": c.get("overall", 0),
            "overall": _d(c.get("overall"), b.get("overall")),
        }
        for k in ALL_DIM_KEYS:
            row[k] = _d(c.get(k), b.get(k))
        delta[tid] = row

    improved = sum(1 for d in delta.values() if (d["overall"] or 0) > 0)
    degraded = sum(1 for d in delta.values() if (d["overall"] or 0) < 0)
    unchanged = sum(1 for d in delta.values() if (d["overall"] or 0) == 0)

    return {
        "ok": True,
        "action_id": action_id,
        "dimension": action["dimension"],
        "before_scores": before_scores,
        "current_scores": current_scores,
        "delta": delta,
        "summary": {
            "total": len(topic_ids),
            "improved": improved,
            "degraded": degraded,
            "unchanged": unchanged,
        },
    }


# ── arXiv Endpoints ─────────────────────────────────────────────────

@router.get("/arxiv/recent/{topic_id}")
async def get_recent_papers(topic_id: str, months: int = 12):
    """Fetch recent arXiv papers related to a topic."""
    from agent_server.arxiv import get_recent, find_related

    topic = await db.get_topic(topic_id)
    if not topic:
        raise HTTPException(404, "Topic not found")

    recent = await get_recent(topic["title"], months_back=months, max_results=10)
    related = await find_related(topic["title"], "", max_results=5)

    seen_ids = set()
    all_papers = []
    for p in recent + related:
        if p.arxiv_id not in seen_ids:
            seen_ids.add(p.arxiv_id)
            all_papers.append({
                "arxiv_id": p.arxiv_id,
                "title": p.title,
                "authors": p.authors[:5],
                "year": p.year,
                "published": p.published,
                "summary": p.summary[:300],
                "url": p.abs_url,
                "categories": p.categories[:3],
            })

    return {
        "topic_id": topic_id,
        "topic_title": topic["title"],
        "papers": all_papers,
        "total": len(all_papers),
        "months_searched": months,
    }


@router.get("/arxiv/verify/{topic_id}")
async def verify_topic_references(topic_id: str):
    """Verify all references in a topic against arXiv."""
    from agent_server.arxiv import build_citation_context

    topic = await db.get_topic(topic_id)
    if not topic or not topic.get("payload"):
        raise HTTPException(404, "Topic not found or no content")

    payload = topic["payload"]
    if isinstance(payload, str):
        payload = json.loads(payload)

    refs = payload.get("references", [])
    summary = payload.get("summary", "")

    ctx = await build_citation_context(topic["title"], summary, refs)

    return {
        "topic_id": topic_id,
        "verification_rate": round(ctx.verification_rate * 100),
        "freshness_score": round(ctx.freshness_score * 100),
        "verified": [
            {
                "claimed": v.claimed_title[:60],
                "actual": v.paper.title[:60] if v.paper else None,
                "arxiv_id": v.paper.arxiv_id if v.paper else None,
                "match": round(v.title_match * 100),
                "issues": v.issues,
            }
            for v in ctx.verified_refs
        ],
        "invalid": [
            {
                "claimed": v.claimed_title[:60],
                "claimed_id": v.claimed_arxiv_id,
                "issues": v.issues,
            }
            for v in ctx.invalid_refs
        ],
        "missing_important": [
            {
                "title": p.title,
                "authors": p.authors[:3],
                "year": p.year,
                "arxiv_id": p.arxiv_id,
                "url": p.abs_url,
            }
            for p in ctx.missing_important
        ],
        "recent": [
            {
                "title": p.title,
                "authors": p.authors[:3],
                "year": p.year,
                "arxiv_id": p.arxiv_id,
                "summary": p.summary[:200],
                "url": p.abs_url,
            }
            for p in ctx.recent_papers
        ],
    }


# ── Research Pipeline ────────────────────────────────────────────────

@router.get("/research/competence")
async def get_competence_map():
    """Build a competence map from MCQ results and Socratic sessions across all topics."""
    topics = await db.get_all_topics()
    done_topics = [t for t in topics if t["status"] == "done"]

    competence = []
    for t in done_topics:
        mcq_responses = await db.get_mcq_responses("default", t["id"])
        total = len(mcq_responses)
        correct = sum(1 for r in mcq_responses if r.get("is_correct"))

        dim_scores: dict[str, dict] = {}
        for r in mcq_responses:
            dim = r.get("dimension", "general")
            if dim not in dim_scores:
                dim_scores[dim] = {"total": 0, "correct": 0}
            dim_scores[dim]["total"] += 1
            dim_scores[dim]["correct"] += 1 if r.get("is_correct") else 0

        misconceptions = await db.get_misconceptions()
        topic_miscon = [m for m in misconceptions if m.get("topic_id") == t["id"]]

        competence.append({
            "topic_id": t["id"],
            "title": t["title"],
            "category": t["category"],
            "assessed": total > 0,
            "mcq_score": round(correct / total * 100) if total > 0 else None,
            "mcq_total": total,
            "dimensions": {
                k: round(v["correct"] / v["total"] * 100)
                for k, v in dim_scores.items()
                if v["total"] > 0
            },
            "misconceptions": [
                {"concept": m.get("sub_concept", ""), "pattern": m.get("claim", "")}
                for m in topic_miscon
            ],
            "connections": t.get("connects_to") or [],
        })

    assessed = [c for c in competence if c["assessed"]]
    avg_score = round(sum(c["mcq_score"] for c in assessed) / len(assessed)) if assessed else None

    strong = [c for c in assessed if (c["mcq_score"] or 0) >= 80]
    weak = [c for c in assessed if (c["mcq_score"] or 0) < 60]
    not_assessed = [c for c in competence if not c["assessed"]]

    return {
        "topics": competence,
        "summary": {
            "total_topics": len(competence),
            "assessed": len(assessed),
            "not_assessed": len(not_assessed),
            "average_score": avg_score,
            "strong_count": len(strong),
            "weak_count": len(weak),
        },
        "ready_for_research": len(assessed) >= 3 and avg_score is not None and avg_score >= 60,
    }


@router.post("/research/directions")
async def generate_research_directions():
    """Use the Teacher model to suggest research directions based on competence map."""
    from agent_server.llm_client import async_databricks_openai
    from agent_server.prompts import RESEARCH_DIRECTION_SYSTEM

    competence = await get_competence_map()

    if not competence.get("ready_for_research"):
        raise HTTPException(
            400,
            "Not enough assessed topics. Complete MCQ challenges on at least 3 topics with 60%+ average."
        )

    topics = await db.get_all_topics()
    done_topics = {t["id"]: t for t in topics if t["status"] == "done"}

    context_parts = ["## Competence Map\n"]
    for c in competence["topics"]:
        if c["assessed"]:
            context_parts.append(
                f"- {c['title']} ({c['category']}): MCQ {c['mcq_score']}% "
                f"| Dimensions: {c['dimensions']}"
            )
            if c["misconceptions"]:
                for m in c["misconceptions"]:
                    context_parts.append(f"  Misconception: {m['concept']} — {m['pattern']}")

    context_parts.append("\n## Topic Summaries\n")
    for c in competence["topics"]:
        t = done_topics.get(c["topic_id"])
        if t and t.get("payload"):
            p = t["payload"]
            if isinstance(p, str):
                try:
                    p = json.loads(p)
                except Exception:
                    continue
            summary = p.get("summary", "")[:300]
            context_parts.append(f"### {c['title']}\n{summary}\n")

    context_parts.append("\n## Topic Connections\n")
    for c in competence["topics"]:
        if c["connections"]:
            context_parts.append(f"- {c['title']} → {', '.join(c['connections'][:5])}")

    try:
        from agent_server.arxiv import get_recent
        assessed_titles = [c["title"] for c in competence["topics"] if c["assessed"]]
        arxiv_lines = ["\n## Recent arXiv Papers (verified, real papers)\n"]
        for title in assessed_titles[:5]:
            papers = await get_recent(title, months_back=12, max_results=3)
            if papers:
                arxiv_lines.append(f"### {title}")
                for p in papers:
                    arxiv_lines.append(
                        f'  - "{p.title}" ({p.year}) arXiv:{p.arxiv_id} — {p.summary[:120]}…'
                    )
        context_parts.extend(arxiv_lines)
    except Exception as e:
        logger.warning("arXiv fetch for research directions failed: %s", e)

    client = async_databricks_openai()
    model = os.environ.get("TEACHER_MODEL", "databricks-claude-sonnet-4-6")

    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": RESEARCH_DIRECTION_SYSTEM},
            {"role": "user", "content": "\n".join(context_parts)},
        ],
        max_tokens=2000,
        response_format=_schemas.response_format(
            "research_directions", _schemas.RESEARCH_DIRECTIONS_SCHEMA),
    )

    raw = (resp.choices[0].message.content or "").strip()

    try:
        result = json.loads(raw)
    except json.JSONDecodeError:
        return {"directions": [], "error": "Failed to parse research directions"}

    return {
        "directions": result.get("directions", []),
        "competence_summary": competence["summary"],
    }


@router.post("/research/scaffold")
async def generate_paper_scaffold(body: dict):
    """Generate a paper scaffold for a chosen research direction."""
    from agent_server.llm_client import async_databricks_openai
    from agent_server.prompts import PAPER_SCAFFOLD_SYSTEM

    direction = body.get("direction", {})
    if not direction.get("title"):
        raise HTTPException(400, "direction.title required")

    topics = await db.get_all_topics()
    done_topics = {t["id"]: t for t in topics if t["status"] == "done"}

    context_parts = [f"## Research Direction\n{json.dumps(direction, indent=2)}\n"]
    context_parts.append("## Source Topic Content\n")

    source_ids = direction.get("builds_on", []) + direction.get("related_work_topics", [])
    for tid in source_ids:
        t = done_topics.get(tid)
        if t and t.get("payload"):
            p = t["payload"]
            if isinstance(p, str):
                try:
                    p = json.loads(p)
                except Exception:
                    continue
            context_parts.append(f"### {t['title']}\n{json.dumps(p)[:3000]}\n")

    client = async_databricks_openai()
    model = os.environ.get("TEACHER_MODEL", "databricks-claude-sonnet-4-6")

    resp = await client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": PAPER_SCAFFOLD_SYSTEM},
            {"role": "user", "content": "\n".join(context_parts)},
        ],
        max_tokens=3000,
        response_format=_schemas.response_format(
            "paper_scaffold", _schemas.PAPER_SCAFFOLD_SCHEMA),
    )

    raw = (resp.choices[0].message.content or "").strip()

    try:
        scaffold = json.loads(raw)
    except json.JSONDecodeError:
        return {"scaffold": None, "error": "Failed to parse scaffold"}

    return {"scaffold": scaffold, "direction": direction}


# ── Status ──────────────────────────────────────────────────────────

@router.get("/status")
async def get_status():
    graph = await db.get_graph_state()
    nodes = list(graph["nodes"].values())
    return {
        "ok": True,
        "teacher": os.getenv("TEACHER_MODEL", ""),
        "student": os.getenv("STUDENT_MODEL", ""),
        "seed": graph["seed"],
        "total": len(nodes),
        "done": sum(1 for n in nodes if n["status"] == "done"),
        "generating": sum(1 for n in nodes if n["status"] == "generating"),
        "queued": sum(1 for n in nodes if n["status"] == "queued"),
        "failed": sum(1 for n in nodes if n["status"] == "failed"),
    }
