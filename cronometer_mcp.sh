#!/bin/bash
# Cronometer MCP launcher — reads creds from ~/.hermes/.env.cronometer
set -a
source /Users/leonl/.hermes/.env.cronometer 2>/dev/null
set +a
exec /Users/leonl/.hermes/hermes-agent/venv/bin/python \
  /Users/leonl/.hermes/skills/self-hosted/cronometer_mcp_server.py "$@"
