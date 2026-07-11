"""Progress tracking for Databricks serverless jobs.

These helpers are intentionally self-contained so job files can run from a
Workspace upload without importing the FastAPI app.
"""

from __future__ import annotations

import json
import os
from typing import Any

import psycopg
from databricks.sdk import WorkspaceClient


def _conninfo() -> str:
    host = os.getenv("PGHOST", "")
    user = os.getenv("PGUSER", "")
    database = os.getenv("PGDATABASE", "databricks_postgres")
    if not host or not user:
        raise RuntimeError("PGHOST and PGUSER are required for job progress tracking")
    return f"host={host} port=5432 dbname={database} user={user} sslmode=require"


def _password() -> str:
    endpoint_name = os.getenv("ENDPOINT_NAME", "")
    if not endpoint_name:
        raise RuntimeError("ENDPOINT_NAME is required for job progress tracking")
    return WorkspaceClient().postgres.generate_database_credential(endpoint=endpoint_name).token


def update_progress(
    run_id: str,
    job_type: str,
    status: str,
    step: str,
    progress: float,
    detail: dict[str, Any] | None = None,
    error: str | None = None,
) -> None:
    schema = os.getenv("GURUKUL_DB_SCHEMA", "gurukul")
    completed_expr = "NOW()" if status in {"succeeded", "failed"} else "NULL"
    payload = json.dumps(detail or {})
    try:
        with psycopg.connect(_conninfo(), password=_password(), autocommit=True) as conn:
            conn.execute(
                f"""
                INSERT INTO {schema}.long_running_jobs
                    (run_id, job_type, status, step, progress, detail, error, completed_at)
                VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s, {completed_expr})
                ON CONFLICT (run_id) DO UPDATE SET
                    status = EXCLUDED.status,
                    step = EXCLUDED.step,
                    progress = EXCLUDED.progress,
                    detail = EXCLUDED.detail,
                    error = EXCLUDED.error,
                    updated_at = NOW(),
                    completed_at = {completed_expr}
                """,
                (run_id, job_type, status, step, progress, payload, error),
            )
    except Exception as e:
        # Serverless base environments can lag the local databricks-sdk and may
        # not expose the Lakebase `.postgres` helper. Progress must never mask
        # the real job error; Databricks run output remains authoritative.
        print(
            f"[progress-warning] Could not write {job_type}/{run_id} "
            f"{status}:{step}: {type(e).__name__}: {e}",
            flush=True,
        )

