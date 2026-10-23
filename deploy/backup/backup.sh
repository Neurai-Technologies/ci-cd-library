#!/bin/sh
# Daily encrypted Postgres dumps with retention. Runs as a long-lived compose
# service (see compose.example.yml); writes to /backups, which is bind-mounted
# to BACKUP_DIR on the host so the operator can rsync it off-box.
#
# pipefail is load-bearing: without it a failing pg_dump still exits 0 through
# gzip|openssl, the empty file gets promoted as a "successful" backup, and
# retention then ages the real history out night by night.
set -eu
set -o pipefail

command -v openssl >/dev/null 2>&1 || {
    echo "openssl is missing from this image; backups cannot be encrypted" >&2
    exit 1
}
: "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE must be set}"
# The label-only salt the .hmac sidecars are keyed under; restore.sh reads
# the same key. Required, with no default: changing it invalidates the
# sidecars of every existing dump and archive (restore refuses them), so an
# existing deployment keeps the value its dumps were made with.
: "${BACKUP_HMAC_SALT:?BACKUP_HMAC_SALT must be set (product.settings)}"
# openssl enc -S keeps only the first 8 bytes of a salt, so a longer one
# would be cut silently and two salts sharing a prefix would give one key.
[ "$(printf %s "$BACKUP_HMAC_SALT" | wc -c | tr -d ' ')" -eq 8 ] || {
    echo "BACKUP_HMAC_SALT must be exactly 8 bytes (openssl uses only 8)" >&2
    exit 1
}
# The product's settings (env_file: product.settings).
: "${DB_SERVICE:?DB_SERVICE must be set (product.settings)}"
: "${DB_NAME:?DB_NAME must be set (product.settings)}"
: "${DB_BACKUP_USER:?DB_BACKUP_USER must be set (product.settings)}"
: "${BACKUP_PREFIX:?BACKUP_PREFIX must be set (product.settings)}"

KEEP_DAYS="${BACKUP_KEEP_DAYS:-14}"
# The ceiling, enforced whatever the dumps do. See the two prunes in the loop:
# KEEP_DAYS is the ordinary window and only applies after a good write, which
# leaves the published promise unkept exactly when dumps are failing. This one
# always runs. It sits under the window /privacy publishes (60 days), and the
# distance between the two is the time a broken chain has to be noticed in.
MAX_DAYS="${BACKUP_MAX_DAYS:-55}"
# /backups in the container; overridable so the script can run under a test.
ROOT="${BACKUP_ROOT:-/backups}"
# Touched after a dump has passed the size and HMAC checks below, on a small
# volume the worker mounts read-only. The worker's daily pass reads its age and
# raises the alarm when backups have stopped. The dumps themselves stay in a
# root-only directory the worker cannot list, which is how it should be.
HEARTBEAT="${BACKUP_HEARTBEAT:-/heartbeat/last-success}"

if [ "$MAX_DAYS" -lt "$KEEP_DAYS" ]; then
    echo "BACKUP_MAX_DAYS ($MAX_DAYS) is below BACKUP_KEEP_DAYS ($KEEP_DAYS); dumps will be deleted at the lower figure" >&2
fi

# Encrypt-then-MAC. aes-256-cbc keeps a dump confidential but not tamper-
# evident, so each dump gets a .hmac sidecar over the ciphertext, keyed from
# the passphrase under its own salt. restore.sh derives the key the same way
# and refuses a file that does not match before decrypting anything.
mac_key() {
    # PBKDF2 at openssl's default 10,000 rounds under a fixed, label-only salt
    # (BACKUP_HMAC_SALT, hex-encoded for -S exactly as restore.sh does), so guessing the passphrase from a sidecar costs as much as
    # guessing it from the ciphertext. -P prints the derived key, nothing is
    # encrypted, and the passphrase still travels by env, never argv.
    openssl enc -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE -S "$(printf '%s' "$BACKUP_HMAC_SALT" | od -An -tx1 | tr -d ' \n')" -P | sed -n 's/^key=//p'
}
hmac_of() {
    openssl dgst -sha256 -mac HMAC -macopt "hexkey:$(mac_key)" -r "$1" | cut -c1-64
}

