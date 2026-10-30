#!/bin/sh
# Source-only backup encryption API. No configuration is read until a function
# is called. Keep format-specific validation and legacy opt-ins in the caller.

_backup_crypto_require() {
    set -eu
    command -v openssl >/dev/null 2>&1 || { echo 'backup crypto: openssl is required' >&2; return 1; }
    : "${BACKUP_PASSPHRASE:?BACKUP_PASSPHRASE must be set}"
    : "${BACKUP_HMAC_SALT:?BACKUP_HMAC_SALT must be set}"
    [ "$(printf %s "$BACKUP_HMAC_SALT" | wc -c | tr -d ' ')" -eq 8 ] || {
        echo 'backup crypto: BACKUP_HMAC_SALT must be exactly 8 bytes' >&2
        return 1
    }
}

_backup_crypto_hmac() {
    # Existing derivation and OpenSSL PBKDF2 defaults; do not change the salt,
    # digest or iteration count without a separately designed format migration.
    derived=$(openssl enc -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE -S "$(printf '%s' "$BACKUP_HMAC_SALT" | od -An -tx1 | tr -d ' \n')" -P) || return 1
    key=$(printf '%s\n' "$derived" | sed -n 's/^key=//p')
    case "$key" in ''|*[!0-9A-Fa-f]*) echo 'backup crypto: could not derive the MAC key' >&2; return 1 ;; esac
    [ "${#key}" -eq 64 ] || { echo 'backup crypto: could not derive the MAC key' >&2; return 1; }
    # The derived MAC key is an internal openssl argument, as in the old code.
    # The passphrase itself is always read from the environment, never argv.
    digest=$(openssl dgst -sha256 -mac HMAC -macopt "hexkey:$key" -r "$1") || return 1
    digest=${digest%% *}
    case "$digest" in ''|*[!0-9a-f]*) return 1 ;; esac
    [ "${#digest}" -eq 64 ] || return 1
    printf '%s\n' "$digest"
}

_backup_crypto_authenticate() {
    # Verify a private snapshot: a later change to the source must not become
    # the bytes decrypted after an earlier successful integrity check.
    cipher=$1
    case "$cipher" in /*) ;; *) cipher="$PWD/$cipher" ;; esac
    for file in "$cipher" "$cipher.hmac"; do
        [ -f "$file" ] && [ ! -L "$file" ] || {
            echo 'backup crypto: ciphertext and .hmac must be plain files' >&2
            return 1
        }
    done
    cp -P "$cipher" "$2/ciphertext" || return 1
    cp -P "$cipher.hmac" "$2/sidecar" || return 1
    [ -f "$2/ciphertext" ] && [ ! -L "$2/ciphertext" ] && [ -f "$2/sidecar" ] && [ ! -L "$2/sidecar" ] || return 1
    expected=$(tr -d '[:space:]' <"$2/sidecar") || return 1
    case "$expected" in ''|*[!0-9a-f]*) echo 'backup crypto: malformed .hmac sidecar' >&2; return 1 ;; esac
    [ "${#expected}" -eq 64 ] || { echo 'backup crypto: malformed .hmac sidecar' >&2; return 1; }
    computed=$(_backup_crypto_hmac "$2/ciphertext") || return 1
    [ "$computed" = "$expected" ] || {
        echo 'backup crypto: ciphertext does not match its .hmac sidecar (modified, truncated, wrong passphrase or salt)' >&2
        return 1
    }
}

# Reads arbitrary stdin, writes OUTFILE and OUTFILE.hmac once. The ciphertext's
# final name is the completion marker; a caller must also check its producer's
# exit status before promoting a transient OUTFILE to an application artifact.
backup_encrypt() (
    set +x
    set -eu
    umask 077
    [ "$#" -eq 1 ] && [ -n "$1" ] || { echo 'usage: backup_encrypt OUTFILE' >&2; exit 2; }
    _backup_crypto_require || exit 1
    out=$1
    [ ! -e "$out" ] && [ ! -L "$out" ] && [ ! -e "$out.hmac" ] && [ ! -L "$out.hmac" ] || {
        echo 'backup crypto: output already exists' >&2
        exit 1
    }
    work=$(mktemp -d "$(dirname "$out")/.backup-crypto.XXXXXX") || exit 1
    complete=0
    trap 'if [ "$complete" -eq 0 ]; then
        if [ "$out" -ef "$work/ciphertext" ]; then rm -f "$out"; fi
        if [ "$out.hmac" -ef "$work/sidecar" ]; then rm -f "$out.hmac"; fi
    fi; rm -rf "$work"' EXIT
    trap 'exit 1' HUP INT TERM
    openssl enc -aes-256-cbc -pbkdf2 -salt -pass env:BACKUP_PASSPHRASE -out "$work/ciphertext" || exit 1
    _backup_crypto_hmac "$work/ciphertext" >"$work/sidecar" || exit 1
    # Same-filesystem hard links do not overwrite a competing immutable output.
    ln "$work/sidecar" "$out.hmac" || exit 1
    ln "$work/ciphertext" "$out" || exit 1
    complete=1
)

# Authentication only; no plaintext is produced.
backup_verify() (
    set +x
    set -eu
    umask 077
    [ "$#" -eq 1 ] || { echo 'usage: backup_verify FILE' >&2; exit 2; }
    _backup_crypto_require || exit 1
    work=$(mktemp -d "${TMPDIR:-/tmp}/backup-crypto.XXXXXX") || exit 1
    trap 'rm -rf "$work"' EXIT
    trap 'exit 1' HUP INT TERM
    _backup_crypto_authenticate "$1" "$work" || exit 1
)

# Authenticates and completely decrypts a private snapshot before emitting any
# plaintext to stdout. The caller owns decompression and application checks.
backup_decrypt() (
    set +x
    set -eu
    umask 077
    [ "$#" -eq 1 ] || { echo 'usage: backup_decrypt FILE' >&2; exit 2; }
    _backup_crypto_require || exit 1
    work=$(mktemp -d "${TMPDIR:-/tmp}/backup-crypto.XXXXXX") || exit 1
    trap 'rm -rf "$work"' EXIT
    trap 'exit 1' HUP INT TERM
    _backup_crypto_authenticate "$1" "$work" || exit 1
    openssl enc -d -aes-256-cbc -pbkdf2 -pass env:BACKUP_PASSPHRASE -in "$work/ciphertext" -out "$work/plaintext" || exit 1
    cat "$work/plaintext"
)
