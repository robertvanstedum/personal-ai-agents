#!/bin/bash
# Compatibility launcher — the real script moved to scripts/track_usage_wrapper.sh
# as part of Phase 2 Slice 1 (2026-07-23). Kept here because this path is
# referenced by an external crontab entry (cron 08:00 + 10:00, see
# OPERATIONS.md) that lives outside this repo and isn't updated by this PR.
# Remove once the crontab entry is confirmed pointing at the new path directly.
exec "$(dirname "$0")/scripts/track_usage_wrapper.sh" "$@"
