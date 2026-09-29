#!/bin/bash
# release_base.sh BEFORE HEAD [BASE_REF] — the commit CI diffs against to
# classify a release (deploy.yml "Classify release contents").
#
# BEFORE is github.event.before. After a rebase and force-push it names a
# commit that no longer exists in the clone ("fatal: bad object"), and on a
# new branch or a PR's first run it is empty or all zeros. Then the base is:
#   1. the merge-base with origin/<BASE_REF> (a pull request's base branch),
#   2. else the merge-base with origin/main,
#   3. else HEAD^ (a push to main, where HEAD is already on main).
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
  echo "$before"
  exit 0
fi
if [[ -n "$before" && "$before" != "$zero" ]]; then
  if [[ -z "$base_ref" ]]; then
    echo "release_base: before=$before is gone on a push to main (history rewritten); full release" >&2
    exit 3
  fi
  echo "release_base: before=$before is not in this clone (a rebase or force-push); using a fallback" >&2
fi

head_sha=$(git rev-parse "${head}^{commit}")
refs=()
[[ -n "$base_ref" ]] && refs+=("origin/$base_ref")
refs+=("origin/main")
for ref in "${refs[@]}"; do
  if git rev-parse -q --verify "${ref}^{commit}" >/dev/null; then
    mb=$(git merge-base "$ref" "$head_sha" 2>/dev/null || true)
    if [[ -n "$mb" && "$mb" != "$head_sha" ]]; then
      echo "release_base: merge-base with $ref" >&2
      echo "$mb"
      exit 0
    fi
  fi
done
if git rev-parse -q --verify "${head_sha}^" >/dev/null; then
  echo "release_base: HEAD^" >&2
  git rev-parse "${head_sha}^"
  exit 0
fi
echo "release_base: no base commit found" >&2
exit 1
