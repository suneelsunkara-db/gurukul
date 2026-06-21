"""Start the Gurukul app: install deps, build frontend, start agent server.

This is the single entry point for both local dev and Databricks Apps.
  Local:  uv run start-app
  DABs:   command: ["uv", "run", "start-app"]

Steps:
  1. Install Node.js dependencies (npm ci)
  2. Build Vite frontend (if build/ missing)
  3. Validate required env vars
  4. Start the Python agent server (FastAPI + MLflow AgentServer)
     - Lakebase tables are initialized on server startup via lifespan hook
"""

import os
import shutil
import socket
import subprocess
import sys
from pathlib import Path

from dotenv import load_dotenv


def _root() -> Path:
    return Path(__file__).resolve().parent.parent


def _log(msg: str) -> None:
    print(f"[gurukul] {msg}", flush=True)


def _run(cmd: list[str], cwd: Path | None = None, env: dict | None = None) -> None:
    """Run a subprocess, streaming output. Exits on failure."""
    merged_env = {**os.environ, **(env or {})}
    result = subprocess.run(cmd, cwd=cwd or _root(), env=merged_env)
    if result.returncode != 0:
        _log(f"FAILED: {' '.join(cmd)} (exit {result.returncode})")
        sys.exit(result.returncode)


def _check_port(port: int) -> bool:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(1)
            s.connect(("localhost", port))
        return False
    except (ConnectionRefusedError, OSError):
        return True


def _is_databricks_app() -> bool:
    return bool(os.getenv("DATABRICKS_APP_NAME"))


def step_load_env() -> None:
    """Load .env for local dev. In Databricks Apps, env vars are injected."""
    env_path = _root() / ".env"
    if env_path.exists():
        load_dotenv(dotenv_path=env_path, override=True)
        _log("Loaded .env")


def step_validate_env() -> None:
    """Check that required config is present."""
    missing = []

    if not os.getenv("TEACHER_MODEL"):
        missing.append("TEACHER_MODEL")
    if not os.getenv("STUDENT_MODEL"):
        missing.append("STUDENT_MODEL")

    if not os.getenv("PGHOST") and not _is_databricks_app():
        missing.append("PGHOST")
    if not os.getenv("ENDPOINT_NAME") and not _is_databricks_app():
        missing.append("ENDPOINT_NAME")

    has_auth = (
        os.getenv("DATABRICKS_TOKEN")
        or os.getenv("DATABRICKS_HOST")
        or _is_databricks_app()
    )
    if not has_auth:
        missing.append("DATABRICKS_HOST (or DATABRICKS_TOKEN)")

    if missing:
        _log(f"ERROR: Missing required env vars: {', '.join(missing)}")
        _log("  Copy .env.example to .env and fill in the values.")
        sys.exit(1)

    _log(f"Config: TEACHER_MODEL={os.getenv('TEACHER_MODEL')}, STUDENT_MODEL={os.getenv('STUDENT_MODEL')}")


def step_install_node_deps() -> None:
    """Install Node.js dependencies if node_modules is missing."""
    build_dir = _root() / "build"
    if build_dir.exists():
        _log("Frontend already built — skipping npm install")
        return

    node_modules = _root() / "node_modules"
    if node_modules.exists():
        _log("Node.js dependencies already installed")
        return

    if not shutil.which("npm"):
        _log("WARNING: npm not found on PATH. Skipping Node.js dependency install.")
        _log("  Install Node.js 20+ to build the Vite frontend.")
        return

    _log("Installing Node.js dependencies...")
    _run(["npm", "ci"])


def step_build_frontend() -> None:
    """Build Vite frontend if dist/ doesn't exist."""
    build_dir = _root() / "build"
    if build_dir.exists():
        _log("Frontend build already exists")
        return

    if not shutil.which("npm"):
        _log("WARNING: npm not found. Skipping frontend build.")
        return

    _log("Building Vite frontend...")
    _run(["npm", "run", "build"])


def step_verify_eval_harness() -> None:
    """Verify the evaluation harness can be imported."""
    try:
        from evals.scorers import GroundingScorer, ExaminerFairnessScorer  # noqa: F401
        from evals.datasets import build_student_eval_dataset  # noqa: F401
        _log("Evaluation harness available (run: uv run gurukul-eval)")
    except ImportError as e:
        _log(f"WARNING: Eval harness not fully available: {e}")
        _log("  This is non-blocking. Install evals deps if needed.")


def step_check_port() -> None:
    """Verify the server port is available."""
    port = int(os.getenv("APP_PORT", os.getenv("PORT", "8000")))
    if not _check_port(port):
        _log(f"ERROR: Port {port} is already in use.")
        _log(f"  Free it: lsof -ti :{port} | xargs kill -9")
        sys.exit(1)
    _log(f"Port {port} is available")


def step_start_server() -> None:
    """Start the FastAPI agent server (Lakebase init happens in server lifespan)."""
    _log("Starting agent server...")
    from agent_server.start_server import main as start_server
    start_server()


def main() -> None:
    _log("=" * 50)
    _log("Gurukul - Self-evolving LLM Research Knowledge Graph")
    _log("=" * 50)

    step_load_env()
    step_validate_env()
    step_install_node_deps()
    step_build_frontend()
    step_verify_eval_harness()

    if not _is_databricks_app():
        step_check_port()

    step_start_server()


if __name__ == "__main__":
    main()
