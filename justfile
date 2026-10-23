set shell := ["bash", "-euo", "pipefail", "-c"]

# Library baseline. Add the tests for checks/ and deploy/ here as they land.
ci: docs paths bypasses secrets

# One-time per clone, from a normal shell: hook shims, hooks path, tool warnings.
setup:
    bash scripts/setup-hooks.sh

docs:
    python3 scripts/check_docs.py

# No tracked path may match the sensitive-path list (also enforced per commit by the hook).
paths:
    git ls-files -z | bash scripts/sensitive-paths.sh

# The scan is only as strong as its config: no ignore file (every scan already
# ignores inline allow comments).
bypasses:
    test ! -e .gitleaksignore

# Committed history only. `gitleaks dir .` would also read gitignored files
# (a local .env, a virtualenv), so every push from a clone that keeps real
# local secrets would fail. The pre-commit hook scans each staged change, and
# GitHub CI scans the files of its clean checkout.
secrets:
    if git rev-parse --verify HEAD >/dev/null 2>&1; then gitleaks git . --redact --no-banner --ignore-gitleaks-allow --config .gitleaks.toml; else echo "No commits yet; nothing to scan (the pre-commit hook scans each staged change)."; fi

# Copy the shared block from CLAUDE.md into the other agent files.
sync-agents:
    python3 scripts/sync_agents.py

# Runs from the pre-commit hook when defined. Keep it fast; ci runs on push.
pre-commit: docs bypasses
