"""Web search service using Tavily for real-time model spec grounding.

Used primarily for comparison chapters to get latest model specifications
that are beyond the LLM's training cutoff.
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

TAVILY_API = "https://api.tavily.com/search"

_cache: dict[str, tuple[float, list[dict]]] = {}
CACHE_TTL = 3600  # 1 hour

MODEL_FAMILIES = [
    {"family": "GPT / o-series", "org": "OpenAI", "query": "OpenAI GPT latest model specifications"},
    {"family": "Claude", "org": "Anthropic", "query": "Anthropic Claude latest model specifications"},
    {"family": "Gemini", "org": "Google DeepMind", "query": "Google Gemini latest model specifications"},
    {"family": "Llama", "org": "Meta", "query": "Meta Llama latest model specifications"},
    {"family": "Qwen", "org": "Alibaba", "query": "Alibaba Qwen latest model specifications"},
    {"family": "DeepSeek", "org": "DeepSeek", "query": "DeepSeek latest model specifications"},
    {"family": "Kimi / Moonshot", "org": "Moonshot AI", "query": "Moonshot AI Kimi latest model specifications"},
    {"family": "Grok", "org": "xAI", "query": "xAI Grok latest model specifications"},
]


def _cache_get(key: str) -> list[dict] | None:
    entry = _cache.get(key)
    if entry and (time.monotonic() - entry[0]) < CACHE_TTL:
        return entry[1]
    if entry:
        del _cache[key]
    return None


def _cache_set(key: str, value: list[dict]) -> None:
    _cache[key] = (time.monotonic(), value)


async def search_tavily(query: str, max_results: int = 5) -> list[dict]:
    """Run a single Tavily search query."""
    api_key = os.environ.get("TAVILY_API_KEY", "")
    if not api_key:
        logger.warning("TAVILY_API_KEY not set — web search disabled")
        return []

    cached = _cache_get(f"tavily:{query}")
    if cached is not None:
        return cached

    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.post(TAVILY_API, json={
                "api_key": api_key,
                "query": query,
                "max_results": max_results,
                "search_depth": "advanced",
                "include_answer": True,
            })
            if resp.status_code != 200:
                logger.warning("Tavily returned %d: %s", resp.status_code, resp.text[:200])
                return []

            data = resp.json()
            results = []

            answer = data.get("answer")
            if answer:
                results.append({
                    "type": "answer",
                    "content": answer,
                    "url": None,
                })

            for r in data.get("results", [])[:max_results]:
                results.append({
                    "type": "result",
                    "title": r.get("title", ""),
                    "content": r.get("content", "")[:500],
                    "url": r.get("url", ""),
                })

            _cache_set(f"tavily:{query}", results)
            return results

    except Exception as e:
        logger.warning("Tavily search failed: %s", e)
        return []


async def search_model_family(family: dict) -> dict:
    """Search for latest specs of a specific model family."""
    year = datetime.now(timezone.utc).year
    query = f"{family['query']} {year}"
    results = await search_tavily(query, max_results=3)

    return {
        "family": family["family"],
        "org": family["org"],
        "results": results,
        "query": query,
    }


async def search_all_model_families() -> list[dict]:
    """Search for latest specs of all known model families in parallel."""
    sem = asyncio.Semaphore(3)

    async def _bounded(fam: dict) -> dict:
        async with sem:
            return await search_model_family(fam)

    results = await asyncio.gather(*[_bounded(f) for f in MODEL_FAMILIES])
    return [r for r in results if r["results"]]


async def search_topic_context(topic_title: str) -> list[dict]:
    """Search for web context related to any topic (not just comparisons)."""
    year = datetime.now(timezone.utc).year
    query = f"{topic_title} LLM research {year}"
    return await search_tavily(query, max_results=3)


def format_web_context(family_results: list[dict]) -> str:
    """Format web search results into a prompt-ready string."""
    if not family_results:
        return ""

    lines = ["\n[WEB SEARCH RESULTS — real-time data, use as ground truth]:"]
    for fr in family_results:
        lines.append(f"\n### {fr['family']} ({fr['org']})")
        for r in fr["results"]:
            if r["type"] == "answer":
                lines.append(f"  Summary: {r['content'][:300]}")
            else:
                lines.append(f"  - {r['title'][:80]}")
                lines.append(f"    {r['content'][:200]}")
                if r.get("url"):
                    lines.append(f"    Source: {r['url']}")

    lines.append(
        "\nUse these web results for latest model specs. "
        "Tag cells sourced from web results as confidence: 'high' with the URL in notes."
    )
    return "\n".join(lines)
