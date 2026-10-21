#!/usr/bin/env bash
# Fail if the dependency tree contains an external crate that is not allow-listed,
# or if Cargo.lock is missing or out of date.
# Usage: check_deps.sh <allowed-deps-file>
set -euo pipefail

allowed="$1"
test -f Cargo.lock || { echo "Cargo.lock must be committed"; exit 1; }

external="$(cargo metadata --format-version 1 --locked \
  | jq -r '.packages[] | select(.source != null) | .name' | sort -u)"
permitted="$(grep -Ev '^\s*(#|$)' "$allowed" | sort -u || true)"
extra="$(comm -23 <(printf '%s\n' "$external" | sed '/^$/d') <(printf '%s\n' "$permitted" | sed '/^$/d'))"

if [[ -n "$extra" ]]; then
  echo "crates not in the allow-list:"
  echo "$extra"
  exit 1
fi
echo "dependencies OK"
