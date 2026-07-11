#!/usr/bin/env bash
set -euo pipefail

# ─── Gurukul Lakebase Search setup ───────────────────────────────────
# Installs the Postgres extensions that power grounded retrieval and
# verifies both index types actually build:
#   - vector          (pgvector, dense similarity)
#   - lakebase_vector (lakebase_ann: IVF + RaBitQ ANN, scales to 1B+)
#   - lakebase_text   (lakebase_bm25: true BM25 full-text)
#
# Prerequisite (one-time, irreversible, done in the Databricks UI):
#   Lakebase project Settings -> "Enable Lakebase Search"
#   (sets shared_preload_libraries; without it lakebase_text won't load)
#
# Usage:
#   ./scripts/setup_search.sh
# ─────────────────────────────────────────────────────────────────────

LOG_TAG="search"
source "$(dirname "$0")/_common.sh"
cd "$GURUKUL_ROOT"

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║   Gurukul — Lakebase Search extension setup      ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

step "1/2  Loading config"
gurukul_load_env
command -v uv >/dev/null 2>&1 || err "uv not found"
ok "uv $(uv --version 2>&1 | head -1)"

step "2/2  Installing + verifying extensions"

uv run python3 - <<'PY'
import asyncio
from pathlib import Path
from dotenv import load_dotenv
load_dotenv(dotenv_path=Path(".env"), override=True)

from agent_server.db import _get_pool

EXTS = ["vector", "lakebase_vector", "lakebase_text"]


async def main() -> int:
    pool = await _get_pool()
    failures = 0

    async with pool.connection() as conn:
        await conn.set_autocommit(True)

        # shared_preload_libraries tells us whether the project-level
        # "Enable Lakebase Search" step has been done.
        row = await (await conn.execute("SHOW shared_preload_libraries")).fetchone()
        spl = row[0] if row else ""
        if "lakebase_text" in spl and "lakebase_vector" in spl:
            print(f"  \u2713 Lakebase Search enabled on project (shared_preload_libraries OK)")
        else:
            print(f"  \u26a0 Lakebase Search NOT enabled on project. Enable it in the")
            print(f"     Lakebase project Settings first. shared_preload_libraries={spl!r}")

        # Install extensions (vector as CASCADE dependency of lakebase_vector).
        for ext in EXTS:
            stmt = (
                "CREATE EXTENSION IF NOT EXISTS lakebase_vector CASCADE"
                if ext == "lakebase_vector"
                else f"CREATE EXTENSION IF NOT EXISTS {ext}"
            )
            try:
                await conn.execute(stmt)
                v = await (await conn.execute(
                    "SELECT extversion FROM pg_extension WHERE extname=%s", (ext,)
                )).fetchone()
                print(f"  \u2713 {ext}: installed {v[0] if v else '?'}")
            except Exception as e:
                failures += 1
                print(f"  \u2717 {ext}: {type(e).__name__}: {str(e)[:120]}")

        # Verify both index types actually build (temp table, then drop).
        try:
            await conn.execute(
                "CREATE TABLE IF NOT EXISTS gurukul._search_probe "
                "(id serial primary key, emb vector(1024), tsv tsvector)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS _probe_ann ON gurukul._search_probe "
                "USING lakebase_ann (emb vector_cosine_ops)"
            )
            await conn.execute(
                "CREATE INDEX IF NOT EXISTS _probe_bm25 ON gurukul._search_probe "
                "USING lakebase_bm25 (tsv)"
            )
            print("  \u2713 lakebase_ann + lakebase_bm25 indexes build OK")
        except Exception as e:
            failures += 1
            print(f"  \u2717 index build: {type(e).__name__}: {str(e)[:120]}")
        finally:
            await conn.execute("DROP TABLE IF EXISTS gurukul._search_probe")

    return failures


rc = asyncio.run(main())
raise SystemExit(1 if rc else 0)
PY

if [ $? -eq 0 ]; then
    ok "Lakebase Search ready (vector + lakebase_vector + lakebase_text)"
else
    err "Extension setup had failures (see above)"
fi

echo ""
echo "  Next: ./scripts/deploy_specter2.sh  (embedding endpoint)"
echo ""
