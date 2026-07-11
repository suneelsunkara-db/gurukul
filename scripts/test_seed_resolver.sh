#!/usr/bin/env bash
set -euo pipefail

# ─── Gurukul seed-resolution regression harness ──────────────────────
# Runs the P1 resolver against a small labeled seed set. This is a live test:
# it queries scholarly sources (arXiv and S2 when configured) and SPECTER2.
#
# Usage:
#   ./scripts/test_seed_resolver.sh
# ─────────────────────────────────────────────────────────────────────

LOG_TAG="seed-resolver"
source "$(dirname "$0")/_common.sh"
cd "$GURUKUL_ROOT"

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║   Gurukul — Seed Resolver Regression             ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

step "1/2  Loading config"
gurukul_load_env
ok "Embedding endpoint: ${EMBEDDING_MODEL:-gurukul-specter2-embed}"
if [ -z "${S2_API_KEY:-}" ]; then
    warn "S2_API_KEY not set — Semantic Scholar search will be skipped"
fi

step "2/2  Running cases"
uv run python3 -m evals.seed_resolution_eval
ok "Seed-resolution checks passed"

