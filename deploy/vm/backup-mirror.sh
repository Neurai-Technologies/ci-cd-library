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
# size and that every copied dump has its .hmac beside it; prune copies older
# than BACKUP_MIRROR_KEEP_DAYS, matching the dump names only; write a
# heartbeat. Alerts go on failure and on recovery, never on success. The
# dumps are encrypted already; this adds no encryption of its own.
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

fail() {
    alert "${PRODUCT} backup mirror FAILED: $*"
    touch "$FAILED_MARK" 2>/dev/null || echo "could not write $FAILED_MARK"
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
REAL_SRC="$(cd "$SRC" && pwd -P)" || fail "could not resolve $SRC"
d="$MIRROR"
while [ ! -e "$d" ] && [ ! -L "$d" ]; do d="$(dirname "$d")"; done
real="$(cd "$d" && pwd -P)" || fail "could not resolve $d"
case "$real" in
    "$REAL_VOLUME" | "$REAL_VOLUME"/*) ;;
    *) fail "BACKUP_MIRROR_DIR ($MIRROR) resolves outside BACKUP_MIRROR_VOLUME ($real)" ;;
esac
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

# 3. Copy what the mirror lacks. Only the dump names: never a .part, the
#    backup lock or anything else in BACKUP_DIR. Each copy is written under a
#    name no dump pattern matches and renamed once its size checks out; a run
#    killed half way leaves one, cleared here first.
find "$MIRROR" -maxdepth 1 -type f -name "${BACKUP_PREFIX}-*.mirroring" -delete \
    || fail "could not clear unfinished copies in $MIRROR"
copied=0
copy_one() {
    local name to
    name="$(basename "$1")"
    to="$MIRROR/$name"
    [ ! -e "$to" ] || return 0
    # -P: a source swapped for a symlink since the check in the loop is
    # copied as a link, never followed, and then refused here.
    cp -pP "$1" "$to.mirroring" || fail "could not copy $name"
    if [ ! -f "$to.mirroring" ] || [ -L "$to.mirroring" ]; then
        rm -f "$to.mirroring"
        fail "$name was not a plain file when copied"
    fi
    [ "$(wc -c <"$1")" -eq "$(wc -c <"$to.mirroring")" ] || fail "the copy of $name differs in size"
    mv "$to.mirroring" "$to" || fail "could not rename the copy of $name"
    copied=$((copied + 1))
}
# One dump at a time, with its sidecar, sidecar first, so the mirror never
# holds a dump without one. BACKUP_DIR is writable from the containers, so a
# symlink there is not a dump: it could point anywhere on this Mac.
for f in "$SRC/${BACKUP_PREFIX}"-*.sql.gz.enc; do
    [ -f "$f" ] && [ ! -L "$f" ] || continue
    name="$(basename "$f")"
    [ ! -e "$MIRROR/$name" ] || [ ! -e "$MIRROR/$name.hmac" ] || continue
    # Past the prune's window already (by the dump's own time, which cp -p
    # keeps): a copy would only be pruned again tonight.
    [ -z "$(find "$f" -mtime +"$KEEP_DAYS")" ] || continue
    [ -f "$f.hmac" ] && [ ! -L "$f.hmac" ] || fail "$name has no .hmac sidecar to copy with it"
    copy_one "$f.hmac"
    copy_one "$f"
done
echo "copied $copied files to $MIRROR"

# 4. Prune copies past the window, by the dump names only.
find "$MIRROR" -maxdepth 1 -type f \
    \( -name "${BACKUP_PREFIX}-*.sql.gz.enc" -o -name "${BACKUP_PREFIX}-*.sql.gz.enc.hmac" \) \
    -mtime +"$KEEP_DAYS" -delete \
    || fail "could not prune $MIRROR"

# 5. Success: a heartbeat, and an alert only when the last run had failed.
date -u +%Y-%m-%dT%H:%M:%SZ >"$HEARTBEAT" 2>/dev/null || echo "heartbeat not written: $HEARTBEAT"
if [ -e "$FAILED_MARK" ]; then
    alert "${PRODUCT} backup mirror recovered: $copied files copied to $MIRROR"
    rm -f "$FAILED_MARK" 2>/dev/null || echo "could not remove $FAILED_MARK"
fi
echo "done"
