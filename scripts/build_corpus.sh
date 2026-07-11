#!/usr/bin/env bash
set -euo pipefail

# ─── Gurukul corpus build / indexing job ─────────────────────────────
# Submits corpus ingestion/embedding/indexing to Databricks serverless Jobs.
# Local shell only stages source and monitors the run.
#
# Usage:
#   ./scripts/build_corpus.sh           # submit + monitor
#   ./scripts/build_corpus.sh --no-wait # submit only
#   ./scripts/build_corpus.sh --dry     # validate + stage only
# ─────────────────────────────────────────────────────────────────────

LOG_TAG="corpus"
source "$(dirname "$0")/_common.sh"
cd "$GURUKUL_ROOT"

DRY_RUN=false
WAIT=true
QUERIES_OVERRIDE=""
LIMIT_OVERRIDE=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --dry) DRY_RUN=true ;;
        --no-wait) WAIT=false ;;
        --queries)
            [[ $# -ge 2 ]] || err "--queries requires a value"
            QUERIES_OVERRIDE="$2"
            shift
            ;;
        --limit-per-query)
            [[ $# -ge 2 ]] || err "--limit-per-query requires a value"
            LIMIT_OVERRIDE="$2"
            shift
            ;;
        *) err "Unknown argument: $1" ;;
    esac
    shift
done

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║   Gurukul — Corpus serverless build              ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

step "1/4  Prerequisites & config"
gurukul_load_env
gurukul_require_auth
[ -n "${PGHOST:-}" ] || err "PGHOST not set in .env"
[ -n "${PGUSER:-}" ] || err "PGUSER not set in .env"
[ -n "${ENDPOINT_NAME:-}" ] || err "ENDPOINT_NAME not set in .env"
SCOPE="${SECRET_SCOPE:-gurukul}"
S2_SECRET_KEY="${S2_SECRET_KEY:-s2_api_key}"
CORPUS_QUERIES="${QUERIES_OVERRIDE:-${S2_CORPUS_QUERIES:-attention is all you need transformer;large language model architecture;instruction tuning large language models;reinforcement learning from human feedback;direct preference optimization language models;constitutional AI harmlessness;retrieval augmented generation;LLM agents reasoning acting;Toolformer language models tools;ReAct reasoning acting language models;mixture of experts large language models;Mixtral mixture of experts;low rank adaptation LoRA large language models;FlashAttention efficient attention;KV cache optimization large language models;speculative decoding language models;quantization large language models;long context language models;chain of thought reasoning language models;LLM evaluation benchmarks;LLM safety alignment jailbreak;multimodal large language models;Llama technical report;Qwen technical report;DeepSeek large language model technical report}}"
LIMIT_PER_QUERY="${LIMIT_OVERRIDE:-${S2_LIMIT_PER_QUERY:-40}}"

SECRETS_JSON=$(databricks secrets list-secrets "$SCOPE" $PROFILE_ARG -o json 2>/dev/null || echo "{}")
if ! python3 - "$S2_SECRET_KEY" "$SECRETS_JSON" <<'PY'
import json, sys
key, raw = sys.argv[1:3]
data = json.loads(raw)
items = data if isinstance(data, list) else data.get("secrets", [])
raise SystemExit(0 if any((s.get("key") or s.get("name")) == key for s in items) else 1)
PY
then
    err "Databricks secret $SCOPE/$S2_SECRET_KEY not found. Run ./scripts/setup_secrets.sh after setting S2_API_KEY."
fi
ok "Found Databricks secret $SCOPE/$S2_SECRET_KEY"

RUN_ID="corpus-$(date +%Y%m%d%H%M%S)"
JOB_NAME="gurukul-corpus-build"
WS_PATH="$(gurukul_workspace_jobs_path "gurukul")"
STAGING=$(mktemp -d)
JOB_SETTINGS=$(mktemp)
trap "rm -rf $STAGING $JOB_SETTINGS" EXIT

step "2/4  Staging job source"
rsync -a --exclude='__pycache__' agent_server "$STAGING/"
rsync -a --exclude='__pycache__' jobs "$STAGING/"
mkdir -p "$STAGING/scripts"
rsync -a --include='*/' --include='*.sql' --exclude='*' scripts/ "$STAGING/scripts/"
cp pyproject.toml "$STAGING/"
databricks workspace mkdirs "$WS_PATH" $PROFILE_ARG >/dev/null 2>&1 || true
databricks workspace import-dir "$STAGING" "$WS_PATH" --overwrite $PROFILE_ARG >/dev/null
ok "Uploaded corpus job source to $WS_PATH"

if $DRY_RUN; then
    echo ""
    log "Dry run complete. To submit: ./scripts/build_corpus.sh"
    exit 0
fi

step "3/4  Creating/updating saved serverless job"
cat > "$JOB_SETTINGS" <<JSON
{
  "name": "${JOB_NAME}",
  "max_concurrent_runs": 1,
  "tasks": [
    {
      "task_key": "build_corpus",
      "spark_python_task": {
        "python_file": "${WS_PATH}/jobs/corpus_build.py",
        "parameters": [
          "--run-id", "${RUN_ID}",
          "--pg-host", "${PGHOST}",
          "--pg-user", "${PGUSER}",
          "--pg-database", "${PGDATABASE:-databricks_postgres}",
          "--lakebase-endpoint", "${ENDPOINT_NAME}",
          "--db-schema", "${GURUKUL_DB_SCHEMA:-gurukul}",
          "--source-root", "${WS_PATH}",
          "--s2-release", "${S2_RELEASE:-latest}",
          "--s2-secret-scope", "${SCOPE}",
          "--s2-secret-key", "${S2_SECRET_KEY}",
          "--embedding-model", "${EMBEDDING_MODEL:-gurukul-specter2-embed}",
          "--probe-query", "${S2_PROBE_QUERY:-reinforcement learning from human feedback}",
          "--queries", "${CORPUS_QUERIES}",
          "--limit-per-query", "${LIMIT_PER_QUERY}",
          "--embedding-batch-size", "${EMBEDDING_BATCH_SIZE:-16}"
        ]
      },
      "environment_key": "corpus_env",
      "timeout_seconds": 7200,
      "max_retries": 0,
      "min_retry_interval_millis": 120000,
      "retry_on_timeout": true
    }
  ],
  "environments": [
    {
      "environment_key": "corpus_env",
      "spec": {
        "client": "2",
        "dependencies": [
          "databricks-sdk>=0.79.0",
          "psycopg[binary]>=3.2.0",
          "httpx>=0.27.0",
          "pandas>=2.2.0",
          "pyarrow>=16.0.0"
        ]
      }
    }
  ]
}
JSON

JOB_ID=$(gurukul_create_or_reset_job "$JOB_NAME" "$JOB_SETTINGS" | tail -1)
[ -n "$JOB_ID" ] || err "Saved job id was empty"

DB_RUN_ID=$(databricks jobs run-now "$JOB_ID" --idempotency-token "$RUN_ID" --no-wait --performance-target PERFORMANCE_OPTIMIZED $PROFILE_ARG -o json \
    | python3 -c "import sys,json; print(json.load(sys.stdin).get('run_id',''))")
[ -n "$DB_RUN_ID" ] || err "Job submission did not return a run_id"
ok "Started saved Databricks job $JOB_ID run: $DB_RUN_ID (progress id: $RUN_ID)"

step "4/4  Monitoring"
if $WAIT; then
    gurukul_monitor_run "$DB_RUN_ID" 30 \
        && ok "Corpus build job succeeded" \
        || err "Corpus build job failed (run id: $DB_RUN_ID)"
else
    ok "Submitted without waiting"
fi

echo ""
echo "  Structured progress row:"
echo "    SELECT * FROM gurukul.long_running_jobs WHERE run_id = '$RUN_ID';"
echo ""
echo "  Databricks run:"
echo "    databricks jobs get-run $DB_RUN_ID $PROFILE_ARG"
echo ""

