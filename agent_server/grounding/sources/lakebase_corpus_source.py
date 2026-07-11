"""Lakebase corpus grounding source."""

from __future__ import annotations

import asyncio
import logging
import os

import psycopg
from databricks.sdk import WorkspaceClient

from agent_server.corpus import hybrid_search
from agent_server.grounding.contracts import GroundingCandidate
from agent_server.grounding.sources.base import GroundingSource, SourceSearchResult

logger = logging.getLogger(__name__)


class LakebaseCorpusSource(GroundingSource):
    name = "lakebase_corpus"
    source_type = "scholarly"

    def _conninfo(self) -> str:
        host = os.getenv("PGHOST", "")
        user = os.getenv("PGUSER", "")
        database = os.getenv("PGDATABASE", "databricks_postgres")
        if not host or not user or not os.getenv("ENDPOINT_NAME"):
            raise RuntimeError("PGHOST, PGUSER, and ENDPOINT_NAME are required")
        return f"host={host} port=5432 dbname={database} user={user} sslmode=require"

    def _password(self) -> str:
        return WorkspaceClient().postgres.generate_database_credential(
            endpoint=os.environ["ENDPOINT_NAME"]
        ).token

    def _search_sync(self, query: str, limit: int) -> SourceSearchResult:
        schema = os.getenv("GURUKUL_DB_SCHEMA", "gurukul")
        with psycopg.connect(self._conninfo(), password=self._password(), autocommit=True) as conn:
            rows = asyncio.run(hybrid_search(conn, schema, query, limit=limit))
        return SourceSearchResult(candidates=[
            GroundingCandidate(
                source_type="scholarly",
                source=self.name,
                title=row["title"],
                abstract=row.get("abstract") or "",
                url=row.get("url"),
                year=row.get("year"),
                external_id=str(row.get("corpus_id")),
                confidence=float(row.get("vector_score") or 0.0),
                metadata={
                    "citation_count": row.get("citation_count"),
                    "text_score": row.get("text_score"),
                    "source": row.get("source"),
                },
            )
            for row in rows
        ])

    async def search(self, query: str, limit: int = 6) -> SourceSearchResult:
        try:
            return await asyncio.to_thread(self._search_sync, query, limit)
        except Exception as e:
            logger.info("Lakebase corpus search skipped for %r: %s", query, e)
            return SourceSearchResult(skipped=True, skip_reason=f"{type(e).__name__}: {e}")

