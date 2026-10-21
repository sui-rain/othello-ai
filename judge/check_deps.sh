#!/usr/bin/env bash
# Fail if Cargo.lock at <head> lists a crate that is not allow-listed.
# Usage: check_deps.sh <head-sha> <allowed-external-file> <allowed-local-file>
#
# Parses Cargo.lock as text instead of calling cargo, so no toolchain or config
# chosen by the PR is ever run. The test job's `--locked` keeps the lock honest.
set -euo pipefail

head="$1"
allowed_external="$2"
allowed_local="$3"

load_list() {
  local line
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    line="${line//[[:space:]]/}"
    [[ -z "$line" || "$line" == \#* ]] && continue
    printf '%s\n' "$line"
  done < "$1"
}

lock="$(git show "$head:Cargo.lock" 2>/dev/null)" || { echo "Cargo.lock must be committed"; exit 1; }

# Emit "name<TAB>external|local" for each [[package]] block.
packages="$(printf '%s\n' "$lock" | awk '
  /^\[\[package\]\]/ { if (name != "") print name "\t" kind; name = ""; kind = "local"; next }
  /^name = "/ { name = $3; gsub(/"/, "", name); next }
  /^source = "/ { kind = "external"; next }
  END { if (name != "") print name "\t" kind }
')"

ext_ok="$(load_list "$allowed_external" | sort -u)"
loc_ok="$(load_list "$allowed_local" | sort -u)"
bad=()
while IFS=$'\t' read -r name kind; do
  [[ -z "$name" ]] && continue
  if [[ "$kind" == "external" ]]; then
    grep -qxF -- "$name" <<<"$ext_ok" || bad+=("$name (external crate not allow-listed)")
  else
    grep -qxF -- "$name" <<<"$loc_ok" || bad+=("$name (local/path crate not allow-listed)")
  fi
done <<<"$packages"

if (( ${#bad[@]} > 0 )); then
  printf '%s\n' "${bad[@]}"
  exit 1
fi
echo "dependencies OK"
