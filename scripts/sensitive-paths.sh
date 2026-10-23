#!/usr/bin/env bash
# The one definition of "a path that must never be committed". Reads
# NUL-separated paths on stdin (git ... -z), prints offenders, exits 1 when
# any. Used by .githooks/pre-commit (staged names), `just ci` and GitHub CI
# (all tracked names), so the remote enforces it without the hook.
# Keep .gitignore in step with this list.
# Templates are allowed: .example, .sample, .template, .dist.
# No escape hatch by design: rename a public CA bundle or fixture instead.
# Directory rules: secrets/ and keystores/ anywhere; backups/ and credentials/
# at the repository root only, so source folders with those names elsewhere pass.
set -uo pipefail
export LC_ALL=C

pattern='(^|/)\.env($|[._-])|\.env$|(^|/)\.envrc$|(^|/)\.dev\.vars($|\.)|(^|/)env\.json$'
pattern="$pattern"'|\.(pem|key|cert|p8|p12|pfx|jks|keystore|mobileprovision|ppk|kdbx|tfvars|tfstate)$|\.tfstate\.backup$'
pattern="$pattern"'|(^|/)id_(rsa|ed25519|ecdsa|dsa)$|(^|/)token$|[-_.]token$|\.token$'
pattern="$pattern"'|(^|/)(credentials|credentials\.json|auth\.json|oauth_creds\.json|\.git-credentials|\.netrc|\.npmrc|\.pypirc)$'
pattern="$pattern"'|(^|/)client_secret_[^/]*\.json$|-service-account\.json$|(^|/)play-publisher\.json$|(^|/)api-project-[^/]*\.json$|(^|/)google-services\.json$|(^|/)GoogleService-Info\.plist$'
pattern="$pattern"'|(^|/)(key|local|secrets)\.properties$|(^|/)secrets\.enc$|(^|/)\.secrets\.key$|(^|/)(secrets|keystores)/|^(backups|credentials)/'
pattern="$pattern"'|\.(db|db-shm|db-wal|sqlite|sqlite3|dump)$|\.sql\.gz(\.enc)?$'

names=$(tr '\0' '\n') || { echo "BLOCK: cannot read the path list" >&2; exit 1; }
matched=$(printf '%s\n' "$names" | grep -iE -- "$pattern"); rc=$?
[ "$rc" -le 1 ] || { echo "BLOCK: path check failed" >&2; exit 1; }
blocked=$(printf '%s\n' "$matched" | grep -viE -- '\.(example|sample|template|dist)$'); rc=$?
[ "$rc" -le 1 ] || { echo "BLOCK: path check failed" >&2; exit 1; }
if [ -n "$blocked" ]; then
  echo "BLOCK: sensitive path(s):" >&2
  printf '%s\n' "$blocked" | sed 's/^/      /' >&2
  exit 1
fi
exit 0
