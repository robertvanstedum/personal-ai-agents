#!/bin/sh
# Master Craftsman's model-gateway key must exist and must never be CoS's key
# (or any other key named here). Compares values; never prints one.
#
#   MC_MODEL_GATEWAY_KEY=... MINIMOI_MODEL_GATEWAY_KEY=... mc-key-check.sh MINIMOI_MODEL_GATEWAY_KEY [NAME ...]
#
# Exit 0: MC's key is set and differs from every named key.
# Exit 1: MC_MODEL_GATEWAY_KEY is empty or unset.
# Exit 2: MC_MODEL_GATEWAY_KEY equals the key in one of the named variables.
# Exit 64: usage (no name given, or a name that is not a shell variable name).
# Dormant in the separate-container plan's PR 1: nothing runs it yet. The
# staging MC service (PR 2) runs it before MC starts.
set -eu

[ "$#" -gt 0 ] || { echo "mc-key-check: name at least one key variable to compare with (e.g. MINIMOI_MODEL_GATEWAY_KEY)" >&2; exit 64; }
mc="${MC_MODEL_GATEWAY_KEY:-}"
if [ -z "$mc" ]; then
  echo "mc-key-check: MC_MODEL_GATEWAY_KEY is empty; Master Craftsman stays off" >&2
  exit 1
fi
for name in "$@"; do
  case "$name" in
    ''|[0-9]*|*[!A-Za-z0-9_]*) echo "mc-key-check: '$name' is not a variable name" >&2; exit 64 ;;
  esac
  eval "other=\${$name:-}"
  if [ -n "$other" ] && [ "$other" = "$mc" ]; then
    echo "mc-key-check: MC_MODEL_GATEWAY_KEY equals $name; Master Craftsman never runs on another agent's key" >&2
    exit 2
  fi
done
echo "mc-key-check: MC's key is set and differs from: $*"
