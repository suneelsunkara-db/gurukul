"""Databricks OpenAI-compatible client helpers.

Model endpoints are hosted on Databricks, so authentication must flow through
Databricks unified auth/OAuth via WorkspaceClient. We do not configure a
separate model API key.
"""

from __future__ import annotations

import logging
import os

from databricks.sdk import WorkspaceClient
from databricks_openai import AsyncDatabricksOpenAI
from openai import AsyncOpenAI

logger = logging.getLogger(__name__)


def async_databricks_openai() -> AsyncOpenAI:
    ws = WorkspaceClient()
    host = (ws.config.host or "").rstrip("/")
    base_url = os.getenv("DATABRICKS_OPENAI_BASE_URL", "").rstrip("/") or None
    if not host or not base_url:
        logger.info(
            "Configuring Databricks OpenAI client (auth_type=%s, host=%s)",
            getattr(ws.config, "auth_type", None),
            host,
        )
        return AsyncDatabricksOpenAI(workspace_client=ws)

    logger.info(
        "Configuring Databricks OpenAI client (auth_type=%s, host=%s, base_url=%s)",
        getattr(ws.config, "auth_type", None),
        host,
        base_url,
    )
    return AsyncDatabricksOpenAI(workspace_client=ws, base_url=base_url)
