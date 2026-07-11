#!/usr/bin/env bash
set -euo pipefail

# ─── Gurukul grounding smoke test ───────────────────────────────────
# Verifies S2, Lakebase corpus, hybrid retrieval, and resolver evidence labels.
#
# Usage:
#   ./scripts/test_grounding.sh
#   ./scripts/test_grounding.sh "Qwen AgentWorld"
# ─────────────────────────────────────────────────────────────────────

LOG_TAG="grounding"
source "$(dirname "$0")/_common.sh"
cd "$GURUKUL_ROOT"

QUERY="${1:-reinforcement learning from human feedback}"

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║   Gurukul — Grounding smoke test                 ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

step "1/2  Loading config"
gurukul_load_env
[ -n "${S2_API_KEY:-}" ] || err "S2_API_KEY is required for grounding smoke"
[ -n "${EMBEDDING_MODEL:-}" ] || err "EMBEDDING_MODEL is required"
ok "Embedding endpoint: ${EMBEDDING_MODEL}"

step "2/2  Running grounding smoke"
uv run python3 -m evals.grounding_smoke --query "$QUERY"
ok "Grounding smoke passed"

