"""Agent server entry point. load_dotenv must run before agent imports."""

# ruff: noqa: E402
import os
from contextlib import asynccontextmanager
from pathlib import Path

from dotenv import load_dotenv

load_dotenv(dotenv_path=Path(__file__).parent.parent / ".env", override=True)

import logging

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from mlflow.genai.agent_server import AgentServer, setup_mlflow_git_based_version_tracking
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from agent_server.db import GurukuDB
from agent_server.routes import router as api_router

import agent_server.agent  # noqa: F401 — registers @invoke/@stream handlers

logger = logging.getLogger(__name__)

log_level = os.getenv("LOG_LEVEL", "INFO")
logging.getLogger("agent_server").setLevel(getattr(logging, log_level.upper(), logging.INFO))


async def _init_db():
    """Create Lakebase tables at startup and clean stale state."""
    try:
        db = GurukuDB()
        await db.init_tables()
        logger.info("Lakebase tables ready")
        await db.cleanup_stale_actions()
    except Exception as exc:
        error_msg = str(exc).lower()
        if any(kw in error_msg for kw in ["lakebase", "pg_hba", "postgres", "database"]):
            logger.error("Lakebase setup failed: %s", exc)
        else:
            logger.error("Database setup failed: %s", exc, exc_info=True)
        raise


agent_server = AgentServer("ResponsesAgent", enable_chat_proxy=False)

_original_lifespan = agent_server.app.router.lifespan_context


@asynccontextmanager
async def _lifespan(app):
    await _init_db()
    async with _original_lifespan(app):
        yield


agent_server.app.router.lifespan_context = _lifespan

if not os.getenv("DATABRICKS_APP_NAME"):
    _ALLOWED_ORIGIN = "http://localhost:3000"

    class CORSMiddleware(BaseHTTPMiddleware):
        """Lightweight CORS that doesn't buffer streaming responses."""

        async def dispatch(self, request: Request, call_next):
            if request.method == "OPTIONS":
                return Response(
                    status_code=200,
                    headers={
                        "Access-Control-Allow-Origin": _ALLOWED_ORIGIN,
                        "Access-Control-Allow-Methods": "GET, POST, PUT, DELETE, OPTIONS",
                        "Access-Control-Allow-Headers": "*",
                        "Access-Control-Max-Age": "3600",
                    },
                )
            response = await call_next(request)
            response.headers["Access-Control-Allow-Origin"] = _ALLOWED_ORIGIN
            return response

    agent_server.app.add_middleware(CORSMiddleware)

agent_server.app.include_router(api_router)

build_dir = Path(__file__).parent.parent / "build"
if build_dir.exists():
    agent_server.app.mount("/", StaticFiles(directory=str(build_dir), html=True), name="static")

app = agent_server.app

if Path(__file__).parent.parent.joinpath(".git").exists():
    setup_mlflow_git_based_version_tracking()
else:
    logger.info("Skipping git-based version tracking (no .git directory)")


def main():
    agent_server.run(app_import_string="agent_server.start_server:app")
