#!/usr/bin/env bash
# ─── Gurukul shared shell helpers ────────────────────────────────────
# Source this from any script for identical logging, .env loading, and
# Databricks auth. Mirrors the conventions in deploy.sh so every script
# looks and behaves the same way.
#
#   Usage (from a script in scripts/ or repo root):
#     source "$(dirname "$0")/_common.sh"     # if the script lives in scripts/
#     gurukul_load_env                          # loads .env, sets PROFILE_ARG
#     gurukul_require_auth                       # ensures Databricks auth
# ─────────────────────────────────────────────────────────────────────

# ─── Colors ──────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; NC='\033[0m'

# Tag prefixing the [name] label. Override before sourcing if desired.
: "${LOG_TAG:=gurukul}"

log()  { echo -e "${BLUE}[${LOG_TAG}]${NC} $1"; }
ok()   { echo -e "${GREEN}  ✓${NC} $1"; }
warn() { echo -e "${YELLOW}  ⚠${NC} $1"; }
err()  { echo -e "${RED}  ✗${NC} $1"; exit 1; }
step() { echo -e "\n${CYAN}━━━ $1 ━━━${NC}"; }

# Resolve the repo root regardless of where the caller script lives.
# Assumes this file is at <root>/scripts/_common.sh.
GURUKUL_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# ─── Load .env + derive the CLI --profile flag ───────────────────────
# Exports every variable defined in .env and sets the global PROFILE_ARG.
gurukul_load_env() {
    if [ -f "$GURUKUL_ROOT/.env" ]; then
        set -a; source "$GURUKUL_ROOT/.env"; set +a
        ok "Loaded .env"
    else
        err ".env not found at $GURUKUL_ROOT/.env"
    fi

    [ -n "${DATABRICKS_HOST:-}" ] || err "DATABRICKS_HOST not set in .env"
    ok "Workspace: $DATABRICKS_HOST"

    PROFILE_ARG=""
    [ -n "${DATABRICKS_CONFIG_PROFILE:-}" ] && PROFILE_ARG="-p $DATABRICKS_CONFIG_PROFILE"
}

# ─── Ensure Databricks CLI is authenticated ──────────────────────────
gurukul_require_auth() {
    command -v databricks >/dev/null 2>&1 || err "Databricks CLI not found"
    if ! databricks auth token --host "$DATABRICKS_HOST" $PROFILE_ARG >/dev/null 2>&1; then
        warn "Not authenticated. Launching browser login..."
        databricks auth login --host "$DATABRICKS_HOST" $PROFILE_ARG
    fi
    ok "Authenticated"
}

# Workspace path for job source uploads. Uses the current user so each dev has
# an isolated staging area.
gurukul_workspace_jobs_path() {
    local app_name="${1:-gurukul}"
    local user
    user=$(databricks current-user me $PROFILE_ARG -o json 2>/dev/null \
        | python3 -c "import sys,json; print(json.load(sys.stdin).get('userName',''))")
    echo "/Workspace/Users/${user}/apps/${app_name}/jobs-src"
}

# Poll a Databricks Jobs run until it terminates. The remote task writes
# structured progress into Lakebase; this monitor keeps the local terminal sane.
gurukul_monitor_run() {
    local run_id="$1"
    local interval="${2:-30}"
    local state result message
    while true; do
        read -r state result message < <(databricks jobs get-run "$run_id" $PROFILE_ARG -o json 2>/dev/null \
            | python3 -c "import sys,json; raw=sys.stdin.read(); \
if not raw.strip(): print('UNKNOWN', '', 'empty Databricks response'); raise SystemExit; \
r=json.loads(raw); s=r.get('state',{}); print(s.get('life_cycle_state','?'), s.get('result_state',''), (s.get('state_message') or '').replace('\n',' ')[:140])")
        log "Run $run_id: $state ${result:+($result)} ${message}"
        case "$state" in
            TERMINATED|SKIPPED|INTERNAL_ERROR)
                [ "$result" = "SUCCESS" ] && return 0
                return 1
                ;;
        esac
        sleep "$interval"
    done
}

# Create or reset a saved Databricks Job by exact name. Saved jobs are preferred
# over jobs submit for long-running work because Databricks can retain settings,
# retry tasks, and apply serverless performance optimization consistently.
gurukul_create_or_reset_job() {
    local job_name="$1"
    local settings_json="$2"
    local job_id
    job_id=$(databricks jobs list --name "$job_name" --limit 1 $PROFILE_ARG -o json 2>/dev/null \
        | python3 -c "import sys,json; d=json.load(sys.stdin); jobs=d if isinstance(d,list) else d.get('jobs', []); print(jobs[0].get('job_id','') if jobs else '')")

    if [ -n "$job_id" ]; then
        local reset_json
        reset_json=$(mktemp)
        python3 - "$job_id" "$settings_json" "$reset_json" <<'PY'
import json, sys
job_id, settings_path, out_path = sys.argv[1:4]
with open(settings_path) as f:
    settings = json.load(f)
with open(out_path, "w") as f:
    json.dump({"job_id": int(job_id), "new_settings": settings}, f)
PY
        if ! databricks jobs reset --json "@$reset_json" $PROFILE_ARG >/dev/null; then
            rm -f "$reset_json"
            err "Failed to update saved job '$job_name'"
        fi
        rm -f "$reset_json"
        ok "Updated saved job '$job_name' ($job_id)"
    else
        job_id=$(databricks jobs create --json "@$settings_json" $PROFILE_ARG -o json \
            | python3 -c "import sys,json; print(json.load(sys.stdin).get('job_id',''))") \
            || err "Failed to create saved job '$job_name'"
        [ -n "$job_id" ] || err "Failed to create saved job '$job_name'"
        ok "Created saved job '$job_name' ($job_id)"
    fi
    echo "$job_id"
}
