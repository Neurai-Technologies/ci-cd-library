#!/usr/bin/env bash
# Blast-radius and corruption guard, sourced by pre-commit and pre-push.
#
# Backstops the "a bad or unisolated git call wiped the repository" class.
# Origin: an unisolated `git init` under a hook replaced the index, tripped
# core.bare=true, and a mass-deletion commit reached main. Ordinary commits
# pass untouched; only a real mass deletion or the corruption signature trips.
#
# Overrides, only when a large deletion is genuinely intended:
#   GIT_GUARD_ACK=1            allow a flagged mass deletion this once
#   GIT_GUARD_DELETE_LIMIT=N   raise the absolute deletion threshold (default 25)

_guard_core_bare() {
  if [ "$(git config --get core.bare 2>/dev/null)" = "true" ]; then
    echo "[guard] ABORT: core.bare=true on a working clone (corruption signature)." >&2
    echo "[guard]   Do not push. Inspect: git config core.bare" >&2
    return 1
  fi
  return 0
}

_guard_staged_blast() {
  local changed deleted tracked limit
  changed=$(git diff --cached --name-only 2>/dev/null | wc -l | tr -d ' ')
  [ "${changed:-0}" -gt 5 ] || return 0
  deleted=$(git diff --cached --diff-filter=D --name-only 2>/dev/null | wc -l | tr -d ' ')
  tracked=$(git ls-tree -r HEAD --name-only 2>/dev/null | wc -l | tr -d ' ')
  limit=${GIT_GUARD_DELETE_LIMIT:-25}
  if [ "${deleted:-0}" -gt "$limit" ] || { [ "${tracked:-0}" -gt 0 ] && [ "$((deleted * 2))" -gt "$tracked" ]; }; then
    if [ "${GIT_GUARD_ACK:-0}" = "1" ]; then
      echo "[guard] mass deletion acknowledged (GIT_GUARD_ACK=1): ${deleted}/${tracked} files." >&2
      return 0
    fi
    echo "[guard] BLOCK: this commit deletes ${deleted} of ${tracked} tracked files." >&2
    git diff --cached --diff-filter=D --name-only 2>/dev/null | sed 's/^/      - /' | head -15 >&2
    echo "[guard]   If intended: GIT_GUARD_ACK=1 git commit ..." >&2
    return 1
  fi
  return 0
}

guard_precommit() { _guard_core_bare && _guard_staged_blast; }
guard_prepush() { _guard_core_bare; }
