#!/usr/bin/env bash
set -euo pipefail

# ─── Gurukul SPECTER2 embedding endpoint deploy ──────────────────────
# Submits the long-running deployment to a Databricks serverless Job.
# Local shell only stages source, submits the job, and monitors progress.
# The remote job registers the SPECTER2 pyfunc and creates/updates the CPU
# Model Serving endpoint, writing structured progress to Lakebase.
#
# The corpus rides Semantic Scholar's precomputed SPECTER2 vectors, so
# the served variant MUST be SPECTER2 proximity (it is) or query vectors
# won't align with the corpus.
#
# Config (override in .env):
#   EMBEDDING_UC_MODEL   UC model name       (default main.default.gurukul_specter2)
#   EMBEDDING_MODEL      serving endpoint    (default gurukul-specter2-embed)
#
# Usage:
#   ./scripts/deploy_specter2.sh          # submit + monitor
#   ./scripts/deploy_specter2.sh --no-wait # submit and print run id
#   ./scripts/deploy_specter2.sh --dry    # validate + stage only
#   ./scripts/deploy_specter2.sh --configure-only # update saved job, no run
# ─────────────────────────────────────────────────────────────────────

LOG_TAG="specter2"
source "$(dirname "$0")/_common.sh"
cd "$GURUKUL_ROOT"

DRY_RUN=false
CONFIGURE_ONLY=false
WAIT=true
for arg in "$@"; do
    case "$arg" in
        --dry) DRY_RUN=true ;;
        --configure-only) CONFIGURE_ONLY=true ;;
        --no-wait) WAIT=false ;;
        *) err "Unknown argument: $arg" ;;
    esac
done

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║   Gurukul — SPECTER2 embedding endpoint          ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

step "1/4  Prerequisites & config"
gurukul_load_env
gurukul_require_auth

UC_MODEL="${EMBEDDING_UC_MODEL:-partner_demo_catalog.gurukul.specter2_embedder}"
ENDPOINT="${EMBEDDING_MODEL:-gurukul-specter2-embed}"
[ -n "${PGHOST:-}" ] || err "PGHOST not set in .env"
[ -n "${PGUSER:-}" ] || err "PGUSER not set in .env"
[ -n "${ENDPOINT_NAME:-}" ] || err "ENDPOINT_NAME not set in .env"
ok "UC model:  $UC_MODEL"
ok "Endpoint:  $ENDPOINT"

RUN_ID="specter2-$(date +%Y%m%d%H%M%S)"
JOB_NAME="gurukul-specter2-deploy"
WS_PATH="$(gurukul_workspace_jobs_path "gurukul")"
EXPERIMENT_WS_DIR="${WS_PATH}/experiments"
EXPERIMENT_PATH="${EXPERIMENT_WS_DIR#/Workspace}/specter2"
STAGING=$(mktemp -d)
JOB_SETTINGS=$(mktemp)
trap "rm -rf $STAGING $JOB_SETTINGS" EXIT

step "2/4  Staging job source"
rsync -a --exclude='__pycache__' agent_server "$STAGING/"
rsync -a --exclude='__pycache__' jobs "$STAGING/"
rsync -a --exclude='__pycache__' specter2 "$STAGING/"
mkdir -p "$STAGING/scripts"
rsync -a --include='*/' --include='*.sql' --exclude='*' scripts/ "$STAGING/scripts/"
cp pyproject.toml "$STAGING/"
databricks workspace mkdirs "$WS_PATH" $PROFILE_ARG >/dev/null 2>&1 || true
databricks workspace import-dir "$STAGING" "$WS_PATH" --overwrite $PROFILE_ARG >/dev/null
databricks workspace mkdirs "$EXPERIMENT_WS_DIR" $PROFILE_ARG >/dev/null 2>&1 || true
ok "Uploaded serverless job source to $WS_PATH"
ok "MLflow experiment parent ready: $EXPERIMENT_WS_DIR"

if $DRY_RUN; then
    echo ""
    log "Dry run complete. To submit: ./scripts/deploy_specter2.sh"
    exit 0
fi

step "3/4  Creating/updating saved serverless job"
cat > "$JOB_SETTINGS" <<JSON
{
  "name": "${JOB_NAME}",
  "max_concurrent_runs": 1,
  "tasks": [
    {
      "task_key": "deploy_specter2",
      "spark_python_task": {
        "python_file": "${WS_PATH}/jobs/deploy_specter2_job.py",
        "parameters": [
          "--run-id", "${RUN_ID}",
          "--uc-model", "${UC_MODEL}",
          "--endpoint", "${ENDPOINT}",
          "--pg-host", "${PGHOST}",
          "--pg-user", "${PGUSER}",
          "--pg-database", "${PGDATABASE:-databricks_postgres}",
          "--lakebase-endpoint", "${ENDPOINT_NAME}",
          "--db-schema", "${GURUKUL_DB_SCHEMA:-gurukul}",
          "--source-root", "${WS_PATH}",
          "--experiment-name", "${EXPERIMENT_PATH}"
        ]
      },
      "environment_key": "specter2_env",
      "timeout_seconds": 7200,
      "max_retries": 0,
      "min_retry_interval_millis": 120000,
      "retry_on_timeout": true
    }
  ],
  "environments": [
    {
      "environment_key": "specter2_env",
      "spec": {
        "environment_version": "5",
        "dependencies": [
          "psycopg[binary]==3.2.13",
          "torch==2.9.0",
          "transformers==4.57.6",
          "adapters==1.3.0"
        ]
      }
    }
  ]
}
JSON

JOB_ID=$(gurukul_create_or_reset_job "$JOB_NAME" "$JOB_SETTINGS" | tail -1)
[ -n "$JOB_ID" ] || err "Saved job id was empty"

if $CONFIGURE_ONLY; then
    ok "Saved job configured ($JOB_ID); not starting a run"
    echo ""
    echo "  To run after approval:"
    echo "    ./scripts/deploy_specter2.sh --no-wait"
    echo ""
    exit 0
fi

DB_RUN_ID=$(databricks jobs run-now "$JOB_ID" --idempotency-token "$RUN_ID" --no-wait --performance-target PERFORMANCE_OPTIMIZED $PROFILE_ARG -o json \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('run_id',''))")
[ -n "$DB_RUN_ID" ] || err "Job submission did not return a run_id"
ok "Started saved Databricks job $JOB_ID run: $DB_RUN_ID (progress id: $RUN_ID)"

step "4/4  Monitoring"
if $WAIT; then
    gurukul_monitor_run "$DB_RUN_ID" 30 \
        && ok "SPECTER2 deployment job succeeded" \
        || err "SPECTER2 deployment job failed (run id: $DB_RUN_ID)"
else
    ok "Submitted without waiting"
fi

echo ""
echo -e "${GREEN}  SPECTER2 endpoint target: '$ENDPOINT'${NC}"
echo ""
echo "  Structured progress row:"
echo "    SELECT * FROM gurukul.long_running_jobs WHERE run_id = '$RUN_ID';"
echo ""
echo "  Databricks run:"
echo "    databricks jobs get-run $DB_RUN_ID $PROFILE_ARG"
echo ""
