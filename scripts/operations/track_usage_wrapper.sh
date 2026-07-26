#!/bin/bash
# Compatibility launcher — the real script moved to scripts/track_usage_wrapper.sh
# as part of Phase 2 Slice 1 (2026-07-23). No external crontab entry referenced
# the old root path when rechecked on 2026-07-26.
# Retained under scripts/operations for traceability; it is no longer at root.
exec "$(dirname "$0")/../track_usage_wrapper.sh" "$@"
