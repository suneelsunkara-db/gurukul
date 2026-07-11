-- Generated from agent_server.db.SCHEMA_SQL. Do not edit manually; update db.py instead.

-- Gurukul schema v10
-- Idempotent: safe to run on every startup.

CREATE SCHEMA IF NOT EXISTS gurukul;

-- ── pgvector (dense embeddings for grounded retrieval) ──────────────
-- The `vector` type backs corpus_papers.embedding. The lakebase_vector /
-- lakebase_text extensions (ANN + BM25 index types) are installed by
-- scripts/setup_search.sh with autocommit so a not-yet-enabled project
-- can't abort this schema transaction. `vector` is always available.
CREATE EXTENSION IF NOT EXISTS vector;

-- ── Topics ──────────────────────────────────────────────────────────
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

-- ── Typed knowledge-graph edges ─────────────────────────────────────
CREATE TABLE IF NOT EXISTS gurukul.graph_edges (
    source_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    edge_type TEXT NOT NULL DEFAULT 'related',
    label     TEXT,
    strength  REAL NOT NULL DEFAULT 0.5,
    PRIMARY KEY (source_id, target_id)
);

-- ── Exploration history ─────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gurukul.explorations (
    id         SERIAL PRIMARY KEY,
    seed       TEXT NOT NULL,
    parent_id  TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── User annotations (notes, bookmarks) ─────────────────────────────
CREATE TABLE IF NOT EXISTS gurukul.annotations (
    user_id         TEXT NOT NULL DEFAULT 'default',
    topic_id        TEXT NOT NULL,
    annotation_type TEXT NOT NULL,
    data            JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    PRIMARY KEY (user_id, topic_id, annotation_type)
);

-- ── Socratic challenge sessions ─────────────────────────────────────
-- Each session is a multi-round dialogue testing understanding of a topic.
-- rounds: [{"question","mode","answer","evaluation":{"accuracy","depth","reasoning","feedback"}}]
CREATE TABLE IF NOT EXISTS gurukul.challenge_sessions (
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
CREATE TABLE IF NOT EXISTS gurukul.eval_runs (
    id           SERIAL PRIMARY KEY,
    run_at       TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    trigger      TEXT NOT NULL DEFAULT 'manual',
    overall      INTEGER NOT NULL,
    topic_count  INTEGER NOT NULL,
    strong       INTEGER NOT NULL DEFAULT 0,
    moderate     INTEGER NOT NULL DEFAULT 0,
    weak         INTEGER NOT NULL DEFAULT 0,
    dimensions   JSONB NOT NULL DEFAULT '{}'::jsonb,
    per_topic    JSONB NOT NULL DEFAULT '[]'::jsonb,
    suggestions  JSONB NOT NULL DEFAULT '[]'::jsonb,
    notes        TEXT,
    improvements JSONB
);

-- ── Improvement actions (feedback loop) ──────────────────────────────
-- Each row is a single improvement action applied from an eval run's plan.
-- Tracks: what was done, which topics were affected, before/after scores.
CREATE TABLE IF NOT EXISTS gurukul.eval_actions (
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
CREATE TABLE IF NOT EXISTS gurukul.quality_learnings (
    id           SERIAL PRIMARY KEY,
    dimension    TEXT NOT NULL,
    hint         TEXT NOT NULL,
    delta_pct    REAL NOT NULL DEFAULT 0,
    source_action_id INTEGER,
    active       BOOLEAN NOT NULL DEFAULT TRUE,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── MCQ questions (generated per topic) ──────────────────────────────
CREATE TABLE IF NOT EXISTS gurukul.mcq_questions (
    id            SERIAL PRIMARY KEY,
    topic_id      TEXT NOT NULL,
    sub_concept   TEXT NOT NULL,
    dimension     TEXT NOT NULL,
    question      TEXT NOT NULL,
    options       JSONB NOT NULL,
    generated_at  TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── MCQ responses ────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS gurukul.mcq_responses (
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
CREATE TABLE IF NOT EXISTS gurukul.misconceptions (
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
CREATE TABLE IF NOT EXISTS gurukul.research_directions (
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

CREATE TABLE IF NOT EXISTS gurukul.paper_scaffolds (
    id              SERIAL PRIMARY KEY,
    direction_id    INTEGER NOT NULL,
    user_id         TEXT NOT NULL DEFAULT 'default',
    version         INTEGER NOT NULL DEFAULT 1,
    scaffold        JSONB NOT NULL,
    refinement_log  JSONB DEFAULT '[]'::jsonb,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Judge calibration (ablation self-test results) ───────────────────
-- For each judge-scored dimension, records whether the judge can actually
-- discriminate quality: we degrade good content and check the score drops.
-- discriminative_power = avg score drop (0-100) when that dimension is ablated.
-- A dimension is 'calibrated' only if the drop exceeds the threshold; the
-- feedback loop refuses to save learnings for uncalibrated dimensions.
CREATE TABLE IF NOT EXISTS gurukul.judge_calibration (
    dimension            TEXT PRIMARY KEY,
    discriminative_power REAL NOT NULL DEFAULT 0,
    calibrated           BOOLEAN NOT NULL DEFAULT FALSE,
    sample_size          INTEGER NOT NULL DEFAULT 0,
    detail               JSONB NOT NULL DEFAULT '{}'::jsonb,
    updated_at           TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Grounded-retrieval corpus (scientific papers) ────────────────────
-- Field-scoped corpus for seed resolution + Research Mentor novelty/
-- coverage. embedding is SPECTER2 proximity (VECTOR(768)); tsv is
-- title(A) + abstract(B). The lakebase_ann / lakebase_bm25 indexes are
-- built by jobs/corpus_build.py AFTER bulk load (BM25 stats are computed
-- at index-build time), not here.
CREATE TABLE IF NOT EXISTS gurukul.corpus_papers (
    corpus_id      BIGINT PRIMARY KEY,
    arxiv_id       TEXT,
    doi            TEXT,
    title          TEXT NOT NULL,
    abstract       TEXT,
    authors        JSONB,
    venue          TEXT,
    year           INTEGER,
    fields         TEXT[],
    citation_count INTEGER NOT NULL DEFAULT 0,
    references_ids BIGINT[],
    url            TEXT,
    source         TEXT NOT NULL DEFAULT 's2',
    embedding      vector(768),
    tsv            TSVECTOR,
    ingested_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Seed resolutions (transparency + threshold tuning) ───────────────
-- One row per resolved seed: what it resolved to (entity/concept/unknown)
-- and the evidence (top candidates, similarity distribution) behind it.
CREATE TABLE IF NOT EXISTS gurukul.seed_resolutions (
    id            SERIAL PRIMARY KEY,
    seed          TEXT NOT NULL,
    resolved_type TEXT NOT NULL,
    entities      JSONB NOT NULL DEFAULT '[]'::jsonb,
    evidence      JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at    TIMESTAMPTZ NOT NULL DEFAULT NOW()
);

-- ── Long-running job progress (serverless jobs) ─────────────────────
-- Local shell scripts submit long-running work to Databricks Jobs; the
-- remote job writes progress here so failures are resumable and visible.
CREATE TABLE IF NOT EXISTS gurukul.long_running_jobs (
    run_id        TEXT PRIMARY KEY,
    job_type      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'queued',
    step          TEXT NOT NULL DEFAULT '',
    progress      REAL NOT NULL DEFAULT 0,
    detail        JSONB NOT NULL DEFAULT '{}'::jsonb,
    error         TEXT,
    started_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    updated_at    TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    completed_at  TIMESTAMPTZ
);

-- ── Indexes ─────────────────────────────────────────────────────────
CREATE INDEX IF NOT EXISTS idx_annotations_user_type
    ON gurukul.annotations(user_id, annotation_type);
CREATE INDEX IF NOT EXISTS idx_edges_typed
    ON gurukul.graph_edges(edge_type);
CREATE INDEX IF NOT EXISTS idx_challenge_user_topic
    ON gurukul.challenge_sessions(user_id, topic_id);
CREATE INDEX IF NOT EXISTS idx_eval_runs_at
    ON gurukul.eval_runs(run_at DESC);
CREATE INDEX IF NOT EXISTS idx_eval_actions_run
    ON gurukul.eval_actions(eval_run_id);
CREATE INDEX IF NOT EXISTS idx_mcq_topic
    ON gurukul.mcq_questions(topic_id);
CREATE INDEX IF NOT EXISTS idx_mcq_resp_user
    ON gurukul.mcq_responses(user_id, topic_id);
CREATE INDEX IF NOT EXISTS idx_misconceptions_active
    ON gurukul.misconceptions(user_id) WHERE resolved_at IS NULL;
CREATE INDEX IF NOT EXISTS idx_research_user
    ON gurukul.research_directions(user_id);
CREATE INDEX IF NOT EXISTS idx_corpus_year
    ON gurukul.corpus_papers(year);
CREATE INDEX IF NOT EXISTS idx_corpus_arxiv
    ON gurukul.corpus_papers(arxiv_id);
CREATE INDEX IF NOT EXISTS idx_seed_resolutions_seed
    ON gurukul.seed_resolutions(seed);
CREATE INDEX IF NOT EXISTS idx_long_jobs_type_updated
    ON gurukul.long_running_jobs(job_type, updated_at DESC);

-- ── Migrations (upgrade existing tables to current schema) ──────────
-- Each block is guarded so it's safe to re-run.

-- v2: typed edges
DO $$ BEGIN
    ALTER TABLE gurukul.graph_edges ADD COLUMN edge_type TEXT NOT NULL DEFAULT 'related';
EXCEPTION WHEN duplicate_column THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE gurukul.graph_edges ADD COLUMN label TEXT;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;
DO $$ BEGIN
    ALTER TABLE gurukul.graph_edges ADD COLUMN strength REAL NOT NULL DEFAULT 0.5;
EXCEPTION WHEN duplicate_column THEN NULL; END $$;
