#!/bin/sh
# Container entrypoint: validate required secrets are present, then exec the
# MCP server. Secrets are injected via the compose env_file (.env), which the
# user populates however they like — file, secret-manager sidecar, mounted
# secret, etc. This script does not pull from any specific secret manager.
#
# Required env:
#   ANKI_MCP_TOKEN   — bearer token MCP clients must send on every request
#
# Optional env (defaults shown):
#   ANKI_MCP_HOST=0.0.0.0
#   ANKI_MCP_PORT=8765
#
# The server is exec'd directly. No token ever lives on disk inside the
# container; it is only in the process environment for the lifetime of the
# container.

set -eu

: "${ANKI_MCP_TOKEN:?ANKI_MCP_TOKEN must be set}"

exec "$@"