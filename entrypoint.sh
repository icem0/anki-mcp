#!/bin/sh
# Container entrypoint: pull secrets from Infisical, then exec the MCP server.
#
# Required env:
#   INFISICAL_TOKEN      — service-account token with read access to the
#                           project/environment/path that holds ANKI_MCP_TOKEN
#   INFISICAL_PROJECT_ID — Infisical workspace UUID
#   INFISICAL_ENV        — environment slug (e.g. "prod")
#
# Optional env (default shown):
#   INFISICAL_SECRET_PATH=/anki-mcp   — folder under which ANKI_MCP_TOKEN lives
#   INFISICAL_SITE_URL=https://infisical.sa-ma.online
#
# The ANKI_MCP_TOKEN is exported into the process env, then the CMD
# (the MCP server) is exec'd. The token is never written to disk.

set -eu

: "${INFISICAL_TOKEN:?INFISICAL_TOKEN must be set}"
: "${INFISICAL_PROJECT_ID:?INFISICAL_PROJECT_ID must be set}"
: "${INFISICAL_ENV:?INFISICAL_ENV must be set}"
: "${INFISICAL_SECRET_PATH:=/anki-mcp}"
: "${INFISICAL_SITE_URL:=https://app.infisical.com}"

INFISICAL_DOMAIN="$INFISICAL_SITE_URL"
export INFISICAL_DOMAIN

# Fetch ANKI_MCP_TOKEN as shell env, then exec the server. Pass `--silent`
# to suppress the CLI's tips; secrets are exported via the standard
# `infisical run` mechanism (a wrapper that exec's the command with the
# fetched secrets pre-populated in the process environment).
exec infisical run \
    --token="$INFISICAL_TOKEN" \
    --projectId="$INFISICAL_PROJECT_ID" \
    --env="$INFISICAL_ENV" \
    --path="$INFISICAL_SECRET_PATH" \
    --silent \
    -- \
    "$@"
