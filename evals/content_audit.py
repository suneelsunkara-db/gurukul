"""Content quality audit for generated Gurukul topics.

This is a deterministic pre-deploy gate. It checks the content already visible
through the local/API app and fails on unsupported specific claims, missing
research references, or weakly related citations.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
import urllib.error
import urllib.request
from typing import Any

from agent_server.guardrails import sanitize_payload


def _get_json(base_url: str, path: str) -> dict[str, Any]:
    url = base_url.rstrip("/") + path
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as e:
        raise RuntimeError(f"Could not read {url}: {e}") from e


def _as_payload(topic_response: dict[str, Any]) -> dict[str, Any]:
    payload = topic_response.get("payload") or {}
    if isinstance(payload, str):
        return json.loads(payload)
    if not isinstance(payload, dict):
        raise RuntimeError("Topic payload is not a JSON object")
    return payload


def _audit_topic(base_url: str, topic_id: str, min_refs: int) -> list[str]:
    topic_response = _get_json(base_url, f"/api/topic/{topic_id}")
    node = topic_response.get("node") or {}
    title = node.get("title") or topic_id
    payload = _as_payload(topic_response)
    refs = payload.get("references") or []
    ref_titles = [str(r.get("title") or "") for r in refs if isinstance(r, dict)]
    quality_meta = payload.get("_quality") if isinstance(payload.get("_quality"), dict) else {}
    evidence_titles = [str(t) for t in quality_meta.get("evidence_titles", []) if t]
    evidence_snippets = [str(t) for t in quality_meta.get("evidence_snippets", []) if t]
    source_titles = evidence_titles or ref_titles
    source_evidence = evidence_snippets or ref_titles

    _, issues = sanitize_payload(
        payload,
        topic_title=title,
        source_titles=source_titles,
        source_evidence=source_evidence,
    )

    failures: list[str] = []
    if len(refs) < min_refs:
        failures.append(f"{topic_id}: only {len(refs)} references; expected at least {min_refs}")
    for issue in issues:
        if issue.get("severity") == "high":
            failures.append(f"{topic_id}: {issue['message']}")
    weak_refs = [i for i in issues if i.get("type") == "weak_reference_relevance"]
    if len(weak_refs) > max(1, len(refs) // 2):
        failures.append(f"{topic_id}: too many weakly related references ({len(weak_refs)}/{len(refs)})")
    return failures


def main() -> int:
    logging.getLogger("agent_server.guardrails").setLevel(logging.ERROR)

    parser = argparse.ArgumentParser(description="Audit generated Gurukul content quality")
    parser.add_argument("--base-url", default="http://localhost:8000", help="Running Gurukul API base URL")
    parser.add_argument("--min-refs", type=int, default=1, help="Minimum references per completed topic")
    parser.add_argument("--limit", type=int, default=0, help="Optional max topics to audit")
    args = parser.parse_args()

    graph = _get_json(args.base_url, "/api/tree")
    nodes = list((graph.get("nodes") or {}).values())
    done_nodes = [n for n in nodes if n.get("status") == "done"]
    if args.limit:
        done_nodes = done_nodes[:args.limit]

    failures: list[str] = []
    for node in done_nodes:
        failures.extend(_audit_topic(args.base_url, node["id"], args.min_refs))

    print(f"Audited {len(done_nodes)} completed topics from {args.base_url}")
    if failures:
        print("\nContent audit failed")
        for failure in failures[:40]:
            print(f"- {failure}")
        if len(failures) > 40:
            print(f"- ... {len(failures) - 40} more")
        return 1

    print("Content audit passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