# Off-site copy: every dump and its sidecar are also PUT to an S3 bucket
# (Cloudflare R2) when BACKUP_R2_ENDPOINT is set. curl signs the request
# itself; the credentials travel by stdin config, never argv. Best effort:
# the local dump is already safe, so a failed upload is logged, not fatal.
R2_ENDPOINT="${BACKUP_R2_ENDPOINT:-}"
R2_BUCKET="${BACKUP_R2_BUCKET:-}"
offsite() {
    [ -n "$R2_ENDPOINT" ] || return 0
    for f in "$@"; do
        if printf 'user = "%s:%s"\n' "${BACKUP_R2_ACCESS_KEY_ID:-}" "${BACKUP_R2_SECRET_ACCESS_KEY:-}" \
            | curl -fsS --max-time 300 --proto '=https' -K - --aws-sigv4 "aws:amz:auto:s3" -T "$f" -o /dev/null \
                "${R2_ENDPOINT}/${R2_BUCKET}/$(basename "$f")"; then
            echo "off-site copy written: $(basename "$f")"
        else
            echo "off-site copy FAILED: $(basename "$f")" >&2
        fi
    done
}
# Say at startup whether uploads will work, not only when the first one fails.
# The credentials are spliced into a curl config line, where a newline would
# start a second directive; R2 keys never need anything outside this set.
if [ -n "$R2_ENDPOINT" ]; then
    case "$R2_ENDPOINT" in
        https://*) ;;
        *) echo "off-site copies MISCONFIGURED: BACKUP_R2_ENDPOINT must start with https://" >&2; R2_ENDPOINT="" ;;
    esac
fi
if [ -n "$R2_ENDPOINT" ] && { [ -z "$R2_BUCKET" ] || [ -z "${BACKUP_R2_ACCESS_KEY_ID:-}" ] || [ -z "${BACKUP_R2_SECRET_ACCESS_KEY:-}" ]; }; then
    echo "off-site copies MISCONFIGURED: bucket, access key id or secret is empty" >&2
    R2_ENDPOINT=""
fi
case "${BACKUP_R2_ACCESS_KEY_ID:-}:${BACKUP_R2_SECRET_ACCESS_KEY:-}" in
    *[!A-Za-z0-9+/=_:-]*) echo "off-site copies MISCONFIGURED: credentials contain unexpected characters" >&2; R2_ENDPOINT="" ;;
esac
if [ -n "$R2_ENDPOINT" ]; then
    echo "off-site copies on: bucket ${R2_BUCKET}"
else
    echo "off-site copies off"
fi

while true; do
    # A container killed mid-write leaves a .part behind; clear it up front.
    find "$ROOT" -name '*.part' -delete
    # The ceiling, before the dump is attempted and whatever it does. Retention
    # used to live entirely in the success branch below, so a database too
    # broken to dump was also a database whose old dumps stopped expiring - and
    # personal data outliving the window /privacy publishes is a promise broken
    # in the one case nobody is watching. Deliberately a second, longer window
    # rather than a move of the first: the reason the prune sits in the success
    # branch is still true, and a broken database must not age the good history
    # out from under the operator at fourteen days.
    find "$ROOT" -name "${BACKUP_PREFIX}-*.sql.gz.enc*" -mtime +"$MAX_DAYS" -delete
    ts="$(date -u +%Y%m%d-%H%M%S)"
    out="${ROOT}/${BACKUP_PREFIX}-${ts}.sql.gz.enc"
    tmp="${out}.part"
    # -pbkdf2 so the passphrase is stretched; env: keeps it out of argv.
    # DB_BACKUP_USER is a read-only role: this container never holds the owner
    # credential, so compromising it yields SELECT, not write/DDL.
    if pg_dump -h "$DB_SERVICE" -U "$DB_BACKUP_USER" "$DB_NAME" \
        | gzip \
        | openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_PASSPHRASE -out "$tmp" \
        && [ "$(wc -c <"$tmp")" -gt 1024 ] \
        && hmac_of "$tmp" >"${out}.hmac"; then
        mv "$tmp" "$out"
        echo "backup written: $out ($(wc -c <"$out") bytes)"
        # The dump is safe on disk at this point; a heartbeat that cannot be
        # written is worth a line, not a failed backup.
        date -u +%Y-%m-%dT%H:%M:%SZ >"$HEARTBEAT" 2>/dev/null \
            || echo "heartbeat not written: $HEARTBEAT (the alarm will think backups stopped)" >&2
        offsite "$out" "${out}.hmac"
        # Retention prunes only after a successful, plausibly-sized write: a
        # broken database must never age the good history out from under the
        # operator.
        find "$ROOT" -name "${BACKUP_PREFIX}-*.sql.gz.enc*" -mtime +"$KEEP_DAYS" -delete
    else
        rm -f "$tmp" "${out}.hmac"
        echo "backup FAILED at ${ts}" >&2
    fi
    # Every 6h from container start (plus the dump-on-start above): the dumps
    # are the only disaster path, and 2026-09-01 proved the loss window is
    # whatever this interval is. A restart just moves the window.
    sleep 21600
done
