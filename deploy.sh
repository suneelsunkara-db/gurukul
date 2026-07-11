#!/usr/bin/env bash
set -euo pipefail

# ─── Gurukul Databricks App Deploy ───────────────────────────────────
# Builds frontend locally, uploads source + build to workspace,
# then deploys the app. No npm runs on the platform.
#
# Usage:
#   ./deploy.sh              # Deploy to Databricks Apps
#   ./deploy.sh --dry        # Validate only, don't deploy
# ─────────────────────────────────────────────────────────────────────

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$ROOT_DIR"

DRY_RUN=false
[[ "${1:-}" == "--dry" ]] && DRY_RUN=true

APP_NAME="gurukul"

# ─── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

log()  { echo -e "${BLUE}[deploy]${NC} $1"; }
ok()   { echo -e "${GREEN}  ✓${NC} $1"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $1"; }
err()  { echo -e "${RED}  ✗${NC} $1"; exit 1; }
step() { echo -e "\n${CYAN}━━━ $1 ━━━${NC}"; }

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║   Gurukul — Databricks App Deployment            ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

# ─── 1. Prerequisites ───────────────────────────────────────────────
step "1/8  Checking prerequisites"

command -v node >/dev/null 2>&1 || err "Node.js not found. Install Node 20+"
ok "Node.js $(node -v)"
command -v npm >/dev/null 2>&1 || err "npm not found"
ok "npm $(npm -v)"
command -v uv >/dev/null 2>&1 || err "uv not found"
ok "uv $(uv --version 2>&1 | head -1)"
command -v databricks >/dev/null 2>&1 || err "Databricks CLI not found"
ok "Databricks CLI $(databricks --version 2>&1 | head -1)"

# ─── 2. Load config & authenticate ──────────────────────────────────
step "2/8  Loading config and authenticating"

if [ -f .env ]; then
    set -a; source .env; set +a
    ok "Loaded .env"
else
    err ".env not found"
fi

[ -n "${DATABRICKS_HOST:-}" ] || err "DATABRICKS_HOST not set"
ok "Workspace: $DATABRICKS_HOST"

PROFILE_ARG=""
[ -n "${DATABRICKS_CONFIG_PROFILE:-}" ] && PROFILE_ARG="-p $DATABRICKS_CONFIG_PROFILE"

if ! databricks auth token --host "$DATABRICKS_HOST" $PROFILE_ARG >/dev/null 2>&1; then
    warn "Not authenticated. Launching browser login..."
    databricks auth login --host "$DATABRICKS_HOST" $PROFILE_ARG
fi
ok "Authenticated"

# ─── 3. Build frontend locally ──────────────────────────────────────
step "3/8  Building frontend"

npm ci --silent 2>&1
rm -rf build/
npm run build 2>&1 | tail -3
[ -d build ] || err "Frontend build failed"
FILE_COUNT=$(find build -type f | wc -l | tr -d ' ')
ok "Frontend built: $FILE_COUNT files in build/"

if $DRY_RUN; then
    echo ""
    log "Dry run complete. To deploy: ./deploy.sh"
    exit 0
fi

# ─── 4. Lakebase setup (schema + grants) ────────────────────────────
step "4/8  Setting up Lakebase schema & permissions"

uv run python3 -c "
import asyncio
from dotenv import load_dotenv
from pathlib import Path
load_dotenv(dotenv_path=Path('.env'), override=True)
from agent_server.db import GurukuDB

async def setup():
    db = GurukuDB()
    await db.setup_schema_and_grants()
asyncio.run(setup())
" 2>&1 && ok "Lakebase schema ready (permissions granted to all roles)" \
       || warn "Lakebase setup had issues (check connection)"

# ─── 5. Secrets + app resources ─────────────────────────────────────
step "5/8  Configuring secrets & app resources"

SCOPE="gurukul"

# Create secret scope (idempotent — ignore "already exists")
if databricks secrets create-scope "$SCOPE" $PROFILE_ARG >/dev/null 2>&1; then
    ok "Created secret scope '$SCOPE'"
else
    ok "Secret scope '$SCOPE' already exists"
fi

# Store the Tavily key from .env into the scope (never committed to git)
if [ -n "${TAVILY_API_KEY:-}" ]; then
    databricks secrets put-secret "$SCOPE" tavily_api_key \
        --string-value "$TAVILY_API_KEY" $PROFILE_ARG >/dev/null 2>&1 \
        && ok "Stored tavily_api_key in scope '$SCOPE'" \
        || warn "Failed to store tavily_api_key"
else
    warn "TAVILY_API_KEY not set in .env — web search will be disabled"
fi

# Store the Semantic Scholar API key (used for corpus + seed-resolution candidates)
if [ -n "${S2_API_KEY:-}" ]; then
    databricks secrets put-secret "$SCOPE" s2_api_key \
        --string-value "$S2_API_KEY" $PROFILE_ARG >/dev/null 2>&1 \
        && ok "Stored s2_api_key in scope '$SCOPE'" \
        || warn "Failed to store s2_api_key"
else
    warn "S2_API_KEY not set in .env — Semantic Scholar search will be skipped by strict grounding policy"
fi

# Ensure the app exists before attaching resources
if ! databricks apps get "$APP_NAME" $PROFILE_ARG >/dev/null 2>&1; then
    log "Creating app '$APP_NAME'..."
    databricks apps create "$APP_NAME" $PROFILE_ARG 2>&1
    ok "App created"
fi

# Attach all app resources (postgres + serving endpoints + secret).
# apps update replaces the resources list, so all four are declared here.
# Lakebase paths derive from ENDPOINT_NAME to avoid duplicating config.
PG_BRANCH="${ENDPOINT_NAME%/endpoints/*}"
PG_DATABASE="${PG_BRANCH}/databases/databricks-postgres"
EMBEDDING_MODEL="${EMBEDDING_MODEL:-gurukul-specter2-embed}"

RESOURCES_JSON=$(mktemp)
cat > "$RESOURCES_JSON" <<JSON
{
  "name": "${APP_NAME}",
  "resources": [
    {"name": "postgres", "postgres": {"branch": "${PG_BRANCH}", "database": "${PG_DATABASE}", "permission": "CAN_CONNECT_AND_CREATE"}},
    {"name": "teacher_llm", "serving_endpoint": {"name": "${TEACHER_MODEL}", "permission": "CAN_QUERY"}},
    {"name": "student_llm", "serving_endpoint": {"name": "${STUDENT_MODEL}", "permission": "CAN_QUERY"}},
    {"name": "embedding_llm", "serving_endpoint": {"name": "${EMBEDDING_MODEL}", "permission": "CAN_QUERY"}},
    {"name": "tavily-api-key", "secret": {"scope": "${SCOPE}", "key": "tavily_api_key", "permission": "READ"}},
    {"name": "s2-api-key", "secret": {"scope": "${SCOPE}", "key": "s2_api_key", "permission": "READ"}}
  ]
}
JSON

databricks apps update "$APP_NAME" --json "@$RESOURCES_JSON" $PROFILE_ARG >/dev/null 2>&1 \
    && ok "App resources updated (postgres, serving endpoints, secret)" \
    || warn "App resource update had issues (check 'databricks apps get $APP_NAME')"

# ─── 6. Upload source code to workspace ─────────────────────────────
step "6/8  Uploading source code"

# Determine the workspace path for source code. Keep app source isolated from
# serverless job artifacts under jobs-src; Databricks Apps exports the entire
# source-code path recursively during deploy.
WS_BASE="/Workspace/Users/$(databricks current-user me $PROFILE_ARG -o json 2>/dev/null | python3 -c "import sys,json; print(json.load(sys.stdin).get('userName',''))")/apps/${APP_NAME}"
WS_PATH="${WS_BASE}/app-src"

log "Workspace path: $WS_PATH"

# Create a clean staging directory with only what the app needs
STAGING=$(mktemp -d)
trap "rm -rf $STAGING $RESOURCES_JSON" EXIT

# Copy Python backend (exclude __pycache__)
rsync -a --exclude='__pycache__' agent_server "$STAGING/"
rsync -a --exclude='__pycache__' scripts "$STAGING/"
rsync -a --exclude='__pycache__' evals "$STAGING/"
cp pyproject.toml "$STAGING/"
cp app.yaml "$STAGING/"
cp README.md "$STAGING/" 2>/dev/null || true

# Copy pre-built frontend
cp -r build "$STAGING/"

# Copy public assets if they exist
[ -d public ] && cp -r public "$STAGING/"

log "Staging directory contents:"
ls -la "$STAGING/" | tail -15

# Upload to workspace. Delete the app source folder first so stale files such
# as an old uv.lock do not remain in the deployment source.
databricks workspace delete "$WS_PATH" --recursive $PROFILE_ARG 2>/dev/null || true
databricks workspace mkdirs "$WS_PATH" $PROFILE_ARG 2>/dev/null || true
databricks workspace import-dir "$STAGING" "$WS_PATH" --overwrite $PROFILE_ARG 2>&1
ok "Source code uploaded to $WS_PATH"

# ─── 7. Deploy the app ──────────────────────────────────────────────
step "7/8  Deploying app"

# Deploy with source code path (app already created in step 5)
databricks apps deploy "$APP_NAME" --source-code-path "$WS_PATH" $PROFILE_ARG 2>&1
ok "App deployed"

# ─── 8. Get app URL ─────────────────────────────────────────────────
step "8/8  App info"

APP_URL=$(databricks apps get "$APP_NAME" $PROFILE_ARG -o json 2>/dev/null \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('url',''))" 2>/dev/null || echo "")

echo ""
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}  Deployment complete!${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo ""
if [ -n "$APP_URL" ]; then
    echo -e "  ${CYAN}App URL:${NC}  $APP_URL"
else
    echo -e "  ${CYAN}App URL:${NC}  Check workspace → Apps → $APP_NAME"
fi
echo ""
echo "  Useful commands:"
echo "    databricks apps get $APP_NAME $PROFILE_ARG"
echo "    databricks apps logs $APP_NAME $PROFILE_ARG"
echo "    databricks apps stop $APP_NAME $PROFILE_ARG"
echo ""
