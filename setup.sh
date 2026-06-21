#!/usr/bin/env bash
set -euo pipefail

# ─── Gurukul Setup ──────────────────────────────────────────────────
# One-time setup script that provisions infrastructure, installs deps,
# and prepares for either local dev or Databricks Apps deployment.
#
# Usage:
#   ./setup.sh              # Interactive: prompts for mode
#   ./setup.sh local        # Set up for local development
#   ./setup.sh deploy       # Set up and deploy to Databricks Apps
# ────────────────────────────────────────────────────────────────────

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

# ─── Colors ─────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'

log()  { echo -e "${BLUE}[gurukul]${NC} $1"; }
ok()   { echo -e "${GREEN}  ✓${NC} $1"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $1"; }
err()  { echo -e "${RED}  ✗${NC} $1"; exit 1; }

# ─── Step 1: Check prerequisites ───────────────────────────────────
log "Checking prerequisites..."

command -v node >/dev/null 2>&1 || err "Node.js not found. Install Node 20+: https://nodejs.org"
NODE_VER=$(node -v | sed 's/v//' | cut -d. -f1)
[ "$NODE_VER" -ge 20 ] || err "Node.js 20+ required (found v$NODE_VER)"
ok "Node.js $(node -v)"

command -v npm >/dev/null 2>&1 || err "npm not found"
ok "npm $(npm -v)"

command -v uv >/dev/null 2>&1 || err "uv not found. Install: curl -LsSf https://astral.sh/uv/install.sh | sh"
ok "uv $(uv --version 2>&1 | head -1)"

command -v databricks >/dev/null 2>&1 || err "Databricks CLI not found. Install: brew install databricks"
ok "databricks CLI $(databricks --version 2>&1 | head -1)"

# ─── Step 2: Load .env ─────────────────────────────────────────────
if [ -f .env ]; then
    set -a; source .env; set +a
    ok "Loaded .env"
else
    if [ -f .env.example ]; then
        cp .env.example .env
        warn "Created .env from .env.example — fill in DATABRICKS_HOST, TEACHER_MODEL, STUDENT_MODEL"
        err "Edit .env and re-run ./setup.sh"
    fi
    err ".env not found. Copy .env.example to .env and fill in values."
fi

# Validate required vars
[ -n "${DATABRICKS_HOST:-}" ] || err "DATABRICKS_HOST not set in .env"
[ -n "${TEACHER_MODEL:-}" ]   || err "TEACHER_MODEL not set in .env"
[ -n "${STUDENT_MODEL:-}" ]   || err "STUDENT_MODEL not set in .env"
ok "Config: TEACHER=$TEACHER_MODEL  STUDENT=$STUDENT_MODEL"

# ─── Step 3: Databricks auth (OAuth) ───────────────────────────────
log "Validating Databricks authentication..."

PROFILE_ARG=""
if [ -n "${DATABRICKS_CONFIG_PROFILE:-}" ]; then
    PROFILE_ARG="--profile $DATABRICKS_CONFIG_PROFILE"
fi

if ! databricks auth token --host "$DATABRICKS_HOST" $PROFILE_ARG >/dev/null 2>&1; then
    warn "Not authenticated. Running databricks auth login..."
    databricks auth login --host "$DATABRICKS_HOST" $PROFILE_ARG
fi

DB_TOKEN=$(databricks auth token --host "$DATABRICKS_HOST" $PROFILE_ARG 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin)['access_token'])" 2>/dev/null || true)
[ -n "$DB_TOKEN" ] || err "Failed to get Databricks token. Run: databricks auth login --host $DATABRICKS_HOST"
ok "Databricks OAuth token acquired"

# ─── Step 4: Install dependencies ──────────────────────────────────
log "Installing dependencies..."

log "  Python (uv sync)..."
uv sync 2>&1 | tail -3
ok "Python dependencies installed"

log "  Node.js (npm ci)..."
npm ci 2>&1 | tail -3
ok "Node.js dependencies installed"

# ─── Step 5: Lakebase setup ────────────────────────────────────────
log "Setting up Lakebase (Autoscaling Postgres)..."

