"""Gurukul agent: Teacher/Student knowledge graph builder.

Uses OpenAI Agents SDK with MLflow ResponsesAgent interface.
The Teacher decomposes seed topics into a knowledge graph.
Student agents generate content for each topic concurrently.
All tool calls stream as thought-process events to the UI.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import time
from datetime import datetime
from typing import AsyncGenerator
from uuid import uuid4

import mlflow
from agents import Agent, Runner, function_tool, set_default_openai_api, set_default_openai_client
from databricks_openai import AsyncDatabricksOpenAI
from mlflow.genai.agent_server import invoke, stream
from mlflow.types.responses import (
    ResponsesAgentRequest,
    ResponsesAgentResponse,
    ResponsesAgentStreamEvent,
)

from agent_server.db import GurukuDB
from agent_server.guardrails import sanitize_payload, validate_examiner_output
from agent_server.prompts import (
    BRANCH_PROMPT,
    CATEGORIES,
    DECOMPOSE_PROMPT,
    DEEPEN_SYSTEM,
    SCHEMA_HINT_TOPIC,
    STUDENT_SYSTEM,
    TEACHER_SYSTEM,
)
from agent_server.sse import broadcast
from agent_server.utils import extract_json, get_session_id, process_agent_stream_events

logger = logging.getLogger(__name__)

set_default_openai_client(AsyncDatabricksOpenAI())
set_default_openai_api("chat_completions")

mlflow.openai.autolog(log_traces=True)
logging.getLogger("mlflow.utils.autologging_utils").setLevel(logging.ERROR)
# Silence noisy span-cleanup warnings ("Failed to end span ... unhashable type: 'list'")
# which are a tracing quirk and do not affect generation.
logging.getLogger("mlflow.entities.span").setLevel(logging.ERROR)

TEACHER_MODEL = os.getenv("TEACHER_MODEL", "")
STUDENT_MODEL = os.getenv("STUDENT_MODEL", "")
CONCURRENCY = int(os.getenv("AGENT_CONCURRENCY", "4"))

db = GurukuDB()

VALID_CATEGORIES = set(CATEGORIES)


def valid_category(s: str | None) -> str:
    if s and s in VALID_CATEGORIES:
        return s
    return "foundations"


def slugify(s: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", s.lower().strip()).strip("-")[:80]
    return slug or f"topic-{int(datetime.now().timestamp())}"


# ── Core logic (callable from both tools and API routes) ──────────


async def _get_model_context() -> str:
    """Fetch latest model families via web search, fall back to static list."""
    try:
        from agent_server.web_search import search_tavily
        results = await search_tavily("latest frontier LLM models released 2026 GPT Claude Gemini Llama", max_results=5)
        if results:
            lines = ["Based on latest web search results:"]
            for r in results[:5]:
                lines.append(f"- {r.get('title', '')}: {r.get('content', '')[:200]}")
            return "\n".join(lines)
    except Exception as e:
        logger.debug("Web search for model context failed: %s", e)
    return (
        "Use the latest generation you know for each major model family "
        "(OpenAI, Anthropic, Google, Meta, Alibaba/Qwen, DeepSeek, Mistral, etc.). "
        "If unsure of the very latest version, note that explicitly."
    )


async def do_decompose(seed: str, parent_id: str | None = None) -> str:
    """Decompose a seed topic into a knowledge graph using the Teacher model."""
    broadcast("thought", {
        "step": "decompose",
        "message": f"Teacher is mapping the knowledge landscape for '{seed}'...",
        "model": TEACHER_MODEL,
    })

    from datetime import date
    current_date = date.today().strftime("%B %Y")
    current_year = date.today().year

    model_context_task = asyncio.create_task(_get_model_context())

    graph_state = await db.get_graph_state()
    existing = [
        {"id": n["id"], "title": n["title"], "category": n["category"]}
        for n in graph_state["nodes"].values()
    ]

    model_context = await model_context_task

    is_branch = parent_id is not None
    parent_title = None
    if is_branch and parent_id:
        node_data = graph_state["nodes"].get(parent_id)
        parent_title = node_data["title"] if node_data else seed

    decompose_prompt_filled = DECOMPOSE_PROMPT.format(
        current_date=current_date,
        current_year=current_year,
        model_context=model_context,
    )

    if is_branch:
        prompt_parts = [
            f'Parent topic: "{parent_title}"',
            f'Exploration direction: "{seed}"',
            "",
            "Existing topics in the knowledge graph:",
            "\n".join(f"- [{t['category']}] {t['title']} ({t['id']})" for t in existing) if existing else "(none)",
            "",
            BRANCH_PROMPT,
        ]
    else:
        prompt_parts = [
            f'Seed topic: "{seed}"',
            "",
            "Existing topics in the knowledge graph:",
            "\n".join(f"- [{t['category']}] {t['title']} ({t['id']})" for t in existing) if existing else "(none)",
            "",
            decompose_prompt_filled,
        ]

    teacher = Agent(
        name="Teacher",
        instructions=TEACHER_SYSTEM,
        model=TEACHER_MODEL,
    )

    t0 = time.monotonic()
    result = await Runner.run(
        teacher,
        input=[{"role": "user", "content": "\n".join(prompt_parts)}],
    )
    teacher_ms = int((time.monotonic() - t0) * 1000)
    logger.info("Teacher decompose completed in %dms (model=%s)", teacher_ms, TEACHER_MODEL)

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
    topics = [t for t in parsed.get("topics", []) if t.get("title")]
    raw_edges = parsed.get("edges", [])

    broadcast("thought", {
        "step": "decompose_done",
        "message": f"Teacher identified {len(topics)} topics and {len(raw_edges)} connections.",
        "topics": [t.get("title", "") for t in topics],
    })

    base_position = len(graph_state["nodes"])

    # Build ID mapping (raw teacher IDs may need deduplication)
    id_map: dict[str, str] = {}
    db_topics = []
    created_ids = []
    nodes_to_broadcast = []

    for i, t in enumerate(topics):
        raw_id = t.get("id") or slugify(t["title"])
        topic_id = raw_id if raw_id not in graph_state["nodes"] else f"{raw_id}-{int(datetime.now().timestamp())}"
        id_map[raw_id] = topic_id

        db_topics.append({
            "id": topic_id,
            "title": t["title"],
            "category": valid_category(t.get("category")),
            "status": "queued",
            "position": base_position + i + 1,
            "is_comparison": bool(t.get("is_comparison")),
            "rationale": t.get("rationale", ""),
        })
        created_ids.append(topic_id)

    # Build typed edges, resolving IDs through the map
    valid_edge_types = {"prerequisite", "builds_on", "contrasts", "applies", "related"}
    all_edges = []
    edges_to_broadcast = []
    for e in raw_edges:
        src_raw = e.get("source", "")
        tgt_raw = e.get("target", "")
        src = id_map.get(src_raw, src_raw)
        tgt = id_map.get(tgt_raw, tgt_raw)
        if src == tgt:
            continue
        etype = e.get("type", "related")
        if etype not in valid_edge_types:
            etype = "related"
        label = e.get("label")
        strength = max(0.0, min(1.0, float(e.get("strength", 0.5))))

        edge_dict = {"source": src, "target": tgt, "type": etype, "label": label, "strength": strength}
        all_edges.append(edge_dict)
        edges_to_broadcast.append(edge_dict)

    # Collect connectsTo for node broadcasts from edges
    connects_map: dict[str, list[str]] = {}
    for e in all_edges:
        connects_map.setdefault(e["source"], []).append(e["target"])
        connects_map.setdefault(e["target"], []).append(e["source"])

    for t_db in db_topics:
        nodes_to_broadcast.append({
            "id": t_db["id"],
            "title": t_db["title"],
            "category": t_db["category"],
            "status": "queued",
            "isComparison": t_db["is_comparison"],
            "rationale": t_db["rationale"],
            "connectsTo": list(set(connects_map.get(t_db["id"], []))),
            "position": t_db["position"],
        })

    await db.upsert_topics_batch(db_topics, all_edges, seed=seed, parent_id=parent_id)

    for node in nodes_to_broadcast:
        broadcast("node", node)
    for edge in edges_to_broadcast:
        broadcast("edge", edge)
    broadcast("explore:done", {"parentId": parent_id})

    return json.dumps({"created": len(created_ids), "topic_ids": created_ids})


@function_tool
async def decompose_topic(seed: str, parent_id: str | None = None) -> str:
    """Decompose a seed topic into a knowledge graph using the Teacher model."""
    return await do_decompose(seed, parent_id)


async def do_generate_topic_content(topic_id: str, quality_hint: str | None = None) -> str:
    """Generate content for a single topic using the Student model."""
    topic = await db.get_topic(topic_id)
    if not topic:
        return json.dumps({"error": f"Topic {topic_id} not found"})

    broadcast("thought", {
        "step": "generate_start",
        "topic_id": topic_id,
        "message": f"Student is writing '{topic['title']}'...",
        "model": STUDENT_MODEL,
    })

    await db.update_topic_status(topic_id, "generating")
    broadcast("status", {"id": topic_id, "status": "generating", "error": None})

    graph_state = await db.get_graph_state()
    prior_topics = [
        f"[{n['category']}] {n['title']}"
        for n in graph_state["nodes"].values()
        if n["status"] == "done"
    ]

    edges = await db.get_edges_for_topic(topic_id)
    connected_titles = []
    for eid in edges:
        enode = graph_state["nodes"].get(eid)
        if enode:
            connected_titles.append(enode["title"])

    from datetime import date
    current_date = date.today().strftime("%B %Y")

    user_parts = [
        f'Topic: "{topic["title"]}"',
        f'Category: {topic["category"]}',
        f'Current date: {current_date}',
    ]
    if connected_titles:
        user_parts.append(f'Connected to: {", ".join(connected_titles)}')
    if prior_topics:
        user_parts.append(f'\nTopics already covered:\n- {chr(10).join("- " + t for t in prior_topics)}')
    arxiv_context = ""
    web_context = ""

    broadcast("thought", {
        "step": "web_arxiv_fetch",
        "topic_id": topic_id,
        "message": f"Fetching sources via web search + arXiv for '{topic['title']}'...",
    })

    try:
        from agent_server.arxiv import search as arxiv_search, find_related
        from agent_server.web_search import search_tavily, format_web_context

        if topic["is_comparison"]:
            from agent_server.web_search import search_all_model_families
            web_task = search_all_model_families()
            arxiv_task = arxiv_search("frontier large language model technical report", max_results=5)
        else:
            web_task = search_tavily(f"{topic['title']} LLM research {current_date}", max_results=5)
            arxiv_task = find_related(topic["title"], "", max_results=5)

        web_results, arxiv_papers = await asyncio.gather(web_task, arxiv_task)

        if isinstance(web_results, list) and web_results:
            if topic["is_comparison"]:
                web_context = format_web_context(web_results)
            else:
                web_lines = ["\n[WEB SOURCES — recent information]:"]
                for r in web_results:
                    title = r.get("title", "")
                    content = r.get("content", "")[:300]
                    url = r.get("url", "")
                    if title:
                        web_lines.append(f"  - {title}: {content} (Source: {url})")
                web_context = "\n".join(web_lines)
            broadcast("thought", {
                "step": "web_search_done",
                "topic_id": topic_id,
                "message": f"Web search returned {len(web_results)} results",
            })

        if arxiv_papers:
            arxiv_lines = ["\n[ARXIV PAPERS — verified research publications]:"]
            for p in arxiv_papers:
                arxiv_lines.append(
                    f'  - "{p.title}" by {", ".join(p.authors[:3])} '
                    f"({p.year}) arXiv:{p.arxiv_id}"
                )
            arxiv_context = "\n".join(arxiv_lines)
            broadcast("thought", {
                "step": "arxiv_fetch",
                "topic_id": topic_id,
                "message": f"Found {len(arxiv_papers)} papers from arXiv",
            })

    except Exception as e:
        logger.warning("Web/arXiv fetch failed for '%s': %s", topic["title"], e)

    if topic["is_comparison"]:
        user_parts.append(
            "\nThis is a COMPARISON chapter. Populate model_comparison."
            "\nUse the LATEST generation of each model family."
            "\nEvery cell MUST have confidence + source note."
        )

    user_parts.append(
        "\nWrite a deep, technical chapter. 3-4 key aspects with mechanisms and trade-offs, "
        "1-2 gists, 2-3 open problems, up to 5 references. Keep under 5000 tokens."
    )
    if web_context:
        user_parts.append(web_context)
    if arxiv_context:
        user_parts.append(arxiv_context)
    if arxiv_context or web_context:
        user_parts.append(
            "\nIMPORTANT: Use the [WEB] and [ARXIV] sources above as ground truth. "
            "Integrate findings into your explanations — don't just list them. "
            "Do NOT invent paper titles or arXiv IDs. "
            "Tag each comparison cell with its source."
        )
    all_hints = []
    if quality_hint:
        all_hints.append(quality_hint)

    try:
        stored_learnings = await db.get_active_quality_learnings()
        for learning in stored_learnings:
            if learning["hint"] not in all_hints:
                all_hints.append(learning["hint"])
    except Exception as e:
        logger.warning("Could not load quality learnings: %s", e)

    if all_hints:
        user_parts.append("\nQUALITY REQUIREMENTS (from prior improvement cycles):")
        for i, h in enumerate(all_hints, 1):
            user_parts.append(f"\n[{i}] {h}")

    user_parts.append("")
    user_parts.append(SCHEMA_HINT_TOPIC)
    user_parts.append("\nReturn strict JSON only.")

    student = Agent(
        name="Student",
        instructions=STUDENT_SYSTEM,
        model=STUDENT_MODEL,
    )

    try:
        t0 = time.monotonic()
        result = await Runner.run(
            student,
            input=[{"role": "user", "content": "\n".join(user_parts)}],
        )
        student_ms = int((time.monotonic() - t0) * 1000)

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

        payload = extract_json(response_text)

        # ── Pass 2: Deepen — self-critique and add missing depth ──
        aspects = payload.get("key_aspects", [])
        needs_deepening = any(
            len((a.get("body") or "").split()) < 80 for a in aspects
        )
        if needs_deepening and not topic["is_comparison"]:
            broadcast("thought", {
                "step": "deepen_start",
                "topic_id": topic_id,
                "message": "Deepening content — adding mechanisms, trade-offs, and failure modes...",
                "model": STUDENT_MODEL,
            })
            try:
                deepener = Agent(
                    name="Deepener",
                    instructions=DEEPEN_SYSTEM,
                    model=STUDENT_MODEL,
                )
                deepen_prompt = (
                    f'Topic: "{topic["title"]}"\n\n'
                    f"Draft content (JSON):\n{json.dumps(payload, indent=2)[:6000]}\n\n"
                    "Review each key_aspect. Add mechanism explanations, trade-offs, "
                    "and failure modes where missing. Expand thin aspects to 2-3 paragraphs. "
                    "Return the COMPLETE improved JSON (same schema)."
                )
                if arxiv_context:
                    deepen_prompt += f"\n\nUse these papers for grounding:\n{arxiv_context}"
                if web_context:
                    deepen_prompt += f"\n\nRecent web sources:\n{web_context[:2000]}"

                t1 = time.monotonic()
                deep_result = await Runner.run(
                    deepener,
                    input=[{"role": "user", "content": deepen_prompt}],
                )
                deep_ms = int((time.monotonic() - t1) * 1000)

                deep_text = ""
                for item in deep_result.new_items:
                    if hasattr(item, "text"):
                        deep_text = item.text
                        break
                    raw_item = item.to_input_item()
                    if isinstance(raw_item, dict) and raw_item.get("type") == "message":
                        for c in raw_item.get("content", []):
                            if isinstance(c, dict) and c.get("type") == "output_text":
                                deep_text = c.get("text", "")
                                break

                deep_payload = extract_json(deep_text)
                if deep_payload.get("key_aspects"):
                    payload = deep_payload
                    broadcast("thought", {
                        "step": "deepen_done",
                        "topic_id": topic_id,
                        "message": f"Deepened content in {deep_ms/1000:.1f}s",
                    })
                else:
                    logger.warning("Deepen pass returned invalid JSON for '%s'", topic["title"][:40])
            except Exception as e:
                logger.warning("Deepen pass failed for '%s': %s", topic["title"][:40], e)

        if topic["is_comparison"] and payload.get("model_comparison"):
            mc = payload["model_comparison"]
            downgraded = 0
            for row in mc.get("rows", []):
                for model, cell in row.get("cells", {}).items():
                    if isinstance(cell, dict):
                        note = (cell.get("note") or "").lower()
                        has_source = any(s in note for s in ["http", "arxiv", "paper", "report", "blog", "official", "model card"])
                        if not has_source and cell.get("confidence") in ("high", "medium"):
                            cell["confidence"] = "medium" if cell["confidence"] == "high" else "low"
                            downgraded += 1
            if downgraded:
                broadcast("thought", {
                    "step": "confidence_check",
                    "topic_id": topic_id,
                    "message": f"Downgraded {downgraded} comparison cells without source attribution",
                })

        payload, guardrail_issues = sanitize_payload(payload)
        if guardrail_issues:
            high_issues = [i for i in guardrail_issues if i["severity"] == "high"]
            med_issues = [i for i in guardrail_issues if i["severity"] == "medium"]
            logger.info(
                "Guardrails for '%s': %d high, %d medium issues",
                topic['title'][:40], len(high_issues), len(med_issues),
            )
            broadcast("thought", {
                "step": "guardrail_check",
                "topic_id": topic_id,
                "message": f"Guardrails flagged {len(guardrail_issues)} issues "
                           f"({len(high_issues)} high severity). "
                           f"Auto-fixed where possible.",
                "issues": [i["message"] for i in guardrail_issues[:5]],
            })

        await db.store_payload(topic_id, payload)

        logger.info("Student '%s' completed in %dms (model=%s)", topic['title'][:40], student_ms, STUDENT_MODEL)
        broadcast("thought", {
            "step": "generate_done",
            "topic_id": topic_id,
            "message": f"Student finished '{topic['title']}' in {student_ms/1000:.1f}s: "
                       f"{len(payload.get('key_aspects', []))} aspects, "
                       f"{len(payload.get('open_problems', []))} problems.",
        })
        broadcast("status", {"id": topic_id, "status": "done", "error": None})

        return json.dumps({
            "topic_id": topic_id,
            "status": "done",
            "aspects": len(payload.get("key_aspects", [])),
        })

    except Exception as e:
        error_msg = str(e)
        await db.update_topic_status(topic_id, "failed", error_msg)
        broadcast("thought", {
            "step": "generate_failed",
            "topic_id": topic_id,
            "message": f"Student failed on '{topic['title']}': {error_msg[:200]}",
        })
        broadcast("status", {"id": topic_id, "status": "failed", "error": error_msg})
        return json.dumps({"topic_id": topic_id, "status": "failed", "error": error_msg})


@function_tool
async def generate_topic_content(topic_id: str) -> str:
    """Generate content for a single topic using the Student model."""
    return await do_generate_topic_content(topic_id)


async def _generate_batch(topic_ids: list[str]) -> list[str]:
    """Generate content for multiple topics concurrently with controlled concurrency."""
    semaphore = asyncio.Semaphore(CONCURRENCY)

    async def gen_one(tid: str) -> str:
        async with semaphore:
            return await do_generate_topic_content(tid)

    tasks = [asyncio.create_task(gen_one(tid)) for tid in topic_ids]
    results = await asyncio.gather(*tasks, return_exceptions=True)
    return [str(r) for r in results]


async def do_generate_all_queued() -> str:
    """Generate content for all queued topics concurrently."""
    graph_state = await db.get_graph_state()
    queued_ids = [
        n["id"] for n in graph_state["nodes"].values()
        if n["status"] == "queued"
    ]

    if not queued_ids:
        return json.dumps({"message": "No queued topics to generate"})

    broadcast("thought", {
        "step": "batch_start",
        "message": f"Starting generation for {len(queued_ids)} queued topics (concurrency={CONCURRENCY})...",
    })

    t0 = time.monotonic()
    results = await _generate_batch(queued_ids)
    batch_ms = int((time.monotonic() - t0) * 1000)

    logger.info("Batch generation: %d topics in %dms (concurrency=%d)", len(results), batch_ms, CONCURRENCY)
    broadcast("thought", {
        "step": "batch_done",
        "message": f"Batch complete: {len(results)} topics in {batch_ms/1000:.1f}s.",
    })

    return json.dumps({"processed": len(results)})


@function_tool
async def generate_all_queued() -> str:
    """Generate content for all queued topics concurrently."""
    return await do_generate_all_queued()


# ── Main Gurukul Agent ─────────────────────────────────────────────

def create_gurukul_agent() -> Agent:
    """Create the main orchestrator agent with exploration tools."""
    return Agent(
        name="Gurukul",
        instructions="""You are Gurukul, an AI research exploration agent. When the user provides a topic:

1. First, call decompose_topic to map the knowledge landscape using the Teacher model.
2. Then, call generate_all_queued to have Student agents write content for each topic concurrently.
3. Report the results: how many topics were created and generated.

If the user asks to explore deeper from a specific topic, use decompose_topic with the parent_id.
Always be transparent about what you're doing — the user can see your tool calls as thought steps.""",
        model=TEACHER_MODEL,
        tools=[decompose_topic, generate_topic_content, generate_all_queued],
    )


# ── MLflow ResponsesAgent handlers ────────────────────────────────


@invoke()
async def invoke_handler(request: ResponsesAgentRequest) -> ResponsesAgentResponse:
    if session_id := get_session_id(request):
        mlflow.update_current_trace(metadata={"mlflow.trace.session": session_id})

    agent = create_gurukul_agent()
    messages = [i.model_dump() for i in request.input]
    result = await Runner.run(agent, messages)
    return ResponsesAgentResponse(
        output=[item.to_input_item() for item in result.new_items]
    )


@stream()
async def stream_handler(
    request: ResponsesAgentRequest,
) -> AsyncGenerator[ResponsesAgentStreamEvent, None]:
    if session_id := get_session_id(request):
        mlflow.update_current_trace(metadata={"mlflow.trace.session": session_id})

    agent = create_gurukul_agent()
    messages = [i.model_dump() for i in request.input]
    result = Runner.run_streamed(agent, input=messages)

    async for event in process_agent_stream_events(result.stream_events()):
        yield event
