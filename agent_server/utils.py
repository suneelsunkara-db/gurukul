"""Shared utilities for the Gurukul agent server."""

from __future__ import annotations

import json
import logging
from typing import Any, AsyncGenerator, AsyncIterator
from uuid import uuid4

from agents.result import StreamEvent
from mlflow.types.responses import ResponsesAgentRequest, ResponsesAgentStreamEvent

logger = logging.getLogger(__name__)


def get_session_id(request: ResponsesAgentRequest) -> str | None:
    if request.context and getattr(request.context, "conversation_id", None):
        return str(request.context.conversation_id)
    if request.custom_inputs and isinstance(request.custom_inputs, dict):
        return request.custom_inputs.get("session_id")
    return None


def _repair_truncated_json(s: str) -> str:
    """Best-effort repair of truncated JSON by closing open structures.

    Walks the string with a small state machine tracking container nesting
    (object/array), string/escape state, and whether the parser is currently
    positioned right after a *complete value*. At each such position it records
    a "safe cut point" plus the closers needed to balance the structure there.

    On truncation we return the longest safe-cut candidate (trimmed + closed),
    which avoids dangling keys like ``{"note"`` that produce invalid JSON.
    """
    stack: list[str] = []  # 'obj' or 'arr'
    in_string = False
    escape = False
    after_colon = False  # inside an object, a value is expected next

    # (cut_index, closing_string) snapshots taken after each complete value.
    safe_points: list[tuple[int, str]] = []

    def closers() -> str:
        return "".join("}" if c == "obj" else "]" for c in reversed(stack))

    def mark_value_complete(idx: int) -> None:
        nonlocal after_colon
        if stack and stack[-1] == "obj":
            after_colon = False
        safe_points.append((idx, closers()))

    i = 0
    n = len(s)
    while i < n:
        ch = s[i]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
                # A closed string is a value only when not an object key.
                if not (stack and stack[-1] == "obj" and not after_colon):
                    mark_value_complete(i + 1)
            i += 1
            continue

        if ch == '"':
            in_string = True
        elif ch == "{":
            stack.append("obj")
            after_colon = False
        elif ch == "[":
            stack.append("arr")
        elif ch in "}]":
            if stack:
                stack.pop()
            mark_value_complete(i + 1)
        elif ch == ":":
            after_colon = True
        elif ch == ",":
            after_colon = False
        elif ch in "0123456789tfn":
            # Start of a primitive (number / true / false / null). Consume it.
            j = i
            while j < n and s[j] not in ',}]" \t\r\n':
                j += 1
            mark_value_complete(j)
            i = j
            continue
        i += 1

    if safe_points:
        cut, closing = safe_points[-1]
        return s[:cut] + closing

    # Nothing complete — just balance whatever is open.
    return s.rstrip().rstrip(",") + closers()


def extract_json(text: str) -> dict[str, Any]:
    """Extract a JSON object from model output, handling markdown fences."""
    trimmed = text.strip()
    starts_with_json = trimmed.startswith("{") or trimmed.startswith("[")

    candidate = text
    if not starts_with_json:
        import re
        fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", text, re.IGNORECASE)
        if fence:
            candidate = fence.group(1)

    start = candidate.find("{")
    if start == -1:
        raise ValueError("No JSON object found in model output.")

    # Fast path: try the full substring first, then progressively shorter
    # complete substrings (handles trailing junk after a valid object).
    for end in range(len(candidate), start, -1):
        s = candidate[start:end]
        if not s.endswith("}"):
            continue
        try:
            return json.loads(s)
        except json.JSONDecodeError:
            continue

    # Salvage path: the output was likely truncated mid-JSON. Repair it.
    repaired = _repair_truncated_json(candidate[start:])
    try:
        return json.loads(repaired)
    except json.JSONDecodeError:
        pass

    raise ValueError("Could not parse JSON from model output.")


async def process_agent_stream_events(
    async_stream: AsyncIterator[StreamEvent],
) -> AsyncGenerator[ResponsesAgentStreamEvent, None]:
    """Convert OpenAI Agents SDK stream events to MLflow ResponsesAgent events."""
    curr_item_id = str(uuid4())
    async for event in async_stream:
        if event.type == "raw_response_event":
            event_data = event.data.model_dump()
            if event_data["type"] == "response.output_item.added":
                curr_item_id = str(uuid4())
                event_data["item"]["id"] = curr_item_id
            elif event_data.get("item") is not None and event_data["item"].get("id") is not None:
                event_data["item"]["id"] = curr_item_id
            elif event_data.get("item_id") is not None:
                event_data["item_id"] = curr_item_id
            yield event_data
        elif event.type == "run_item_stream_event" and event.item.type == "tool_call_output_item":
            yield ResponsesAgentStreamEvent(
                type="response.output_item.done",
                item=event.item.to_input_item(),
            )
