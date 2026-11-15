#!/bin/sh
# Nightly leak scan (#612). Not installed. A host without the private tree is left alone.
# Install only after the allowlist exists (spec step 4). This script exits without scanning until then.
set -eu
ROOT="${MINIMOI_PRIVATE_ROOT:-}"
PUB="${MINIMOI_PUBLIC_REPO:-}"
if [ -z "$ROOT" ] || [ ! -d "$ROOT" ]; then
  echo "leak-scan: private tree is not on this machine; not copying it" >&2
  exit 2
fi
if [ -z "$PUB" ] || [ ! -d "$PUB" ]; then
  echo "leak-scan: public repo is not set" >&2
  exit 2
fi
if [ ! -f "$ROOT/leak-scan/allowlist.jsonl" ]; then
  echo "leak-scan: no allowlist yet; nightly watch stays off" >&2
  exit 0
fi
cd "$(dirname "$0")/../.."
exec python3 -m core.leak_scan --public "$PUB" --private "$ROOT" --watch