[ -n "${PGHOST:-}" ]        || err "PGHOST not set in .env (Lakebase endpoint hostname)"
[ -n "${ENDPOINT_NAME:-}" ] || err "ENDPOINT_NAME not set in .env (projects/<id>/branches/<id>/endpoints/<id>)"
[ -n "${PGUSER:-}" ]        || err "PGUSER not set in .env (your Databricks email for local dev)"
ok "Lakebase: PGHOST=$PGHOST"
ok "Lakebase: ENDPOINT_NAME=$ENDPOINT_NAME"
ok "Lakebase: PGUSER=$PGUSER"

log "  Creating schema and tables..."
uv run python -c "
import asyncio
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(dotenv_path=Path('.env'), override=True)
from agent_server.db import GurukuDB
async def init():
    db = GurukuDB()
    await db.setup_schema_and_grants()
    print('Schema + grants applied successfully')
asyncio.run(init())
" 2>&1 && ok "Lakebase schema ready (permissions granted to all roles)" || warn "Lakebase setup had issues (check connection)"

# ─── Step 6: Build frontend ────────────────────────────────────────
log "Building Vite frontend..."
npm run build 2>&1 | tail -5
ok "Frontend built (./dist)"

# ─── Step 7: Verify evaluation harness ─────────────────────────────
log "Verifying evaluation harness..."

uv run python -c "
from evals.scorers import (
    GroundingScorer, ReferenceIntegrityScorer,
    EpistemicMarkerScorer, ContentStructureScorer,
    ExaminerFairnessScorer,
)
from evals.datasets import (
    build_student_eval_dataset,
    build_teacher_eval_dataset,
    build_examiner_eval_dataset,
)
print('All scorers and dataset builders imported successfully')
" 2>&1 && ok "Evaluation harness verified" || warn "Eval harness import failed (non-blocking)"

ok "Available eval commands:"
echo "    uv run gurukul-eval            # Run all evaluations"
echo "    uv run gurukul-eval student    # Evaluate Student content"
echo "    uv run gurukul-eval teacher    # Evaluate Teacher graph"
echo "    uv run gurukul-eval examiner   # Evaluate Examiner fairness"

# ─── Done ───────────────────────────────────────────────────────────
echo ""
log "════════════════════════════════════════════════════════"
log "  Setup complete!"
log "════════════════════════════════════════════════════════"
echo ""

# ─── Step 8: Run mode ──────────────────────────────────────────────
MODE="${1:-}"

if [ -z "$MODE" ]; then
    echo "  How would you like to run Gurukul?"
    echo ""
    echo "    1) local   - Start locally (Vite + agent server)"
    echo "    2) deploy  - Deploy to Databricks Apps"
    echo "    3) eval    - Run evaluation harness on existing data"
    echo ""
    read -rp "  Choose [1/2/3]: " CHOICE
    case "$CHOICE" in
        1|local)  MODE="local" ;;
        2|deploy) MODE="deploy" ;;
        3|eval)   MODE="eval" ;;
        *)       log "No mode selected. Run manually:"; echo "  Local:  uv run start-app"; echo "  Eval:   uv run gurukul-eval"; echo "  Deploy: databricks bundle deploy && databricks bundle run gurukul"; exit 0 ;;
    esac
fi

case "$MODE" in
    local)
        log "Starting locally..."
        echo ""
        echo "  App:  http://localhost:3000"
        echo "  (Vite proxies /api to backend on :8000)"
        echo ""
        echo "  Run:  npm run dev"
        echo ""
        ;;
    eval)
        log "Running evaluation harness..."
        echo ""
        echo "  This evaluates all generated content against grounding,"
        echo "  consistency, and alignment scorers."
        echo ""
        uv run gurukul-eval
        echo ""
        log "View results in Gurukul UI: Eval Dashboard tab"
        ;;
    deploy)
        log "Deploying to Databricks Apps..."
        echo ""

        log "  Validating bundle..."
        databricks bundle validate $PROFILE_ARG
        ok "Bundle valid"

        log "  Deploying..."
        databricks bundle deploy $PROFILE_ARG
        ok "Bundle deployed"

        log "  Starting app..."
        databricks bundle run gurukul $PROFILE_ARG
        ok "App started"

        echo ""
        log "  App is deploying. Check status:"
        echo "    databricks bundle run gurukul $PROFILE_ARG"
        echo ""
        ;;
    *)
        err "Unknown mode: $MODE. Use 'local', 'deploy', or 'eval'."
        ;;
esac
