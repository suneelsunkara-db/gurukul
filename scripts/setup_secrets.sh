#!/usr/bin/env bash
set -euo pipefail

# ─── Gurukul Databricks Secret Setup ─────────────────────────────────
# Stores local .env secrets in the Databricks secret scope used by the app.
# Secret values are never printed.
#
# Usage:
#   ./scripts/setup_secrets.sh
#   S2_API_KEY=... ./scripts/setup_secrets.sh
# ─────────────────────────────────────────────────────────────────────

LOG_TAG="secrets"
source "$(dirname "$0")/_common.sh"
cd "$GURUKUL_ROOT"

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════════╗"
echo "  ║   Gurukul — Databricks Secret Setup              ║"
echo "  ╚══════════════════════════════════════════════════╝"
echo -e "${NC}"

step "1/3  Loading config"
gurukul_load_env
gurukul_require_auth

SCOPE="${SECRET_SCOPE:-gurukul}"

step "2/3  Ensuring secret scope"
if databricks secrets create-scope "$SCOPE" $PROFILE_ARG >/dev/null 2>&1; then
    ok "Created secret scope '$SCOPE'"
else
    ok "Secret scope '$SCOPE' already exists"
fi

put_secret() {
    local env_name="$1"
    local key_name="$2"
    local required="${3:-false}"
    local value="${!env_name:-}"

    if [ -z "$value" ]; then
        if [ "$required" = "true" ]; then
            err "$env_name is required but not set"
        fi
        warn "$env_name not set — skipping $SCOPE/$key_name"
        return 0
    fi

    databricks secrets put-secret "$SCOPE" "$key_name" \
        --string-value "$value" $PROFILE_ARG >/dev/null
    ok "Stored $SCOPE/$key_name"
}

step "3/3  Storing secrets"
put_secret "TAVILY_API_KEY" "tavily_api_key" "false"
put_secret "S2_API_KEY" "s2_api_key" "true"

ok "Secret setup complete"

