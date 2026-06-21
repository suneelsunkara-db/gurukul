-- Gurukul Lakebase schema
-- Run against the Lakebase Postgres database to create all required tables.

CREATE SCHEMA IF NOT EXISTS gurukul;

CREATE TABLE IF NOT EXISTS gurukul.topics (
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

CREATE TABLE IF NOT EXISTS gurukul.graph_edges (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    PRIMARY KEY (source_id, target_id)
);

CREATE TABLE IF NOT EXISTS gurukul.explorations (
    id         SERIAL PRIMARY KEY,
    seed       TEXT NOT NULL,
    parent_id  TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

CREATE TABLE IF NOT EXISTS gurukul.annotations (
    user_id         TEXT NOT NULL DEFAULT 'default',
    topic_id        TEXT NOT NULL,
    annotation_type TEXT NOT NULL,
    data            JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, topic_id, annotation_type)
);
