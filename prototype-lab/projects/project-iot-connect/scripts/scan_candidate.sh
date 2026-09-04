#!/bin/sh
# Deterministic no-secret / no-laptop-path / no-stale-name / no-out-of-scope scan.
# Exit 0 when clean, 1 when any category has a hit. Portable (POSIX sh + grep).
set -u
cd "$(dirname "$0")/.." || exit 2

status=0
report() {  # $1 label, $2 file holding the hits
  count=$(grep -c '' "$2" 2>/dev/null); count=${count:-0}
  if [ "$count" -gt 0 ]; then
    echo "FAIL  $1 ($count hit(s))"; sed 's/^/      /' "$2"; status=1
  else
    echo "PASS  $1"
  fi
  rm -f "$2"
}
tmp() { mktemp "${TMPDIR:-/tmp}/iotconnect-scan.XXXXXX"; }

# Files that legitimately embed the scanned patterns (the scanner itself and the
# packaging test that asserts their absence) are excluded everywhere.
SELF='scripts/scan_candidate.sh|tests/test_standalone_packaging.py'
EXCLUDE='--exclude-dir=.git --exclude-dir=.venv --exclude-dir=__pycache__ --exclude-dir=.pytest_cache --exclude-dir=.verify --exclude=*.png --exclude=*.pyc'
# Program-control documents staged in the tree discuss future scope on purpose.
PROGRAM_DOCS='--exclude=BRIEF.md --exclude=DECISIONS.md --exclude=START_HERE.md --exclude=project.yaml --exclude-dir=build --exclude-dir=design --exclude-dir=handoffs --exclude-dir=docs'
# Same set, but keeping docs/ in scope (the delivered design references were
# renamed in revision 5; only the Codex-staged program-control documents, which
# are byte-identical to program/ and record the naming decision itself, are out).
PROGRAM_DOCS_ONLY='--exclude=BRIEF.md --exclude=DECISIONS.md --exclude=START_HERE.md --exclude=project.yaml --exclude-dir=build --exclude-dir=design --exclude-dir=handoffs'

f=$(tmp); grep -rnE $EXCLUDE '(/Users/|/home/[a-z]|~/|C:\\)' . | grep -vE "$SELF" > "$f"
report "absolute user / laptop paths" "$f"

# The active package uses only the IoT Connect namespace. Earlier naming is
# permitted solely in immutable program/design history, the two preserved
# source documents under docs/history/, the README provenance sentence, the
# `predecessor` provenance field in project.yaml, the hashed uuid5 namespace
# constant in app/domain/identity.py (kept so prepared-record UUIDs stay stable
# across the rename), and this scanner's negative-test fixture. Both file contents and active path names
# are checked so a binary or empty file cannot retain an obsolete identifier in
# its name unnoticed.
HISTORICAL='--exclude=BRIEF.md --exclude=DECISIONS.md --exclude=START_HERE.md --exclude-dir=build --exclude-dir=design --exclude-dir=handoffs --exclude-dir=history'
f=$(tmp)
grep -rniE $EXCLUDE $HISTORICAL '(connect[ ._-]?hq|nightjar|wham)' . \
  | grep -vE "$SELF" \
  | grep -vE 'README.md:.*Historical provenance|README.md:.*\*Project Nightjar\*|README.md:.*docs/history/connect-hq/INDEX.md|^\./project\.yaml:[0-9]+:  predecessor: project-nightjar$|^\./app/domain/identity\.py:[0-9]+:.*f"prototype-lab/wham-v3/\{object_type\}/\{display_number\}"' > "$f"
find . -depth -print \
  | grep -vE '^\./(build|design|handoffs)(/|$)|^\./docs/history(/|$)' \
  | grep -iE '(connect[._-]?hq|nightjar|wham)' >> "$f"
report "retired identifiers in active source or paths" "$f"

