#!/usr/bin/env bash
# Fail if the commits between <base> and <head> change a protected path.
# Usage: check_protected.sh <base-sha> <head-sha> <protected-paths-file>
#
# Runs from the trusted base checkout (guard.yml, pull_request_target). It only
# reads git data of the PR head; it never builds or executes PR code.
set -euo pipefail

base="$1"
head="$2"
patterns_file="$3"

# Load patterns robustly: keep a final line without newline, strip CR and
# trailing whitespace, skip blanks and comments, reject invalid regexes.
patterns=()
while IFS= read -r line || [[ -n "$line" ]]; do
  line="${line%$'\r'}"
  line="${line%"${line##*[![:space:]]}"}"
  [[ -z "$line" || "$line" == \#* ]] && continue
  rc=0
  printf '' | grep -E -- "$line" >/dev/null 2>&1 || rc=$?
  if (( rc == 2 )); then
    echo "invalid pattern in $patterns_file: $line"
    exit 1
  fi
  patterns+=("$line")
done < "$patterns_file"
(( ${#patterns[@]} > 0 )) || { echo "no protected patterns loaded"; exit 1; }

# Every changed path, unquoted, with renames split into delete + add.
mapfile -d '' changed < <(git -c core.quotePath=false diff --no-renames --name-only -z "$base...$head")

violations=()
for f in "${changed[@]}"; do
  [[ -z "$f" ]] && continue
  if [[ ! "$f" =~ ^[A-Za-z0-9._/-]+$ ]]; then
    violations+=("$f (unusual characters in path)")
    continue
  fi
  for pat in "${patterns[@]}"; do
    rc=0
    printf '%s\n' "$f" | grep -Eq -- "$pat" || rc=$?
    if (( rc == 0 )); then
      violations+=("$f")
      break
    elif (( rc == 2 )); then
      echo "grep failed on pattern: $pat"
      exit 1
    fi
  done
done

if (( ${#violations[@]} > 0 )); then
  echo "this pull request changes protected files (the owner must make these changes):"
  printf '  %s\n' "${violations[@]}"
  exit 1
fi
echo "no protected paths changed (${#changed[@]} files checked)"
