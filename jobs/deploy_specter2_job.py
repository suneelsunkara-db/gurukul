"""Serverless Databricks Job: deploy the SPECTER2 embedding endpoint.

This file is uploaded to Workspace and run remotely by
scripts/deploy_specter2.sh. Local shell execution only submits/monitors the
run; model downloads, UC registration, and endpoint updates happen here.
"""

from __future__ import annotations

import argparse
import os
import sys
import time

from databricks.sdk import WorkspaceClient


def _ensure_uc_schema(ws: WorkspaceClient, uc_model: str) -> None:
    catalog, schema, _ = uc_model.split(".", 2)
    try:
        ws.schemas.create(name=schema, catalog_name=catalog)
        print(f"Created UC schema {catalog}.{schema}")
    except Exception as e:
        msg = str(e).lower()
        if "already exists" not in msg and "resource_already_exists" not in msg:
            raise
        print(f"UC schema {catalog}.{schema} already exists")


def _endpoint_ready(ws: WorkspaceClient, endpoint: str) -> bool:
    try:
        ep = ws.serving_endpoints.get(endpoint)
    except Exception:
        return False
    state = getattr(getattr(ep, "state", None), "ready", None)
    return str(state).upper().endswith("READY")


def _deploy_endpoint(ws: WorkspaceClient, endpoint: str, uc_model: str, version: int) -> None:
    config = {
        "served_entities": [
            {
                "name": "specter2",
                "entity_name": uc_model,
                "entity_version": str(version),
                "workload_size": "Small",
                "workload_type": "CPU",
                "scale_to_zero_enabled": True,
            }
        ]
    }
    try:
        ws.serving_endpoints.get(endpoint)
        print(f"Updating existing endpoint {endpoint} to {uc_model}/{version}")
        ws.api_client.do(
            "PUT",
            f"/api/2.0/serving-endpoints/{endpoint}/config",
            body=config,
        )
    except Exception:
        print(f"Creating endpoint {endpoint} from {uc_model}/{version}")
        ws.api_client.do(
            "POST",
            "/api/2.0/serving-endpoints",
            body={"name": endpoint, "config": config},
        )


def _wait_for_endpoint(
    ws: WorkspaceClient,
    endpoint: str,
    timeout_s: int,
    run_id: str,
    update_progress_fn,
) -> None:
    started = time.monotonic()
    last_state = ""
    while time.monotonic() - started < timeout_s:
        ep = ws.serving_endpoints.get(endpoint)
        state = getattr(getattr(ep, "state", None), "ready", None)
        state_str = str(state)
        if state_str != last_state:
            print(f"Endpoint state: {state_str}")
            last_state = state_str
        update_progress_fn(
            run_id,
            "specter2_deploy",
            "running",
            "waiting_for_endpoint",
            85,
            {"endpoint": endpoint, "state": state_str},
        )
        if state_str.upper().endswith("READY"):
            return
        time.sleep(30)
    raise TimeoutError(f"Endpoint {endpoint} was not READY after {timeout_s}s")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--uc-model", required=True)
    ap.add_argument("--endpoint", required=True)
    ap.add_argument("--pg-host", required=True)
    ap.add_argument("--pg-user", required=True)
    ap.add_argument("--pg-database", default="databricks_postgres")
    ap.add_argument("--lakebase-endpoint", required=True)
    ap.add_argument("--db-schema", default="gurukul")
    ap.add_argument("--source-root", required=True)
    ap.add_argument("--experiment-name", required=True)
    ap.add_argument("--wait-timeout-s", type=int, default=3600)
    args = ap.parse_args()

    if args.source_root not in sys.path:
        sys.path.insert(0, args.source_root)

    from jobs.progress import update_progress
    from specter2.register_specter2 import register_model

    os.environ["PGHOST"] = args.pg_host
    os.environ["PGUSER"] = args.pg_user
    os.environ["PGDATABASE"] = args.pg_database
    os.environ["ENDPOINT_NAME"] = args.lakebase_endpoint
    os.environ["GURUKUL_DB_SCHEMA"] = args.db_schema

    ws = WorkspaceClient()
    try:
        update_progress(args.run_id, "specter2_deploy", "running", "ensure_uc_schema", 5)
        _ensure_uc_schema(ws, args.uc_model)

        update_progress(args.run_id, "specter2_deploy", "running", "download_and_register", 15)
        version = register_model(args.uc_model, experiment_name=args.experiment_name)

        update_progress(
            args.run_id,
            "specter2_deploy",
            "running",
            "create_or_update_endpoint",
            70,
            {"uc_model": args.uc_model, "version": version, "endpoint": args.endpoint},
        )
        _deploy_endpoint(ws, args.endpoint, args.uc_model, version)

        update_progress(
            args.run_id,
            "specter2_deploy",
            "running",
            "waiting_for_endpoint",
            80,
            {"endpoint": args.endpoint},
        )
        _wait_for_endpoint(
            ws,
            args.endpoint,
            args.wait_timeout_s,
            args.run_id,
            update_progress,
        )

        update_progress(
            args.run_id,
            "specter2_deploy",
            "succeeded",
            "ready",
            100,
            {"uc_model": args.uc_model, "endpoint": args.endpoint, "version": version},
        )
    except Exception as e:
        update_progress(
            args.run_id,
            "specter2_deploy",
            "failed",
            "failed",
            100,
            {"uc_model": args.uc_model, "endpoint": args.endpoint},
            error=f"{type(e).__name__}: {e}",
        )
        raise


if __name__ == "__main__":
    main()

