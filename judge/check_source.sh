#!/usr/bin/env bash
# Fail if engine or CLI source at <head> uses constructs the judge cannot measure
# fairly or that could escape it: child processes, threads, files, network,
# environment, unsafe code, embedded files, inline assembly, foreign code.
# Usage: check_source.sh <head-sha>
# This is defense in depth; CI also runs the candidate as an unprivileged user.
set -euo pipefail

head="$1"
status=0

check() {  # <pathspec> <description> <extended regex>
  local spec="$1" what="$2" re="$3" hits
  hits="$(git -c core.quotePath=false grep -nE -- "$re" "$head" -- "$spec" || true)"
  if [[ -n "$hits" ]]; then
    echo "not allowed in $spec ($what):"
    printf '%s\n' "$hits" | sed 's/^/  /' | head -20
    status=1
  fi
}

for spec in 'crates/engine/src' 'crates/cli/src'; do
  check "$spec" "threads, files, network, OS access" '::(thread|fs|net|os|sync|ffi)\b|\b(thread|fs|net|os|sync|ffi)::|\bspawn\b'
  check "$spec" "child processes" '\bCommand::|process::(Command|Child|Stdio)|process::\{[^}]*\b(Command|Child|Stdio)\b'
  check "$spec" "environment variables" '\benv::(var|vars|var_os|vars_os|set_var|remove_var)\b|\b(option_)?env!'
  check "$spec" "unsafe or foreign code" '\bunsafe\b|\bextern\b|\basm!|global_asm!|#\s*!?\[\s*(no_mangle|link|export_name)'
  check "$spec" "embedded or out-of-tree files" '\binclude(_bytes|_str)?!|#\s*\[\s*path\b'
  check "$spec" "renamed std" '\bextern\s+crate\b|\buse\s+(::)?(std|core|alloc)\s+as\b'
  check "$spec" "use lists that may hide the above" '\buse\s+(::)?(std|core|alloc)::\{[^}]*\b(thread|fs|net|os|sync|ffi|env|process)\b'
done
check 'crates/engine/src' "I/O and environment" '\b(io|env|process)::'

if (( status == 0 )); then echo "source restrictions OK"; fi
exit "$status"
