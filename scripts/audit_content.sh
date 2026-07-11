#!/usr/bin/env bash
set -euo pipefail

# ─── Gurukul generated-content quality audit ─────────────────────────
# Deterministic pre-deploy gate for generated topic payloads.
#
# Usage:
#   ./scripts/audit_content.sh
#   ./scripts/audit_content.sh --base-url http://localhost:8000 --min-refs 1
# ─────────────────────────────────────────────────────────────────────

LOG_TAG="content-audit"
source "$(dirname "$0")/_common.sh"
cd "$GURUKUL_ROOT"

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║   Gurukul — Content quality audit                ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

step "1/2  Loading config"
gurukul_load_env
ok "Config loaded"

step "2/2  Running content audit"
uv run python3 -m evals.content_audit "$@"
ok "Content audit passed"
