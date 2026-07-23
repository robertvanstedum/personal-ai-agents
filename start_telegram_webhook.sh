#!/bin/bash
# Compatibility launcher — the real script moved to
# scripts/start_telegram_webhook.sh as part of Phase 2 Slice 2 (2026-07-23).
# Kept here because infrastructure/launchd/com.vanstedum.telegram-webhook.plist
# references this exact path in its ProgramArguments, and that plist is
# installed on the Mac outside this repo's control — it isn't updated by
# this PR. Remove once the plist is confirmed pointing at the new path
# directly (reinstall/reload after updating ProgramArguments).
exec "$(dirname "$0")/scripts/start_telegram_webhook.sh" "$@"
