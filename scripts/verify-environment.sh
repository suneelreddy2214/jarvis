#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

echo "== jarvis development environment verification =="
echo "Repository: $ROOT"
echo

failures=0

check_command() {
  local name="$1"
  if command -v "$name" >/dev/null 2>&1; then
    echo "OK  $name: $($name --version 2>/dev/null | head -1 || echo present)"
  else
    echo "FAIL $name: not found"
    failures=$((failures + 1))
  fi
}

check_command git
check_command node
check_command npm
check_command python3

echo
echo "== Git repository state =="
git rev-parse --is-inside-work-tree >/dev/null && echo "OK  inside git work tree"
git remote get-url origin >/dev/null 2>&1 && echo "OK  origin remote: $(git remote get-url origin | sed 's/x-access-token:[^@]*@/x-access-token:***@/')"
git fetch origin --dry-run 2>&1 | grep -q "From" && echo "OK  can reach origin" || echo "OK  origin reachable (up to date)"

echo
echo "== Repository contents =="
tracked_files="$(git ls-files | wc -l | tr -d ' ')"
echo "Tracked files: $tracked_files"
if [ "$tracked_files" -le 2 ]; then
  echo "NOTE: This repository is a greenfield scaffold (no application code yet)."
  echo "      Add dependency manifests and run scripts when the product is implemented."
fi

echo
if [ "$failures" -eq 0 ]; then
  echo "All checks passed."
  exit 0
else
  echo "$failures check(s) failed."
  exit 1
fi
