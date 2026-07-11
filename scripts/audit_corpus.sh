#!/usr/bin/env bash
set -euo pipefail

# ─── Gurukul corpus quality audit ────────────────────────────────────
# Pre-deploy data gate for corpus cleanliness and golden retrieval coverage.
#
# Usage:
#   ./scripts/audit_corpus.sh
#   ./scripts/audit_corpus.sh --min-rows 200
# ─────────────────────────────────────────────────────────────────────

LOG_TAG="corpus-audit"
source "$(dirname "$0")/_common.sh"
cd "$GURUKUL_ROOT"

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║   Gurukul — Corpus quality audit                 ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

step "1/2  Loading config"
gurukul_load_env
[ -n "${EMBEDDING_MODEL:-}" ] || err "EMBEDDING_MODEL is required"
ok "Embedding endpoint: ${EMBEDDING_MODEL}"

step "2/2  Running audit"
uv run python3 -m evals.corpus_audit "$@"
ok "Corpus audit passed"