# Plain-name denylist for the runtime/test tree. The names themselves are never
# stored in this tree: the default list is derived at scan time from the account
# running the scan (login name + full-name tokens of 4+ letters, macOS `id -F` /
# GECOS on Linux). Add more with IOTCONNECT_NAME_DENYLIST="name1|name2" or a local,
# git-ignored scripts/scan_denylist.local (one pattern per line). Program-control
# and design-history documents (BRIEF/DECISIONS/START_HERE/project.yaml, build/,
# design/, handoffs/, and docs/history/) identify the
# decision owner on purpose and are excluded from this category only.
owner_tokens=$( { id -un 2>/dev/null; id -F 2>/dev/null || getent passwd "$(id -un)" 2>/dev/null | cut -d: -f5 | cut -d, -f1; } | tr ' ' '\n' | awk 'length($0) >= 4' | tr '[:upper:]' '[:lower:]' | sort -u | paste -sd '|' -)
name_denylist="$owner_tokens"
[ -n "${IOTCONNECT_NAME_DENYLIST:-}" ] && name_denylist="${name_denylist:+$name_denylist|}$IOTCONNECT_NAME_DENYLIST"
[ -f scripts/scan_denylist.local ] && name_denylist="${name_denylist:+$name_denylist|}$(grep -v '^#' scripts/scan_denylist.local | grep -v '^$' | paste -sd '|' -)"
RUNTIME_TREE='app contracts integration_stubs postgres postman scripts tests fixtures README.md HANDS_ON_RUNBOOK.md Makefile docker-compose.yml Dockerfile .dockerignore .env.example .gitignore docs/ARCHITECTURE.md'
f=$(tmp)
if [ -n "$name_denylist" ]; then
  grep -rniE $EXCLUDE "\b($name_denylist)\b" $RUNTIME_TREE 2>/dev/null | grep -vE "$SELF" > "$f"
  report "plain personal names in the runtime/test tree (denylist: $(echo "$name_denylist" | tr '|' ',' | sed 's/[a-z]/*/g' | cut -c1-40)… masked, $(echo "$name_denylist" | tr '|' '\n' | wc -l | tr -d ' ') pattern(s))" "$f"
else
  echo "SKIP  plain personal names: no denylist available (set IOTCONNECT_NAME_DENYLIST)"; rm -f "$f"
fi

f=$(tmp); grep -rnE $EXCLUDE '(github\.com/[A-Za-z0-9_-]+|[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[a-z]{2,})' . | grep -vE "$SELF" | grep -vE 'schema\.getpostman\.com|www\.w3\.org|schemas\.xmlsoap\.org' > "$f"
report "personal identities / repository handles / e-mail addresses" "$f"

f=$(tmp); grep -rnE $EXCLUDE '(AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY|xox[baprs]-[A-Za-z0-9-]+|ghp_[A-Za-z0-9]{20,}|SNOWFLAKE_PAT=[^$'"'"'"]+[A-Za-z0-9])' . | grep -vE "$SELF" > "$f"
report "secret patterns" "$f"

f=$(tmp); grep -rnoE $EXCLUDE '\b[0-9]{13,19}\b' . | grep -vE "$SELF" | grep -vE '8901410321111851|3101501234|^./fixtures/.*:8901[0-9]{16}$' > "$f"
report "PAN-like numbers (13-19 digits, excluding synthetic ICCID/IMSI)" "$f"

f=$(tmp); grep -rniE $EXCLUDE $PROGRAM_DOCS '(salesforce|zuora|adyen|stripe|fiber|fibre|\bont\b)' . | grep -vE "$SELF" > "$f"
report "out-of-scope vendor / fiber scope in runtime code and delivered docs" "$f"

f=$(tmp); grep -rnE $EXCLUDE 'claude-draft|output/pdf|output/pptx|output/screenshots|mockups/' app integration_stubs scripts tests README.md HANDS_ON_RUNBOOK.md Makefile docker-compose.yml 2>/dev/null | grep -vE "$SELF" > "$f"
report "dated draft / generated output dependencies in runtime code" "$f"

# Binary presentation assets cannot be text-scanned for retired names, so each
# rendered slide is pinned to the checksum recorded at its visual review
# (docs/PRESENTATION_VISUAL_REVIEW_2026-09-04.md). A changed or missing slide
# fails here until it is re-rendered, re-inspected and the manifest updated.
f=$(tmp)
SLIDES=app/static/iotconnect/presentation
if [ -f "$SLIDES/CHECKSUMS.sha256" ]; then
  if command -v sha256sum >/dev/null 2>&1; then
    (cd "$SLIDES" && sha256sum -c --quiet CHECKSUMS.sha256) > "$f" 2>&1 || echo "checksum verification failed for $SLIDES" >> "$f"
  else
    (cd "$SLIDES" && shasum -a 256 -c CHECKSUMS.sha256 | grep -v ': OK$') > "$f" 2>&1 || true
  fi
  [ "$(grep -c '' "$SLIDES/CHECKSUMS.sha256")" -eq 5 ] || echo "$SLIDES/CHECKSUMS.sha256 must list exactly five slides" >> "$f"
else
  echo "$SLIDES/CHECKSUMS.sha256 is missing" > "$f"
fi
report "presentation slide checksums match the visual-review manifest" "$f"

f=$(tmp); [ -f .env ] && echo "./.env exists (ignored by .gitignore; must never be committed)" > "$f"
report "committed .env" "$f"

exit $status
