#!/bin/bash
# Nightly copy of the encrypted dumps from BACKUP_DIR to a second disk on the
# host Mac, so one failed disk does not take the only local copies with it.
#
# Installed as a LaunchAgent by install-autostart.sh when BACKUP_MIRROR_DIR is
# set (backup-mirror.plist.template, daily at BACKUP_MIRROR_TIME). It runs the
# installed copy beside the checked product.settings, like vm-autostart.sh.
#
# Each run: refuse a destination that could overwrite or prune the source;
# stop (exit 0, one alert a day) when the second disk is not mounted, so
# nothing is ever written onto the system disk in its place; copy the dumps
# and sidecars the mirror lacks, never replacing one it has; check each copy's
# bytes and that every copied dump has its .hmac beside it; prune copies older
# than BACKUP_MIRROR_KEEP_DAYS, matching the dump names only; write a
# heartbeat. Alerts go on failure and on recovery, never on success. The
# dumps are encrypted already; this adds no encryption of its own. Optional
# explicit archive families use the same run and isolated destination folders.
set -eu
# The log, heartbeat and state files are the operator's alone; nothing else
# reads them. Copies keep their source's mode (cp -p).
umask 077
# Settings: the product.settings install-autostart.sh copied beside this script.
# shellcheck source=/dev/null
. "$(dirname "$0")/product.settings"
: "${PRODUCT:?}"
: "${REPO_DIR:?}"
: "${APP_SUPPORT_DIR:?}"
: "${BACKUP_PREFIX:?BACKUP_PREFIX must be set (product.settings)}"
SRC="${BACKUP_DIR:-${REPO_DIR}/backups}"
MIRROR="${BACKUP_MIRROR_DIR:-}"
VOLUME="${BACKUP_MIRROR_VOLUME:-}"
KEEP_DAYS="${BACKUP_MIRROR_KEEP_DAYS:-${BACKUP_KEEP_DAYS:-14}}"

LOG="${HOME}/Library/Logs/${PRODUCT}-backup-mirror.log"
FAILED_MARK="${APP_SUPPORT_DIR}/backup-mirror.failed"
UNMOUNTED_DAY="${APP_SUPPORT_DIR}/backup-mirror.unmounted"
HEARTBEAT="${APP_SUPPORT_DIR}/backup-mirror.last-success"
# The alert webhook (DEPLOY_ALERT_URL in the operator's .env) carries a bot
# token, so it never sits in product.settings: the operator writes it, one
# line, to this file (chmod 600). Absent: log lines only.
URL_FILE="${APP_SUPPORT_DIR}/alert-url"

mkdir -p "$(dirname "$LOG")" "$APP_SUPPORT_DIR"
exec >>"$LOG" 2>&1
echo "=== $(date '+%Y-%m-%d %H:%M:%S') backup mirror ==="

# A token file anyone else could read, or write to redirect the alerts, is
# not used: a plain file of this user's, mode 600, or the run stops here
# (find, not stat: the two stats disagree, and the tests run on Linux too).
ALERT_URL=""
if [ -e "$URL_FILE" ] || [ -L "$URL_FILE" ]; then
    if [ -L "$URL_FILE" ] || [ -z "$(find "$URL_FILE" -maxdepth 0 -type f -user "$(id -u)" -perm 600)" ]; then
        echo "REFUSED: $URL_FILE must be a plain file owned by this user with mode 600 (chmod 600); nothing done"
        exit 1
    fi
    ALERT_URL="$(head -n 1 "$URL_FILE")"
fi

alert() {
    # The deployer's alert, same shape: the URL travels by stdin config,
    # never argv, and a lost message never fails the run.
    echo "ALERT: $*"
    [ -n "$ALERT_URL" ] || return 0
    case "$ALERT_URL" in
        *[!A-Za-z0-9._~:/?=\&%+-]*) echo "the alert URL has unexpected characters; alerts off"; return 0 ;;
    esac
    curl -fsS --max-time 10 --retry 2 --retry-delay 2 --data-urlencode "text=$*" -K - <<<"url = \"${ALERT_URL}\"" >/dev/null 2>&1 \
        || echo "alert delivery failed"
}

note_failure() {
    alert "${PRODUCT} backup mirror FAILED: $*"
    touch "$FAILED_MARK" 2>/dev/null || echo "could not write $FAILED_MARK"
}

fail() {
    note_failure "$*"
    exit 1
}

