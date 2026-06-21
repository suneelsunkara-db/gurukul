"""Lakebase (managed Postgres) persistence layer for the Gurukul knowledge graph.

Tables:
  topics       – each generated topic node with status, category, and payload
  graph_edges  – conceptual connections between topics
  explorations – seed queries and their decomposition history
  annotations  – user annotations (read status, research seeds, critiques)

Uses an async connection pool with OAuth token rotation per the official
Lakebase Autoscaling pattern. Connections are reused across requests;
a fresh credential is minted only when the pool creates a new connection.

Ref: https://docs.databricks.com/aws/en/oltp/projects/tutorial-databricks-apps-autoscaling
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import psycopg
from databricks.sdk import WorkspaceClient
from psycopg.rows import dict_row
from psycopg_pool import AsyncConnectionPool

logger = logging.getLogger(__name__)

SCHEMA = os.getenv("GURUKUL_DB_SCHEMA", "gurukul")

_ws: WorkspaceClient | None = None
_pool: AsyncConnectionPool | None = None


def _get_ws() -> WorkspaceClient:
    global _ws
    if _ws is None:
        _ws = WorkspaceClient()
    return _ws


@dataclass(frozen=True)
class TopicRow:
    id: str
    title: str
    category: str
    status: str
    position: int
    is_comparison: bool
    rationale: str
    payload: dict | None
    error: str | None
    seed: str | None
    created_at: datetime
    updated_at: datetime


SCHEMA_VERSION = 7

SCHEMA_SQL = f"""
-- Gurukul schema v{SCHEMA_VERSION}
-- Idempotent: safe to run on every startup.

CREATE SCHEMA IF NOT EXISTS {SCHEMA};

