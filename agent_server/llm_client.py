"""Databricks OpenAI-compatible client helpers.

This avoids depending on the `databricks-openai` package at app deploy time.
Databricks Model Serving exposes an OpenAI-compatible chat completions API at
`/serving-endpoints`, and the Databricks SDK already knows how to obtain auth.
"""

from __future__ import annotations

from databricks.sdk import WorkspaceClient
from openai import AsyncOpenAI


def async_databricks_openai() -> AsyncOpenAI:
    ws = WorkspaceClient()
    host = (ws.config.host or "").rstrip("/")
    headers = ws.config.authenticate()
    auth = headers.get("Authorization", "")
    token = auth.removeprefix("Bearer ").strip()
    if not host or not token:
        raise RuntimeError("Could not configure Databricks OpenAI client")
    return AsyncOpenAI(api_key=token, base_url=f"{host}/serving-endpoints")