# 1. A destination that could reach the source, or the whole disk, is refused.
[ -n "$MIRROR" ] || fail "BACKUP_MIRROR_DIR is empty"
[ -n "$VOLUME" ] || fail "BACKUP_MIRROR_VOLUME is empty"
# Trailing slashes off, so the comparisons below see one spelling of a path;
# only / itself is left empty by that.
while [ "${SRC%/}" != "$SRC" ]; do SRC="${SRC%/}"; done
while [ "${MIRROR%/}" != "$MIRROR" ]; do MIRROR="${MIRROR%/}"; done
while [ "${VOLUME%/}" != "$VOLUME" ]; do VOLUME="${VOLUME%/}"; done
[ -n "$MIRROR" ] || fail "BACKUP_MIRROR_DIR must not be /"
[ -n "$VOLUME" ] || fail "BACKUP_MIRROR_VOLUME must not be /"
[ -n "$SRC" ] || fail "BACKUP_DIR must not be /"
for path in "$SRC" "$MIRROR" "$VOLUME"; do
    case "$path" in
        /*) ;;
        *) fail "$path is not an absolute path" ;;
    esac
    case "$path" in
        */.. | */../* | */. | */./*) fail "$path must not contain . or .. parts" ;;
    esac
done
case "$MIRROR" in
    "$SRC" | "$SRC"/*) fail "BACKUP_MIRROR_DIR ($MIRROR) is BACKUP_DIR or inside it" ;;
esac
case "$SRC" in
    "$MIRROR"/*) fail "BACKUP_DIR ($SRC) is inside BACKUP_MIRROR_DIR" ;;
esac
case "$MIRROR" in
    "$VOLUME"/?*) ;;
    *) fail "BACKUP_MIRROR_DIR ($MIRROR) must be a folder inside BACKUP_MIRROR_VOLUME ($VOLUME)" ;;
esac
# The prefix goes into the find -name patterns, where * or ? would widen the prune.
case "$BACKUP_PREFIX" in
    *[!A-Za-z0-9_-]*) fail "BACKUP_PREFIX may hold only letters, digits, _ and -" ;;
esac
case "$KEEP_DAYS" in
    '' | *[!0-9]*) fail "BACKUP_MIRROR_KEEP_DAYS must be a whole number of days (got $KEEP_DAYS)" ;;
esac

# Additional explicit archive families share this process, alert state and disk.
# Each record is namespace|absolute source|prefix|suffix. Namespaces are flat,
# lowercase and unique; extra families use fixed UTC-shaped timestamp names.
SOURCES=("$SRC")
NAMESPACES=("")
PATTERNS=("${BACKUP_PREFIX}-*.sql.gz.enc")
while IFS= read -r record; do
    [ -n "$record" ] || continue
    separators="${record//[^|]/}"
    [ "${#separators}" -eq 3 ] || fail "extra mirror source must have exactly four pipe-separated fields"
    IFS='|' read -r namespace source prefix suffix <<<"$record"
    case "$namespace" in
        ''|*[!a-z0-9_-]*) fail "extra mirror namespace must use lowercase letters, digits, _ and -" ;;
    esac
    case "$prefix" in
        ''|*[!A-Za-z0-9_-]*) fail "extra mirror prefix may hold only letters, digits, _ and -" ;;
    esac
    case "$suffix" in sql.gz.enc|tar.gz.enc) ;; *) fail "extra mirror suffix must be sql.gz.enc or tar.gz.enc" ;; esac
    case "$source" in /*) ;; *) fail "extra mirror source must be an absolute path" ;; esac
    case "$source" in /|*/..|*/../*|*/.|*/./*) fail "extra mirror source must not be / or contain . or .. parts" ;; esac
    [ -d "$source" ] || fail "extra mirror source ($source) is not a directory"
    for used in "${NAMESPACES[@]}"; do
        [ "$used" != "$namespace" ] || fail "duplicate extra mirror namespace: $namespace"
    done
    SOURCES+=("$source")
    NAMESPACES+=("$namespace")
    PATTERNS+=("${prefix}-[0-9][0-9][0-9][0-9][0-9][0-9][0-9][0-9]-[0-9][0-9][0-9][0-9][0-9][0-9].${suffix}")
done <<<"${BACKUP_MIRROR_EXTRA_SOURCES:-}"

# 2. The second disk must be mounted at BACKUP_MIRROR_VOLUME. A directory test
#    is not enough: with the disk absent, its mount point can still exist as a
#    plain folder on the system disk, and a copy would fill that instead.
if ! mount | grep -qF " on ${VOLUME} ("; then
    echo "destination not mounted: $VOLUME"
    today="$(date +%Y-%m-%d)"
    if [ "$(cat "$UNMOUNTED_DAY" 2>/dev/null)" != "$today" ]; then
        alert "${PRODUCT} backup mirror: destination not mounted ($VOLUME); nothing copied"
        echo "$today" >"$UNMOUNTED_DAY" 2>/dev/null || echo "could not write $UNMOUNTED_DAY"
    fi
    touch "$FAILED_MARK" 2>/dev/null || echo "could not write $FAILED_MARK"
    exit 0
fi

[ -d "$SRC" ] || fail "BACKUP_DIR ($SRC) is not a directory"
# The checks above read the paths as written; a symlink on the way could
# still lead the copy and the prune somewhere else. So the real paths decide:
# first of the nearest folder that exists, before anything is created, then
# of the mirror itself.
REAL_VOLUME="$(cd "$VOLUME" && pwd -P)" || fail "could not resolve $VOLUME"
REAL_SOURCES=()
for source in "${SOURCES[@]}"; do
    resolved="$(cd "$source" && pwd -P)" || fail "could not resolve $source"
    [ -n "$(find "$resolved" -maxdepth 0 -type d -user "$(id -u)" ! -perm -020 ! -perm -002)" ] \
        || fail "$source must be owned by this user and not writable by other users"
    REAL_SOURCES+=("$resolved")
done
REAL_SRC="${REAL_SOURCES[0]}"
d="$MIRROR"
while [ ! -e "$d" ] && [ ! -L "$d" ]; do d="$(dirname "$d")"; done
real="$(cd "$d" && pwd -P)" || fail "could not resolve $d"
case "$real" in
    "$REAL_VOLUME" | "$REAL_VOLUME"/*) ;;
    *) fail "BACKUP_MIRROR_DIR ($MIRROR) resolves outside BACKUP_MIRROR_VOLUME ($real)" ;;
esac
# Check every source against the proposed real destination before creating it.
proposed="$real${MIRROR#"$d"}"
for source in "${REAL_SOURCES[@]}"; do
    case "$proposed" in "$source"|"$source"/*) fail "mirror destination resolves to a source or inside it" ;; esac
    case "$source" in "$proposed"/*) fail "a mirror source resolves inside the destination" ;; esac
done
mkdir -p "$MIRROR" || fail "could not create $MIRROR"
REAL_MIRROR="$(cd "$MIRROR" && pwd -P)" || fail "could not resolve $MIRROR"
case "$REAL_MIRROR" in
    "$REAL_VOLUME"/?*) ;;
    *) fail "BACKUP_MIRROR_DIR ($MIRROR) resolves outside BACKUP_MIRROR_VOLUME ($REAL_MIRROR)" ;;
esac
case "$REAL_MIRROR" in
    "$REAL_SRC" | "$REAL_SRC"/*) fail "BACKUP_MIRROR_DIR ($MIRROR) resolves to BACKUP_DIR or inside it" ;;
esac
case "$REAL_SRC" in
    "$REAL_MIRROR"/*) fail "BACKUP_DIR ($SRC) resolves inside BACKUP_MIRROR_DIR" ;;
esac
MIRROR="$REAL_MIRROR"

# 3. Validate all namespaces before copying or pruning any family. A namespace
# is one plain folder, so it cannot alias a different family through a link.
TARGETS=("$MIRROR")
for ((i=1; i<${#SOURCES[@]}; i++)); do
    target="$MIRROR/${NAMESPACES[$i]}"
    [ ! -L "$target" ] || fail "extra mirror destination is a symlink: $target"
    if [ -e "$target" ] && [ ! -d "$target" ]; then fail "extra mirror destination is not a directory: $target"; fi
    mkdir -p "$target" || fail "could not create $target"
    TARGETS+=("$target")
done
for target in "${TARGETS[@]}"; do
    [ -n "$(find "$target" -maxdepth 0 -type d -user "$(id -u)" ! -perm -020 ! -perm -002)" ] \
        || fail "$target must be owned by this user and not writable by other users"
done

copied=0
pair_failures=0
PAIR=""
PAIR_COMPLETE=0
cleanup() {
    local file
    [ -n "$PAIR" ] || return 0
    for file in "$PAIR.hmac" "$PAIR"; do
        if [ "$PAIR_COMPLETE" -eq 0 ] && [ "$file" -ef "$file.mirroring" ]; then rm -f "$file"; fi
        rm -f "$file.mirroring"
    done
}
trap cleanup EXIT
trap 'exit 1' HUP INT TERM

copy_one() {
    local from="$1" target="$2" name to compare_status
    name="$(basename "$from")"
    to="$target/$name"
    [ -f "$from" ] && [ ! -L "$from" ] || fail "$name was not a plain file when copied"
    [ -n "$(find "$from" -maxdepth 0 -type f -user "$(id -u)" ! -perm -020 ! -perm -002)" ] \
        || fail "$name must be owned by this user and not writable by other users"
    if [ -e "$to" ] || [ -L "$to" ]; then
        [ -f "$to" ] && [ ! -L "$to" ] || fail "$name in the mirror is not a plain file"
        [ -n "$(find "$to" -maxdepth 0 -type f -user "$(id -u)" ! -perm -020 ! -perm -002)" ] \
            || fail "$name in the mirror is not owned privately by this user"
        if cmp -s "$from" "$to"; then
            return 0
        else
            compare_status=$?
            [ "$compare_status" -eq 1 ] || fail "could not compare existing mirror copy of $name"
            note_failure "existing mirror copy of $name differs from its immutable source; pair skipped"
            pair_failures=$((pair_failures + 1))
            return 1
        fi
    fi
    [ ! -e "$to.mirroring" ] && [ ! -L "$to.mirroring" ] || fail "unfinished copy path is not clear: $name"
    cp -pP "$from" "$to.mirroring" || fail "could not copy $name"
    if [ ! -f "$to.mirroring" ] || [ -L "$to.mirroring" ]; then fail "$name was not a plain file when copied"; fi
    [ -f "$from" ] && [ ! -L "$from" ] || fail "$name was not a plain file when copied"
    [ "$(wc -c <"$from")" -eq "$(wc -c <"$to.mirroring")" ] || fail "the copy of $name differs in size"
    cmp -s "$from" "$to.mirroring" || fail "the copy of $name differs in content"
    chmod 600 "$to.mirroring" || fail "could not protect the copy of $name"
    # Retain the staging inode until the pair is complete; cleanup then knows
    # which final links belong to this run, including a signal after publication.
    ln "$to.mirroring" "$to" || fail "could not publish the copy of $name"
    copied=$((copied + 1))
}

mirror_family() {
    local source="$1" target="$2" pattern="$3" f name pair_start
    find "$target" -maxdepth 1 -type f \
        \( -name "$pattern.mirroring" -o -name "$pattern.hmac.mirroring" \) -delete \
        || fail "could not clear unfinished copies in $target"
    for f in "$source"/$pattern; do
        [ -f "$f" ] && [ ! -L "$f" ] || continue
        name="$(basename "$f")"
        [ -z "$(find "$f" -mtime +"$KEEP_DAYS")" ] || continue
        [ -f "$f.hmac" ] && [ ! -L "$f.hmac" ] || fail "$name has no .hmac sidecar to copy with it"
        PAIR="$target/$name"
        PAIR_COMPLETE=0
        pair_start=$copied
        if ! copy_one "$f.hmac" "$target" || ! copy_one "$f" "$target"; then
            cleanup
            PAIR=""
            copied=$pair_start
            continue
        fi
        cmp -s "$f.hmac" "$PAIR.hmac" && cmp -s "$f" "$PAIR" \
            || fail "$name changed while its pair was being copied"
        PAIR_COMPLETE=1
        cleanup
        PAIR=""
    done
    # 4. Only this family's immutable encrypted names and sidecars can expire.
    find "$target" -maxdepth 1 -type f \
        \( -name "$pattern" -o -name "$pattern.hmac" \) -mtime +"$KEEP_DAYS" -delete \
        || fail "could not prune $target"
}
mirror_family "${REAL_SOURCES[0]}" "${TARGETS[0]}" "${PATTERNS[0]}"
for ((i=1; i<${#SOURCES[@]}; i++)); do
    mirror_family "${REAL_SOURCES[$i]}" "${TARGETS[$i]}" "${PATTERNS[$i]}"
done
echo "copied $copied files to $MIRROR"
if [ "$pair_failures" -gt 0 ]; then
    echo "$pair_failures mismatched pairs skipped; mirror run incomplete"
    exit 1
fi

# 5. Success: a heartbeat, and an alert only when the last run had failed.
date -u +%Y-%m-%dT%H:%M:%SZ >"$HEARTBEAT" 2>/dev/null || echo "heartbeat not written: $HEARTBEAT"
if [ -e "$FAILED_MARK" ]; then
    alert "${PRODUCT} backup mirror recovered: $copied files copied to $MIRROR"
    rm -f "$FAILED_MARK" 2>/dev/null || echo "could not remove $FAILED_MARK"
fi
echo "done"
