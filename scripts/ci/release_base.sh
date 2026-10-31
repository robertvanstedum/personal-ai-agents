#!/bin/bash
# release_base.sh BEFORE HEAD [BASE_REF] — the commit CI diffs against to
# classify a release (deploy.yml "Classify release contents").
#
# Pull requests always use the merge-base with origin/<BASE_REF>. Their
# synchronize event's BEFORE names the previous branch head, which would
# hide earlier unmerged changes. A missing PR target requires full coverage.
# For pushes, BEFORE is github.event.before. If empty or all zeros, use the
# merge-base with origin/main, else HEAD^ when HEAD is already on main.
# Exception: on a push to main (no BASE_REF) a non-empty BEFORE that is gone
# means main's history was rewritten, so HEAD^ could hide changed services.
# Then it exits 3 and deploy.yml classifies a full release.
# Prints the chosen commit; says on stderr which fallback it took.
set -euo pipefail
before="${1:-}"
head="${2:-HEAD}"
base_ref="${3:-}"
zero=0000000000000000000000000000000000000000

if [[ -n "$before" && "$before" != "$zero" ]] && git cat-file -e "${before}^{commit}" 2>/dev/null; then
  if [[ -z "$base_ref" ]]; then
    echo "$before"
    exit 0
  fi
  echo "release_base: PR update ignores previous head; comparing cumulative changes" >&2
elif [[ -n "$before" && "$before" != "$zero" ]]; then
  if [[ -z "$base_ref" ]]; then
    echo "release_base: before=$before is gone on a push to main (history rewritten); full release" >&2
    exit 3
  fi
  echo "release_base: before=$before is not in this clone (a rebase or force-push); using a fallback" >&2
fi

head_sha=$(git rev-parse "${head}^{commit}")
refs=()
if [[ -n "$base_ref" ]]; then
  refs+=("origin/$base_ref")
else
  refs+=("origin/main")
fi
for ref in "${refs[@]}"; do
  if git rev-parse -q --verify "${ref}^{commit}" >/dev/null; then
    mb=$(git merge-base "$ref" "$head_sha" 2>/dev/null || true)
    if [[ -n "$mb" && ( -n "$base_ref" || "$mb" != "$head_sha" ) ]]; then
      echo "release_base: merge-base with $ref" >&2
      echo "$mb"
      exit 0
    fi
  fi
done
if [[ -n "$base_ref" ]]; then
  echo "release_base: PR target merge-base unavailable; full release" >&2
  exit 3
fi
if git rev-parse -q --verify "${head_sha}^" >/dev/null; then
  echo "release_base: HEAD^" >&2
  git rev-parse "${head_sha}^"
  exit 0
fi
echo "release_base: no base commit found" >&2
exit 1