-- ── Topics ──────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS {SCHEMA}.topics (
    id            TEXT PRIMARY KEY,
    title         TEXT NOT NULL,
    category      TEXT NOT NULL DEFAULT 'foundations',
    status        TEXT NOT NULL DEFAULT 'queued',
    position      INTEGER NOT NULL DEFAULT 0,
    is_comparison BOOLEAN NOT NULL DEFAULT FALSE,
    rationale     TEXT NOT NULL DEFAULT '',
    payload       JSONB,
    error         TEXT,
    seed          TEXT,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Typed knowledge-graph edges ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS {SCHEMA}.graph_edges (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    edge_type TEXT NOT NULL DEFAULT 'related',
    label     TEXT,
    strength  REAL NOT NULL DEFAULT 0.5,
    PRIMARY KEY (source_id, target_id)
);

-- ── Exploration history ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS {SCHEMA}.explorations (
    id         SERIAL PRIMARY KEY,
    seed       TEXT NOT NULL,
    parent_id  TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── User annotations (notes, bookmarks) ─────────────────────────────
CREATE TABLE IF NOT EXISTS {SCHEMA}.annotations (
    user_id         TEXT NOT NULL DEFAULT 'default',
    topic_id        TEXT NOT NULL,
    annotation_type TEXT NOT NULL,
    data            JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, topic_id, annotation_type)
);

-- ── Socratic challenge sessions ─────────────────────────────────────
-- Each session is a multi-round dialogue testing understanding of a topic.
-- rounds: [{{"question","mode","answer","evaluation":{{"accuracy","depth","reasoning","feedback"}}}}]
CREATE TABLE IF NOT EXISTS {SCHEMA}.challenge_sessions (
    id           SERIAL PRIMARY KEY,
    user_id      TEXT NOT NULL DEFAULT 'default',
    topic_id     TEXT NOT NULL,
    started_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at TIMESTAMPTZ,
    rounds       JSONB NOT NULL DEFAULT '[]'::jsonb,
    final_level  TEXT,
    final_scores JSONB
);

-- ── Evaluation benchmark runs ─────────────────────────────────────
-- Each row is a snapshot of quality scores at a point in time.
-- Enables tracking improvements across regeneration cycles.
CREATE TABLE IF NOT EXISTS {SCHEMA}.eval_runs (
    id           SERIAL PRIMARY KEY,
    run_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trigger      TEXT NOT NULL DEFAULT 'manual',
    overall      INTEGER NOT NULL,
    topic_count  INTEGER NOT NULL,
    strong       INTEGER NOT NULL DEFAULT 0,
    moderate     INTEGER NOT NULL DEFAULT 0,
    weak         INTEGER NOT NULL DEFAULT 0,
    dimensions   JSONB NOT NULL DEFAULT '{{}}'::jsonb,
    per_topic    JSONB NOT NULL DEFAULT '[]'::jsonb,
    suggestions  JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes        TEXT,
    improvements JSONB
);

-- ── Improvement actions (feedback loop) ──────────────────────────────
-- Each row is a single improvement action applied from an eval run's plan.
-- Tracks: what was done, which topics were affected, before/after scores.
CREATE TABLE IF NOT EXISTS {SCHEMA}.eval_actions (
    id           SERIAL PRIMARY KEY,
    eval_run_id  INTEGER NOT NULL,
    dimension    TEXT NOT NULL,
    action_type  TEXT NOT NULL,
    description  TEXT NOT NULL,
    status       TEXT NOT NULL DEFAULT 'pending',
    topic_ids    JSONB NOT NULL DEFAULT '[]'::jsonb,
    before_scores JSONB,
    after_scores  JSONB,
    delta         JSONB,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    started_at   TIMESTAMPTZ,
    completed_at TIMESTAMPTZ
);

-- ── Quality learnings (persistent feedback loop) ─────────────────────
-- Stores quality hints that improved scores during iterative improvement.
-- Applied automatically to ALL future content generation.
CREATE TABLE IF NOT EXISTS {SCHEMA}.quality_learnings (
    id           SERIAL PRIMARY KEY,
    dimension    TEXT NOT NULL,
    hint         TEXT NOT NULL,
    delta_pct    REAL NOT NULL DEFAULT 0,
    source_action_id INTEGER,
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── MCQ questions (generated per topic) ──────────────────────────────
CREATE TABLE IF NOT EXISTS {SCHEMA}.mcq_questions (
    id            SERIAL PRIMARY KEY,
    topic_id      TEXT NOT NULL,
    sub_concept   TEXT NOT NULL,
    dimension     TEXT NOT NULL,
    question      TEXT NOT NULL,
    options       JSONB NOT NULL,
    generated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── MCQ responses ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS {SCHEMA}.mcq_responses (
    id            SERIAL PRIMARY KEY,
    user_id       TEXT NOT NULL DEFAULT 'default',
    question_id   INTEGER NOT NULL,
    topic_id      TEXT NOT NULL,
    selected      TEXT NOT NULL,
    is_correct    BOOLEAN NOT NULL,
    time_ms       INTEGER,
    answered_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Misconceptions (tracked across sessions) ─────────────────────────
CREATE TABLE IF NOT EXISTS {SCHEMA}.misconceptions (
    id             SERIAL PRIMARY KEY,
    user_id        TEXT NOT NULL DEFAULT 'default',
    topic_id       TEXT NOT NULL,
    sub_concept    TEXT NOT NULL,
    claim          TEXT NOT NULL,
    correction     TEXT NOT NULL,
    severity       TEXT NOT NULL DEFAULT 'minor',
    occurrences    INTEGER NOT NULL DEFAULT 1,
    first_seen     TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    last_seen      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    resolved_at    TIMESTAMPTZ
);

-- ── Research directions & paper scaffolds ─────────────────────────────
CREATE TABLE IF NOT EXISTS {SCHEMA}.research_directions (
    id              SERIAL PRIMARY KEY,
    user_id         TEXT NOT NULL DEFAULT 'default',
    title           TEXT NOT NULL,
    source_topics   JSONB NOT NULL,
    open_problems   JSONB NOT NULL DEFAULT '[]'::jsonb,
    hypothesis      TEXT,
    readiness_score REAL NOT NULL DEFAULT 0,
    blocking_gaps   JSONB,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS {SCHEMA}.paper_scaffolds (
    id              SERIAL PRIMARY KEY,
    direction_id    INTEGER NOT NULL,
    user_id         TEXT NOT NULL DEFAULT 'default',
    version         INTEGER NOT NULL DEFAULT 1,
    scaffold        JSONB NOT NULL,
    refinement_log  JSONB DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Indexes ─────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_annotations_user_type
    ON {SCHEMA}.annotations(user_id, annotation_type);
CREATE INDEX IF NOT EXISTS idx_edges_typed
    ON {SCHEMA}.graph_edges(edge_type);
CREATE INDEX IF NOT EXISTS idx_challenge_user_topic
    ON {SCHEMA}.challenge_sessions(user_id, topic_id);
CREATE INDEX IF NOT EXISTS idx_eval_runs_at
    ON {SCHEMA}.eval_runs(run_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_actions_run
    ON {SCHEMA}.eval_actions(eval_run_id);
CREATE INDEX IF NOT EXISTS idx_mcq_topic
    ON {SCHEMA}.mcq_questions(topic_id);
CREATE INDEX IF NOT EXISTS idx_mcq_resp_user
    ON {SCHEMA}.mcq_responses(user_id, topic_id);
CREATE INDEX IF NOT EXISTS idx_misconceptions_active
    ON {SCHEMA}.misconceptions(user_id) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_research_user
    ON {SCHEMA}.research_directions(user_id);

-- ── Migrations (upgrade existing tables to current schema) ──────────
-- Each block is guarded so it's safe to re-run.

-- v2: typed edges
DO $$ BEGIN
    ALTER TABLE {SCHEMA}.graph_edges ADD COLUMN edge_type TEXT NOT NULL DEFAULT 'related';
EXCEPTION WHEN duplicate_column THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE {SCHEMA}.graph_edges ADD COLUMN label TEXT;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE {SCHEMA}.graph_edges ADD COLUMN strength REAL NOT NULL DEFAULT 0.5;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;
"""


def _conninfo_base() -> str:
    """Connection string without password (pool injects a fresh token)."""
    host = os.getenv("PGHOST", "")
    user = os.getenv("PGUSER", "")
    database = os.getenv("PGDATABASE", "databricks_postgres")
    if not host or not user:
        raise ValueError("PGHOST and PGUSER must be set. See .env.example.")
    return f"host={host} port=5432 dbname={database} user={user} sslmode=require"


class _OAuthConnection(psycopg.AsyncConnection):
    """AsyncConnection subclass that injects a fresh OAuth token on connect."""

    @classmethod
    async def connect(cls, conninfo: str = "", **kwargs) -> _OAuthConnection:
        endpoint_name = os.getenv("ENDPOINT_NAME", "")
        if not endpoint_name:
            raise ValueError("ENDPOINT_NAME not set.")
        cred = _get_ws().postgres.generate_database_credential(endpoint=endpoint_name)
        kwargs["password"] = cred.token
        return await super().connect(conninfo, **kwargs)


async def _get_pool() -> AsyncConnectionPool:
    """Lazy-init a shared async connection pool."""
    global _pool
    if _pool is None:
        _pool = AsyncConnectionPool(
            conninfo=_conninfo_base(),
            connection_class=_OAuthConnection,
            min_size=1,
            max_size=8,
            open=False,
        )
        await _pool.open()
        logger.info("Lakebase connection pool opened (min=1, max=8)")
    return _pool


class GurukuDB:
    """Async Lakebase client backed by a shared connection pool.

    Connections are reused across requests. The pool's custom connection
    class mints a fresh OAuth credential whenever a new connection is
    created, so token expiry is handled transparently.
    """

    async def _pool(self) -> AsyncConnectionPool:
        return await _get_pool()

    async def init_tables(self) -> None:
        """Create schema & tables if they don't exist.

        When run by the table owner (local dev, deploy.sh) this executes
        all DDL including indexes.  When run by a non-owner role (app SP
        at runtime) and tables already exist, the DDL is skipped — the
        owner must have already run it via deploy.sh.
        """
        pool = await self._pool()
        async with pool.connection() as conn:
            try:
                await conn.execute(SCHEMA_SQL)
                await conn.commit()
            except Exception as e:
                await conn.rollback()
                if "must be owner" in str(e) or "InsufficientPrivilege" in type(e).__name__:
                    logger.info(
                        "Schema DDL skipped (tables owned by another role). "
                        "Run deploy.sh to apply schema changes."
                    )
                else:
                    raise

        logger.info(
            "Gurukul Lakebase schema v%d ready (schema '%s')",
            SCHEMA_VERSION, SCHEMA,
        )

    async def setup_schema_and_grants(self) -> None:
        """Full schema setup + permission grants. Called from deploy.sh
        and setup.sh where the caller owns the schema."""
        await self.init_tables()
        await self._grant_schema_to_all_app_roles()

    async def _grant_schema_to_all_app_roles(self) -> None:
        """Grant schema access to all non-system Postgres roles.

        When the schema is created by a user during local dev, the
        Databricks App service principal can't access it (and vice versa).
        This ensures both the user role and any app SP roles can use
        the schema and its tables.
        """
        pool = await self._pool()
        try:
            async with pool.connection() as conn:
                rows = await (await conn.execute(
                    "SELECT rolname FROM pg_roles "
                    "WHERE rolname NOT LIKE 'pg_%' "
                    "AND rolname NOT LIKE 'databricks_%' "
                    "AND rolname != 'cloud_admin'"
                )).fetchall()

                for (role,) in rows:
                    quoted = f'"{role}"'
                    await conn.execute(f"GRANT USAGE ON SCHEMA {SCHEMA} TO {quoted}")
                    await conn.execute(f"GRANT CREATE ON SCHEMA {SCHEMA} TO {quoted}")
                    await conn.execute(f"GRANT ALL PRIVILEGES ON ALL TABLES IN SCHEMA {SCHEMA} TO {quoted}")
                    await conn.execute(f"GRANT ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA {SCHEMA} TO {quoted}")
                    await conn.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} GRANT ALL ON TABLES TO {quoted}")
                    await conn.execute(f"ALTER DEFAULT PRIVILEGES IN SCHEMA {SCHEMA} GRANT ALL ON SEQUENCES TO {quoted}")

                await conn.commit()
                role_names = [r[0] for r in rows]
                logger.info("Schema '%s' permissions granted to: %s", SCHEMA, role_names)
        except Exception as e:
            logger.warning("Could not grant schema permissions (non-fatal): %s", e)

    # ── Topics ─────────────────────────────────────────────────────

    async def upsert_topic(
        self,
        *,
        id: str,
        title: str,
        category: str = "foundations",
        status: str = "queued",
        position: int = 0,
        is_comparison: bool = False,
        rationale: str = "",
        seed: str | None = None,
    ) -> None:
        now = datetime.now(timezone.utc)
        pool = await self._pool()
        async with pool.connection() as conn:
            await conn.execute(
                f"""
                INSERT INTO {SCHEMA}.topics
                    (id, title, category, status, position, is_comparison, rationale, seed, created_at, updated_at)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (id) DO UPDATE SET
                    title = EXCLUDED.title, category = EXCLUDED.category,
                    status = EXCLUDED.status, position = EXCLUDED.position,
                    is_comparison = EXCLUDED.is_comparison, rationale = EXCLUDED.rationale,
                    updated_at = EXCLUDED.updated_at
                """,
                (id, title, category, status, position, is_comparison, rationale, seed, now, now),
            )
            await conn.commit()

    async def upsert_topics_batch(
        self,
        topics: list[dict],
        edges: list[dict],
        seed: str | None = None,
        parent_id: str | None = None,
    ) -> None:
        """Insert multiple topics and typed edges in a single transaction.

        Each edge dict: {"source": str, "target": str, "type": str, "label": str|None, "strength": float}
        """
        now = datetime.now(timezone.utc)
        pool = await self._pool()
        async with pool.connection() as conn:
            for t in topics:
                await conn.execute(
                    f"""
                    INSERT INTO {SCHEMA}.topics
                        (id, title, category, status, position, is_comparison, rationale, seed, created_at, updated_at)
                    VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                    ON CONFLICT (id) DO UPDATE SET
                        title = EXCLUDED.title, category = EXCLUDED.category,
                        status = EXCLUDED.status, position = EXCLUDED.position,
                        is_comparison = EXCLUDED.is_comparison, rationale = EXCLUDED.rationale,
                        updated_at = EXCLUDED.updated_at
                    """,
                    (t["id"], t["title"], t["category"], t["status"],
                     t["position"], t["is_comparison"], t["rationale"],
                     seed, now, now),
                )
            for e in edges:
                src, tgt = e["source"], e["target"]
                etype = e.get("type", "related")
                label = e.get("label")
                strength = e.get("strength", 0.5)
                await conn.execute(
                    f"""INSERT INTO {SCHEMA}.graph_edges (source_id, target_id, edge_type, label, strength)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (source_id, target_id) DO UPDATE SET
                            edge_type = EXCLUDED.edge_type, label = EXCLUDED.label, strength = EXCLUDED.strength""",
                    (src, tgt, etype, label, strength),
                )
                reverse_type = etype
                if etype == "prerequisite":
                    reverse_type = "builds_on"
                elif etype == "builds_on":
                    reverse_type = "prerequisite"
                await conn.execute(
                    f"""INSERT INTO {SCHEMA}.graph_edges (source_id, target_id, edge_type, label, strength)
                        VALUES (%s, %s, %s, %s, %s)
                        ON CONFLICT (source_id, target_id) DO UPDATE SET
                            edge_type = EXCLUDED.edge_type, label = EXCLUDED.label, strength = EXCLUDED.strength""",
                    (tgt, src, reverse_type, label, strength),
                )
            if seed is not None:
                await conn.execute(
                    f"INSERT INTO {SCHEMA}.explorations (seed, parent_id) VALUES (%s, %s)",
                    (seed, parent_id),
                )
            await conn.commit()

    async def update_topic_status(
        self, topic_id: str, status: str, error: str | None = None
    ) -> None:
        now = datetime.now(timezone.utc)
        pool = await self._pool()
        async with pool.connection() as conn:
            await conn.execute(
                f"UPDATE {SCHEMA}.topics SET status = %s, error = %s, updated_at = %s WHERE id = %s",
                (status, error, now, topic_id),
            )
            await conn.commit()

    async def store_payload(self, topic_id: str, payload: dict) -> None:
        now = datetime.now(timezone.utc)
        pool = await self._pool()
        async with pool.connection() as conn:
            await conn.execute(
                f"UPDATE {SCHEMA}.topics SET payload = %s::jsonb, status = 'done', updated_at = %s WHERE id = %s",
                (json.dumps(payload), now, topic_id),
            )
            await conn.commit()

    async def get_topic(self, topic_id: str) -> dict | None:
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"SELECT * FROM {SCHEMA}.topics WHERE id = %s", (topic_id,)
            )
            return await cur.fetchone()

    async def get_all_topics(self) -> list[dict]:
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"SELECT * FROM {SCHEMA}.topics ORDER BY position"
            )
            return await cur.fetchall()

    # ── Graph edges ────────────────────────────────────────────────

    async def upsert_edge(
        self,
        source_id: str,
        target_id: str,
        edge_type: str = "related",
        label: str | None = None,
        strength: float = 0.5,
    ) -> None:
        pool = await self._pool()
        async with pool.connection() as conn:
            await conn.execute(
                f"""INSERT INTO {SCHEMA}.graph_edges (source_id, target_id, edge_type, label, strength)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (source_id, target_id) DO UPDATE SET
                        edge_type = EXCLUDED.edge_type, label = EXCLUDED.label, strength = EXCLUDED.strength""",
                (source_id, target_id, edge_type, label, strength),
            )
            reverse_type = edge_type
            if edge_type == "prerequisite":
                reverse_type = "builds_on"
            elif edge_type == "builds_on":
                reverse_type = "prerequisite"
            await conn.execute(
                f"""INSERT INTO {SCHEMA}.graph_edges (source_id, target_id, edge_type, label, strength)
                    VALUES (%s, %s, %s, %s, %s)
                    ON CONFLICT (source_id, target_id) DO UPDATE SET
                        edge_type = EXCLUDED.edge_type, label = EXCLUDED.label, strength = EXCLUDED.strength""",
                (target_id, source_id, reverse_type, label, strength),
            )
            await conn.commit()

    async def get_edges_for_topic(self, topic_id: str) -> list[str]:
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"SELECT target_id FROM {SCHEMA}.graph_edges WHERE source_id = %s",
                (topic_id,),
            )
            rows = await cur.fetchall()
            return [r["target_id"] for r in rows]

    async def get_all_edges(self) -> list[dict]:
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"SELECT source_id, target_id, edge_type, label, strength FROM {SCHEMA}.graph_edges"
            )
            return await cur.fetchall()

    async def find_prerequisite_path(self, from_id: str, to_id: str) -> list[str] | None:
        """BFS over prerequisite edges to find shortest learning path."""
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"SELECT source_id, target_id FROM {SCHEMA}.graph_edges WHERE edge_type = 'prerequisite'"
            )
            rows = await cur.fetchall()

        adj: dict[str, list[str]] = {}
        for r in rows:
            adj.setdefault(r["source_id"], []).append(r["target_id"])

        from collections import deque
        queue: deque[list[str]] = deque([[from_id]])
        visited = {from_id}
        while queue:
            path = queue.popleft()
            node = path[-1]
            if node == to_id:
                return path
            for neighbor in adj.get(node, []):
                if neighbor not in visited:
                    visited.add(neighbor)
                    queue.append(path + [neighbor])
        return None

    # ── Graph state (full, single connection) ──────────────────────

    async def get_graph_state(self) -> dict:
        """Return full graph state in a single pooled connection."""
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur_t = await conn.execute(f"SELECT * FROM {SCHEMA}.topics ORDER BY position")
            topics = await cur_t.fetchall()
            cur_e = await conn.execute(
                f"SELECT source_id, target_id, edge_type, label, strength FROM {SCHEMA}.graph_edges"
            )
            edges = await cur_e.fetchall()

        edge_map: dict[str, list[str]] = {}
        for e in edges:
            edge_map.setdefault(e["source_id"], []).append(e["target_id"])

        # Deduplicate edges (A→B and B→A become one edge object)
        edge_set: set[str] = set()
        typed_edges: list[dict] = []
        for e in edges:
            key = "::".join(sorted([e["source_id"], e["target_id"]]))
            if key not in edge_set:
                edge_set.add(key)
                typed_edges.append({
                    "source": e["source_id"],
                    "target": e["target_id"],
                    "type": e.get("edge_type", "related"),
                    "label": e.get("label"),
                    "strength": e.get("strength", 0.5),
                })

        nodes: dict[str, Any] = {}
        seed = None
        for t in topics:
            if t["seed"] and not seed:
                seed = t["seed"]
            nodes[t["id"]] = {
                "id": t["id"],
                "title": t["title"],
                "category": t["category"],
                "status": t["status"],
                "isComparison": t["is_comparison"],
                "rationale": t["rationale"],
                "connectsTo": edge_map.get(t["id"], []),
                "position": t["position"],
                "error": t["error"],
            }

        return {"nodes": nodes, "edges": typed_edges, "seed": seed}

    # ── Explorations ───────────────────────────────────────────────

    async def record_exploration(self, seed: str, parent_id: str | None = None) -> None:
        pool = await self._pool()
        async with pool.connection() as conn:
            await conn.execute(
                f"INSERT INTO {SCHEMA}.explorations (seed, parent_id) VALUES (%s, %s)",
                (seed, parent_id),
            )
            await conn.commit()

    # ── Annotations ────────────────────────────────────────────────

    async def get_annotations(self, user_id: str = "default") -> dict:
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"SELECT * FROM {SCHEMA}.annotations WHERE user_id = %s",
                (user_id,),
            )
            rows = await cur.fetchall()
            result: dict = {}
            for r in rows:
                key = f"{r['topic_id']}:{r['annotation_type']}"
                result[key] = r["data"]
            return result

    async def upsert_annotation(
        self, topic_id: str, annotation_type: str, data: dict, user_id: str = "default"
    ) -> None:
        now = datetime.now(timezone.utc)
        pool = await self._pool()
        async with pool.connection() as conn:
            await conn.execute(
                f"""
                INSERT INTO {SCHEMA}.annotations
                    (user_id, topic_id, annotation_type, data, updated_at)
                VALUES (%s, %s, %s, %s::jsonb, %s)
                ON CONFLICT (user_id, topic_id, annotation_type) DO UPDATE SET
                    data = EXCLUDED.data, updated_at = EXCLUDED.updated_at
                """,
                (user_id, topic_id, annotation_type, json.dumps(data), now),
            )
            await conn.commit()

    # ── Challenge sessions ─────────────────────────────────────────

    async def create_challenge_session(
        self, topic_id: str, user_id: str = "default"
    ) -> int:
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"""INSERT INTO {SCHEMA}.challenge_sessions (user_id, topic_id)
                    VALUES (%s, %s) RETURNING id""",
                (user_id, topic_id),
            )
            row = await cur.fetchone()
            await conn.commit()
            return row["id"]

    async def get_challenge_session(self, session_id: int) -> dict | None:
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"SELECT * FROM {SCHEMA}.challenge_sessions WHERE id = %s",
                (session_id,),
            )
            return await cur.fetchone()

    async def append_challenge_round(
        self, session_id: int, round_data: dict
    ) -> None:
        pool = await self._pool()
        async with pool.connection() as conn:
            await conn.execute(
                f"""UPDATE {SCHEMA}.challenge_sessions
                    SET rounds = rounds || %s::jsonb
                    WHERE id = %s""",
                (json.dumps([round_data]), session_id),
            )
            await conn.commit()

    async def complete_challenge_session(
        self, session_id: int, final_level: str, final_scores: dict
    ) -> None:
        now = datetime.now(timezone.utc)
        pool = await self._pool()
        async with pool.connection() as conn:
            await conn.execute(
                f"""UPDATE {SCHEMA}.challenge_sessions
                    SET completed_at = %s, final_level = %s, final_scores = %s::jsonb
                    WHERE id = %s""",
                (now, final_level, json.dumps(final_scores), session_id),
            )
            await conn.commit()

    async def get_challenge_history(
        self, topic_id: str, user_id: str = "default"
    ) -> list[dict]:
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"""SELECT id, started_at, completed_at, final_level, final_scores,
                           jsonb_array_length(rounds) as round_count
                    FROM {SCHEMA}.challenge_sessions
                    WHERE user_id = %s AND topic_id = %s
                    ORDER BY started_at DESC""",
                (user_id, topic_id),
            )
            return await cur.fetchall()

    async def get_understanding_map(
        self, user_id: str = "default"
    ) -> dict[str, dict]:
        """Return latest understanding level for each topic with a completed challenge."""
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"""SELECT DISTINCT ON (topic_id)
                        topic_id, final_level, final_scores, completed_at
                    FROM {SCHEMA}.challenge_sessions
                    WHERE user_id = %s AND final_level IS NOT NULL
                    ORDER BY topic_id, completed_at DESC""",
                (user_id,),
            )
            rows = await cur.fetchall()
        return {
            r["topic_id"]: {
                "level": r["final_level"],
                "scores": r["final_scores"],
                "assessed_at": r["completed_at"].isoformat() if r["completed_at"] else None,
            }
            for r in rows
        }

    # ── Eval Runs ───────────────────────────────────────────────────

    async def record_eval_run(
        self,
        *,
        overall: int,
        topic_count: int,
        strong: int,
        moderate: int,
        weak: int,
        dimensions: dict,
        per_topic: list[dict],
        suggestions: list[dict],
        trigger: str = "manual",
        notes: str | None = None,
        improvements: dict | None = None,
    ) -> int:
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"""INSERT INTO {SCHEMA}.eval_runs
                    (trigger, overall, topic_count, strong, moderate, weak,
                     dimensions, per_topic, suggestions, notes, improvements)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                RETURNING id""",
                (
                    trigger, overall, topic_count, strong, moderate, weak,
                    json.dumps(dimensions), json.dumps(per_topic),
                    json.dumps(suggestions), notes,
                    json.dumps(improvements) if improvements else None,
                ),
            )
            row = await cur.fetchone()
            await conn.commit()
        return row["id"]

    async def get_eval_history(self, limit: int = 50, include_per_topic: bool = False) -> list[dict]:
        pool = await self._pool()
        cols = ("id, run_at, trigger, overall, topic_count, "
                "strong, moderate, weak, dimensions, "
                "suggestions, notes, improvements")
        if include_per_topic:
            cols += ", per_topic"
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"SELECT {cols} FROM {SCHEMA}.eval_runs ORDER BY run_at DESC LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        for r in rows:
            if r.get("run_at"):
                r["run_at"] = r["run_at"].isoformat()
        return rows

    async def get_eval_run_detail(self, run_id: int) -> dict | None:
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"SELECT * FROM {SCHEMA}.eval_runs WHERE id = %s",
                (run_id,),
            )
            row = await cur.fetchone()
        if row and row.get("run_at"):
            row["run_at"] = row["run_at"].isoformat()
        return row

    # ── Eval Actions ──────────────────────────────────────────────

    async def create_eval_action(
        self,
        *,
        eval_run_id: int,
        dimension: str,
        action_type: str,
        description: str,
        topic_ids: list[str],
        before_scores: dict | None = None,
    ) -> int:
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"""INSERT INTO {SCHEMA}.eval_actions
                    (eval_run_id, dimension, action_type, description,
                     topic_ids, before_scores, status)
                VALUES (%s, %s, %s, %s, %s, %s, 'pending')
                RETURNING id""",
                (
                    eval_run_id, dimension, action_type, description,
                    json.dumps(topic_ids),
                    json.dumps(before_scores) if before_scores else None,
                ),
            )
            row = await cur.fetchone()
            await conn.commit()
        return row["id"]

    async def update_eval_action_status(
        self, action_id: int, status: str, **extra_fields
    ) -> None:
        pool = await self._pool()
        async with pool.connection() as conn:
            sets = ["status = %s"]
            vals: list = [status]
            if status == "running":
                sets.append("started_at = NOW()")
            elif status in ("done", "failed"):
                sets.append("completed_at = NOW()")
            for k in ("after_scores", "delta"):
                if k in extra_fields:
                    sets.append(f"{k} = %s")
                    vals.append(json.dumps(extra_fields[k]))
            vals.append(action_id)
            await conn.execute(
                f"UPDATE {SCHEMA}.eval_actions SET {', '.join(sets)} WHERE id = %s",
                tuple(vals),
            )
            await conn.commit()

    async def cleanup_stale_actions(self) -> None:
        """Mark any 'running' eval actions as 'failed' on startup (stale from prior crash)."""
        pool = await self._pool()
        async with pool.connection() as conn:
            cur = await conn.execute(
                f"UPDATE {SCHEMA}.eval_actions SET status = 'failed', completed_at = NOW() "
                f"WHERE status = 'running' RETURNING id",
            )
            rows = await cur.fetchall()
            await conn.commit()
            if rows:
                logger.info("Cleaned up %d stale eval actions", len(rows))

    async def get_eval_actions(self, eval_run_id: int) -> list[dict]:
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"SELECT * FROM {SCHEMA}.eval_actions WHERE eval_run_id = %s ORDER BY id",
                (eval_run_id,),
            )
            rows = await cur.fetchall()
        for r in rows:
            for k in ("created_at", "started_at", "completed_at"):
                if r.get(k):
                    r[k] = r[k].isoformat()
        return rows

    async def get_all_eval_actions(self, limit: int = 50) -> list[dict]:
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"SELECT * FROM {SCHEMA}.eval_actions ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            rows = await cur.fetchall()
        for r in rows:
            for k in ("created_at", "started_at", "completed_at"):
                if r.get(k):
                    r[k] = r[k].isoformat()
        return rows

    # ── Quality learnings ──────────────────────────────────────────

    async def save_quality_learning(
        self,
        dimension: str,
        hint: str,
        delta_pct: float,
        source_action_id: int | None = None,
    ) -> int:
        """Persist a quality hint that proved effective during improvement."""
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"""INSERT INTO {SCHEMA}.quality_learnings
                    (dimension, hint, delta_pct, source_action_id)
                    VALUES (%s, %s, %s, %s)
                    RETURNING id""",
                (dimension, hint, delta_pct, source_action_id),
            )
            row = await cur.fetchone()
            await conn.commit()
            return row["id"]

    async def get_active_quality_learnings(self) -> list[dict]:
        """Retrieve all active quality learnings, ordered by effectiveness."""
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"""SELECT dimension, hint, delta_pct
                    FROM {SCHEMA}.quality_learnings
                    WHERE active = TRUE
                    ORDER BY delta_pct DESC""",
            )
            rows = await cur.fetchall()
            return [dict(r) for r in rows]

    # ── MCQ ──────────────────────────────────────────────────────────

    async def store_mcq_questions(self, topic_id: str, questions: list[dict]) -> list[int]:
        """Store generated MCQ questions for a topic. Returns list of IDs."""
        pool = await self._pool()
        ids = []
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            for q in questions:
                cur = await conn.execute(
                    f"""INSERT INTO {SCHEMA}.mcq_questions
                        (topic_id, sub_concept, dimension, question, options)
                        VALUES (%s, %s, %s, %s, %s)
                        RETURNING id""",
                    (topic_id, q["sub_concept"], q["dimension"],
                     q["question"], json.dumps(q["options"])),
                )
                row = await cur.fetchone()
                ids.append(row["id"])
            await conn.commit()
        return ids

    async def get_mcq_questions(self, topic_id: str) -> list[dict]:
        """Get all MCQ questions for a topic."""
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"SELECT * FROM {SCHEMA}.mcq_questions WHERE topic_id = %s ORDER BY id",
                (topic_id,),
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    async def delete_mcq_questions(self, topic_id: str) -> None:
        """Delete existing MCQ questions for a topic (before regenerating)."""
        pool = await self._pool()
        async with pool.connection() as conn:
            await conn.execute(
                f"DELETE FROM {SCHEMA}.mcq_responses WHERE topic_id = %s",
                (topic_id,),
            )
            await conn.execute(
                f"DELETE FROM {SCHEMA}.mcq_questions WHERE topic_id = %s",
                (topic_id,),
            )
            await conn.commit()

    async def store_mcq_response(
        self, user_id: str, question_id: int, topic_id: str,
        selected: str, is_correct: bool, time_ms: int | None = None,
    ) -> int:
        """Store a user's MCQ response."""
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"""INSERT INTO {SCHEMA}.mcq_responses
                    (user_id, question_id, topic_id, selected, is_correct, time_ms)
                    VALUES (%s, %s, %s, %s, %s, %s)
                    RETURNING id""",
                (user_id, question_id, topic_id, selected, is_correct, time_ms),
            )
            row = await cur.fetchone()
            await conn.commit()
            return row["id"]

    async def get_mcq_responses(self, user_id: str, topic_id: str) -> list[dict]:
        """Get all MCQ responses for a user on a topic."""
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"""SELECT r.*, q.dimension, q.sub_concept
                    FROM {SCHEMA}.mcq_responses r
                    JOIN {SCHEMA}.mcq_questions q ON r.question_id = q.id
                    WHERE r.user_id = %s AND r.topic_id = %s
                    ORDER BY r.answered_at DESC""",
                (user_id, topic_id),
            )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Misconceptions ───────────────────────────────────────────────

    async def track_misconception(
        self, user_id: str, topic_id: str, sub_concept: str,
        claim: str, correction: str, severity: str = "minor",
    ) -> int:
        """Track or increment a misconception. Deduplicates by sub_concept."""
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            cur = await conn.execute(
                f"""SELECT id, occurrences FROM {SCHEMA}.misconceptions
                    WHERE user_id = %s AND topic_id = %s AND sub_concept = %s
                    AND resolved_at IS NULL""",
                (user_id, topic_id, sub_concept),
            )
            existing = await cur.fetchone()

            if existing:
                await conn.execute(
                    f"""UPDATE {SCHEMA}.misconceptions
                        SET occurrences = occurrences + 1, last_seen = NOW()
                        WHERE id = %s""",
                    (existing["id"],),
                )
                await conn.commit()
                return existing["id"]
            else:
                cur = await conn.execute(
                    f"""INSERT INTO {SCHEMA}.misconceptions
                        (user_id, topic_id, sub_concept, claim, correction, severity)
                        VALUES (%s, %s, %s, %s, %s, %s)
                        RETURNING id""",
                    (user_id, topic_id, sub_concept, claim, correction, severity),
                )
                row = await cur.fetchone()
                await conn.commit()
                return row["id"]

    async def get_misconceptions(self, user_id: str = "default", resolved: bool = False) -> list[dict]:
        """Get misconceptions, optionally filtered by resolved status."""
        pool = await self._pool()
        async with pool.connection() as conn:
            conn.row_factory = dict_row
            if resolved:
                cur = await conn.execute(
                    f"SELECT * FROM {SCHEMA}.misconceptions WHERE user_id = %s ORDER BY last_seen DESC",
                    (user_id,),
                )
            else:
                cur = await conn.execute(
                    f"""SELECT * FROM {SCHEMA}.misconceptions
                        WHERE user_id = %s AND resolved_at IS NULL
                        ORDER BY occurrences DESC, last_seen DESC""",
                    (user_id,),
                )
            rows = await cur.fetchall()
        return [dict(r) for r in rows]

    # ── Reset ──────────────────────────────────────────────────────

    async def reset(self) -> None:
        pool = await self._pool()
        async with pool.connection() as conn:
            await conn.execute(f"DELETE FROM {SCHEMA}.mcq_responses")
            await conn.execute(f"DELETE FROM {SCHEMA}.mcq_questions")
            await conn.execute(f"DELETE FROM {SCHEMA}.misconceptions")
            await conn.execute(f"DELETE FROM {SCHEMA}.eval_actions")
            await conn.execute(f"DELETE FROM {SCHEMA}.eval_runs")
            await conn.execute(f"DELETE FROM {SCHEMA}.challenge_sessions")
            await conn.execute(f"DELETE FROM {SCHEMA}.graph_edges")
            await conn.execute(f"DELETE FROM {SCHEMA}.annotations")
            await conn.execute(f"DELETE FROM {SCHEMA}.topics")
            await conn.execute(f"DELETE FROM {SCHEMA}.explorations")
            await conn.commit()
        logger.info("Gurukul database reset (quality_learnings preserved)")
