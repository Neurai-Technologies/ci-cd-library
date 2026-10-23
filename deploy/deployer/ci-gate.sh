#!/usr/bin/env bash
# Accept an Actions runs response on stdin. Shared by promotion and the CD image.
set -euo pipefail
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
verdict=$(jq --arg sha "${1:?commit sha required}" --arg repository "${2:?repository required}" \
  --slurpfile policy "$here/ci-policy.json" -f "$here/ci-gate.jq")
printf '%s\n' "$verdict" >&2
jq -e '.ok == true' <<< "$verdict" >/dev/null
