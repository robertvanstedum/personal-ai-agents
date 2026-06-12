#!/bin/bash
# Signal that Claude Code is starting work on a spec.
# Flips status → in_build in guild.design_log and writes a transition row.
#
# Usage: scripts/start_build.sh <spec-filename>
# Example: scripts/start_build.sh build_spec_build_discipline_2026-06-12.md
#
# Run once at the start of each build session referencing a specific spec.
# Safe to re-run — just updates last_transition_at if already in_build.

set -euo pipefail

if [[ $# -lt 1 ]]; then
    echo "Usage: $0 <spec-filename>"
    echo "Example: $0 build_spec_build_discipline_2026-06-12.md"
    exit 1
fi

SPEC_FILE="$1"
RESPONSE=$(curl -s -w "\n%{http_code}" -X POST localhost:8770/start-build \
    -H "Content-Type: application/json" \
    -d "{\"spec_file\": \"${SPEC_FILE}\"}")

HTTP_CODE=$(echo "$RESPONSE" | tail -n1)
BODY=$(echo "$RESPONSE" | head -n-1)

if [[ "$HTTP_CODE" == "200" ]]; then
    echo "✅ Build started: ${SPEC_FILE}"
    echo "${BODY}" | python3 -c "import sys,json; d=json.load(sys.stdin); print(f'   {d.get(\"previous_status\",\"?\")} → in_build')" 2>/dev/null || true
else
    echo "⚠️  start-build returned HTTP ${HTTP_CODE}: ${BODY}"
    echo "   (Design/Dev agent may be down — build can proceed, status won't auto-flip)"
    exit 0   # non-fatal: don't block the build
fi
