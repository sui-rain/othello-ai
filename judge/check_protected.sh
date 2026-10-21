#!/usr/bin/env bash
# Fail if a branch named auto/* changes a protected path.
# Usage: check_protected.sh <base-ref> <head-branch-name> <protected-paths-file>
# CI runs the copy of this script and of the paths file taken from main, not from the PR.
set -euo pipefail

base="$1"
branch="$2"
patterns="$3"

case "$branch" in
  auto/*) ;;
  *) echo "branch '$branch' is not an auto/ branch; protected-path check skipped"; exit 0 ;;
esac

changed="$(git diff --name-only "$base"...HEAD)"
violations=""
while IFS= read -r pat; do
  [[ -z "$pat" || "$pat" == \#* ]] && continue
  hits="$(printf '%s\n' "$changed" | grep -E -- "$pat" || true)"
  [[ -n "$hits" ]] && violations+="$hits"$'\n'
done < "$patterns"

if [[ -n "$violations" ]]; then
  echo "auto/ branches must not change these protected files:"
  printf '%s' "$violations" | sort -u
  exit 1
fi
echo "no protected paths changed"
