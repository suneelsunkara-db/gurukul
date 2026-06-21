"""Server-Sent Events broadcaster for real-time UI updates.

The Docusaurus frontend connects to /api/events and receives graph mutations
(new topics, status changes, thought process steps) in real time.
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any

from starlette.requests import Request
from starlette.responses import StreamingResponse

logger = logging.getLogger(__name__)

_clients: set[asyncio.Queue] = set()


def broadcast(event: str, data: Any) -> None:
    """Send an SSE event to all connected clients."""
    msg = f"event: {event}\ndata: {json.dumps(data)}\n\n"
    dead: list[asyncio.Queue] = []
    for q in _clients:
        try:
            q.put_nowait(msg)
        except asyncio.QueueFull:
            dead.append(q)
    for q in dead:
        _clients.discard(q)
    if _clients:
        logger.debug("SSE broadcast [%s] to %d client(s)", event, len(_clients))


async def sse_endpoint(request: Request) -> StreamingResponse:
    """SSE endpoint: streams graph init + live mutations to the browser."""
    from agent_server.db import GurukuDB

    db = GurukuDB()

    queue: asyncio.Queue = asyncio.Queue(maxsize=256)
    _clients.add(queue)

    async def event_generator():
        try:
            graph = await db.get_graph_state()
            yield f"event: init\ndata: {json.dumps(graph)}\n\n"

            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield msg
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        finally:
            _clients.discard(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
            "Access-Control-Allow-Origin": "*",
        },
    )
