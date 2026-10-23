#!/bin/bash
# Put this library's vm-autostart.sh and the product's product.settings in place
# as the login agent's copies, render the LaunchAgent plist from
# launchagent.plist.template, then load the agent. Run the copy inside the
# product's checkout (the library is pulled in by git subtree at a tag):
#
#   bash <repo>/<LIBRARY_DIR>/deploy/vm/install-autostart.sh <ORIGIN_URL> <path to product.settings>
#
# ORIGIN_URL is the product repository on GitHub (https://github.com/... or
# git@github.com:...). It is an argument, not a setting: product.settings sits
# in the same agent-writable tree it is being checked against, so it cannot
# name the repository that vouches for it.
#
# Run it from a Mac terminal, not from a sandboxed agent: it writes to
# ~/Library, which the sandbox cannot.
#
# This installer loads the agent itself (launchctl bootstrap, step 8), as the
# product-specific installer it replaces did; it never kickstarts it.
# Loading the agent RUNS the script at once (RunAtLoad in the plist). Before
# loading, this installer requires: the script, the plist template and
# product.settings identical to a fresh clone of ORIGIN_URL's main
# (sandboxed agents can write the working tree and its .git, so neither its
# copy nor its git is trusted; only the clone's product.settings is ever
# sourced); the agent not already loaded; flock, docker and colima on the
# agent's own PATH; docker answering on the agent's socket; and the database
# volume present. The agent then re-checks the VM, the data disk and the
# volume itself, and with the volume present it only reports and exits 0.
set -eu

USAGE="usage: bash install-autostart.sh <ORIGIN_URL> <path to product.settings in this checkout>"
ORIGIN_URL="${1:?$USAGE}"
SETTINGS_ARG="${2:?$USAGE}"
case "$ORIGIN_URL" in
    https://github.com/* | git@github.com:*) ;;
    *) printf 'STOP: ORIGIN_URL must start with https://github.com/ or git@github.com: (got %s)\n' "$ORIGIN_URL"; exit 1 ;;
esac

# Where this checkout is, from the installer's own location
# (<repo>/<LIBRARY_DIR>/deploy/vm/), never from product.settings: the repo root
# is the nearest directory above the library holding a .git.
LIB_DIR="$(cd "$(dirname "$0")" && pwd)"
LIB_ROOT="$(dirname "$(dirname "$LIB_DIR")")"
REPO_ROOT="$(dirname "$LIB_ROOT")"
while [ ! -e "$REPO_ROOT/.git" ]; do
    [ "$REPO_ROOT" != / ] || { printf 'STOP: no repository found above %s\n' "$LIB_ROOT"; exit 1; }
    REPO_ROOT="$(dirname "$REPO_ROOT")"
done
LIBRARY_REL="${LIB_ROOT#"$REPO_ROOT"/}"
ENV_ABS="$(cd "$(dirname "$SETTINGS_ARG")" && pwd)/$(basename "$SETTINGS_ARG")"
case "$ENV_ABS" in
    "$REPO_ROOT"/*) ;;
    *) printf 'STOP: %s is not inside this checkout (%s)\n' "$ENV_ABS" "$REPO_ROOT"; exit 1 ;;
esac
SRC_REL="$LIBRARY_REL/deploy/vm/vm-autostart.sh"
TPL_REL="$LIBRARY_REL/deploy/vm/launchagent.plist.template"
ENV_REL="${ENV_ABS#"$REPO_ROOT"/}"

# The agent's own PATH (vm-autostart.sh sets exactly this), so a tool found
# here is a tool the agent will find at login.
export PATH=/opt/homebrew/bin:/usr/bin:/bin:/usr/sbin:/sbin

WORK=""
LABEL=""
PREV_SCRIPT=0
PREV_ENV=0
PREV_PLIST=0
SCRIPT_REPLACED=0
ENV_REPLACED=0
PLIST_REPLACED=0
LOADED=0

step() { printf '\n== %s\n' "$1"; }
stop() { printf 'STOP: %s\n' "$1"; exit 1; }
# For a sed replacement with | as the delimiter: |, & and \ are literal.
esc() { printf '%s' "$1" | sed -e 's/[|&\\]/\\&/g'; }

on_exit() {
    local status=$?
    set +e
    [ -n "$WORK" ] && rm -rf "$WORK"
    [ "$status" -eq 0 ] && return 0
    if [ "$SCRIPT_REPLACED" -eq 0 ] && [ "$ENV_REPLACED" -eq 0 ] && [ "$PLIST_REPLACED" -eq 0 ]; then
        printf 'Nothing was changed.\n'
        return 0
    fi
    printf '\nThis run failed AFTER replacing installed files.\n'
    if [ "$PLIST_REPLACED" -eq 1 ]; then
        printf 'The new plist is in LaunchAgents, so launchd will load it at the next login unless you undo this.\n'
    fi
    printf 'Agent loaded by this run: %s\n' "$([ "$LOADED" -eq 1 ] && echo yes || echo no)"
    printf 'To undo, run these in order:\n'
    printf '  launchctl bootout %s\n' "$LABEL"
    if [ "$SCRIPT_REPLACED" -eq 1 ]; then
        if [ "$PREV_SCRIPT" -eq 1 ]; then
            printf '  cp "%s/vm-autostart.sh" "%s"\n' "$BACKUP_DIR" "$DEST"
        else
            printf '  rm "%s"\n' "$DEST"
        fi
    fi
    if [ "$ENV_REPLACED" -eq 1 ]; then
        if [ "$PREV_ENV" -eq 1 ]; then
            printf '  cp "%s/product.settings" "%s"\n' "$BACKUP_DIR" "$DEST_ENV"
        else
            printf '  rm "%s"\n' "$DEST_ENV"
        fi
    fi
    if [ "$PLIST_REPLACED" -eq 1 ]; then
        if [ "$PREV_PLIST" -eq 1 ]; then
            printf '  cp "%s/%s" "%s"\n' "$BACKUP_DIR" "$PLIST_NAME" "$PLIST"
        else
            printf '  rm "%s"\n' "$PLIST"
        fi
    fi
    printf '(The bootout line reports an error if the agent was not loaded; that is fine.)\n'
    return 0
}
trap on_exit EXIT

step "1. flock, docker, colima and git must be on the agent's PATH"
for tool in flock docker colima git; do
    command -v "$tool" >/dev/null || stop "$tool not found on the agent's PATH ($PATH)"
done
echo "ok: flock, docker, colima and git found"

step "2. Take private copies and require them to match GitHub's main"
WORK=$(mktemp -d "${TMPDIR:-/tmp}/vm-autostart.XXXXXX")
cp "$LIB_DIR/vm-autostart.sh" "$WORK/vm-autostart.sh"
cp "$LIB_DIR/launchagent.plist.template" "$WORK/launchagent.plist.template"
cp "$ENV_ABS" "$WORK/product.settings"
# A fresh clone outside the working tree, from the URL given on the command
# line, run from inside WORK: git run in the checkout would honour its hooks
# and its .git/config (url.insteadOf could swap the source, core.sshCommand
# would run code), all writable by sandboxed agents, so it cannot vouch for
# the files it is checking. The operator's own global and system config stay
# on: they are trusted, and their credential helper is what a private https
# clone needs.
GIT_NO_REPLACE_OBJECTS=1 git -C "$WORK" -c core.hooksPath=/dev/null clone -q --depth 1 --no-checkout --branch main "$ORIGIN_URL" "$WORK/origin" \
    || stop "could not clone $ORIGIN_URL to check the files against main"
git -C "$WORK/origin" show "HEAD:$SRC_REL" > "$WORK/main-vm-autostart.sh" || stop "could not read $SRC_REL from GitHub's main"
git -C "$WORK/origin" show "HEAD:$TPL_REL" > "$WORK/main-template" || stop "could not read $TPL_REL from GitHub's main"
git -C "$WORK/origin" show "HEAD:$ENV_REL" > "$WORK/main-product.settings" || stop "could not read $ENV_REL from GitHub's main"
if ! cmp -s "$WORK/vm-autostart.sh" "$WORK/main-vm-autostart.sh"; then
    diff "$WORK/main-vm-autostart.sh" "$WORK/vm-autostart.sh" | head -40 || true
    stop "the working-tree $SRC_REL differs from GitHub's main (diff above: main first)"
fi
if ! cmp -s "$WORK/launchagent.plist.template" "$WORK/main-template"; then
    diff "$WORK/main-template" "$WORK/launchagent.plist.template" | head -40 || true
    stop "the working-tree $TPL_REL differs from GitHub's main (diff above: main first)"
fi
if ! cmp -s "$WORK/product.settings" "$WORK/main-product.settings"; then
    diff "$WORK/main-product.settings" "$WORK/product.settings" | head -40 || true
    stop "the working-tree $ENV_REL differs from GitHub's main (diff above: main first)"
fi
echo "ok: all three files match main of $ORIGIN_URL at $(git -C "$WORK/origin" rev-parse --short HEAD)"
echo "script sha256: $(shasum -a 256 "$WORK/vm-autostart.sh" | cut -d' ' -f1)"

# Only now read the settings, and from the clone, never the working tree.
# shellcheck source=/dev/null
. "$WORK/main-product.settings"
: "${PRODUCT:?}"
: "${REPO_DIR:?}"
: "${LIBRARY_DIR:?}"
: "${COLIMA_PROFILE:?}"
: "${COLIMA_HOME:?}"
: "${LAUNCH_LABEL:?}"
: "${APP_SUPPORT_DIR:?}"
: "${COMPOSE_PROJECT:?}"
# The agent will run restore.sh from REPO_DIR/LIBRARY_DIR; that must be this
# checkout, the one just verified.
[ "$REPO_DIR" = "$REPO_ROOT" ] || stop "product.settings names REPO_DIR $REPO_DIR, but this checkout is $REPO_ROOT"
[ "$LIBRARY_DIR" = "$LIBRARY_REL" ] || stop "product.settings names LIBRARY_DIR $LIBRARY_DIR, but this installer is in $LIBRARY_REL"
# The login job sources product.settings from APP_SUPPORT_DIR, so it must sit
# where sandboxed agents cannot write: under ~/Library, with no symlink on the
# way that could point it back into a writable tree.
# Trailing slashes off first, so $HOME/Library/ cannot pass as a folder below it.
while [ "${APP_SUPPORT_DIR%/}" != "$APP_SUPPORT_DIR" ]; do APP_SUPPORT_DIR="${APP_SUPPORT_DIR%/}"; done
case "$APP_SUPPORT_DIR" in
    */.. | */../*) stop "APP_SUPPORT_DIR must not contain .. ($APP_SUPPORT_DIR)" ;;
    "$HOME/Library/"*) ;;
    *) stop "APP_SUPPORT_DIR must be under $HOME/Library (got $APP_SUPPORT_DIR)" ;;
esac
d="$APP_SUPPORT_DIR"
while [ "$d" != "$HOME/Library" ] && [ "$d" != / ]; do
    [ ! -L "$d" ] || stop "$d is a symlink; APP_SUPPORT_DIR must be a real folder under $HOME/Library"
    d="$(dirname "$d")"
done
[ "$d" = "$HOME/Library" ] || stop "APP_SUPPORT_DIR must be a folder below $HOME/Library (got $APP_SUPPORT_DIR)"
DEST_DIR="$APP_SUPPORT_DIR"
DEST="$DEST_DIR/vm-autostart.sh"
DEST_ENV="$DEST_DIR/product.settings"
PLIST_NAME="${LAUNCH_LABEL}.plist"
PLIST="$HOME/Library/LaunchAgents/$PLIST_NAME"
DOMAIN="gui/$(id -u)"
LABEL="$DOMAIN/$LAUNCH_LABEL"
LOG_BASE="$HOME/Library/Logs/${PRODUCT}-vm-autostart"
LOG="$LOG_BASE.log"
: "${DB_VOLUME:?DB_VOLUME must be set (product.settings)}"
PGDATA_VOLUME="${COMPOSE_PROJECT}_${DB_VOLUME}"
STAMP=$(date +%Y%m%d-%H%M%S)
BACKUP_DIR="$DEST_DIR/backup-$STAMP"
export DOCKER_HOST="unix://${COLIMA_HOME}/${COLIMA_PROFILE}/docker.sock"

step "3. The agent must not already be loaded"
if launchctl print "$LABEL" >/dev/null 2>&1; then
    stop "$LAUNCH_LABEL is already loaded. Unload it deliberately first if you mean to reinstall: launchctl bootout $LABEL"
fi
echo "not loaded"

step "4. Check the copies' contents and render the plist"
bash -n "$WORK/vm-autostart.sh" || stop "vm-autostart.sh does not parse"
grep -q '^pgdata_present()' "$WORK/vm-autostart.sh" || stop "vm-autostart.sh lacks the pgdata_present guard"
grep -q '^erasure_clause()' "$WORK/vm-autostart.sh" || stop "vm-autostart.sh lacks the erasure alert"
# These go into the plist as XML text; refuse characters XML would need escaped.
for v in "$LAUNCH_LABEL" "$APP_SUPPORT_DIR" "$LOG_BASE"; do
    case "$v" in
        *[\<\>\&\"\']*) stop "LAUNCH_LABEL, APP_SUPPORT_DIR and the log path must not contain any of <>&\"' (got $v)" ;;
    esac
done
sed -e "s|__LAUNCH_LABEL__|$(esc "$LAUNCH_LABEL")|g" \
    -e "s|__SCRIPT_PATH__|$(esc "$DEST")|g" \
    -e "s|__LOG_BASE__|$(esc "$LOG_BASE")|g" \
    "$WORK/launchagent.plist.template" > "$WORK/$PLIST_NAME"
! grep -q '__[A-Z_]*__' "$WORK/$PLIST_NAME" || stop "the rendered plist still has a placeholder"
plutil -lint "$WORK/$PLIST_NAME" >/dev/null || stop "the plist is not a valid plist"
grep -qF "<string>${LAUNCH_LABEL}</string>" "$WORK/$PLIST_NAME" || stop "the plist has an unexpected label"
grep -qF "<string>$DEST</string>" "$WORK/$PLIST_NAME" || stop "the plist does not run $DEST"
echo "ok: script parses and has both fixes; plist is valid and runs $DEST"

step "5. docker must answer on the agent's socket and the database volume must exist"
volumes=$(docker volume ls --format '{{.Name}}' 2>&1) || stop "docker did not answer on $DOCKER_HOST: $volumes"
printf '%s\n' "$volumes" | grep -qx "$PGDATA_VOLUME" || stop "$PGDATA_VOLUME not found; loading the agent now could start a restore"
echo "ok: $PGDATA_VOLUME present"

step "6. Back up whatever is installed now (outside LaunchAgents)"
mkdir -p "$BACKUP_DIR"
if [ -f "$DEST" ]; then
    cp -p "$DEST" "$BACKUP_DIR/vm-autostart.sh"
    PREV_SCRIPT=1
    echo "saved the installed script"
else
    echo "no installed script to save"
fi
if [ -f "$DEST_ENV" ]; then
    cp -p "$DEST_ENV" "$BACKUP_DIR/product.settings"
    PREV_ENV=1
    echo "saved the installed product.settings"
else
    echo "no installed product.settings to save"
fi
if [ -f "$PLIST" ]; then
    cp -p "$PLIST" "$BACKUP_DIR/$PLIST_NAME"
    PREV_PLIST=1
    echo "saved the installed plist"
else
    echo "no installed plist to save"
fi
echo "backups in $BACKUP_DIR"

step "7. Install the checked copies"
SCRIPT_REPLACED=1
cp "$WORK/vm-autostart.sh" "$DEST"
cmp -s "$WORK/vm-autostart.sh" "$DEST" || stop "the installed script does not match the checked copy"
ENV_REPLACED=1
cp "$WORK/product.settings" "$DEST_ENV"
cmp -s "$WORK/product.settings" "$DEST_ENV" || stop "the installed product.settings does not match the checked copy"
PLIST_REPLACED=1
cp "$WORK/$PLIST_NAME" "$PLIST"
cmp -s "$WORK/$PLIST_NAME" "$PLIST" || stop "the installed plist does not match the checked copy"
echo "installed $DEST, $DEST_ENV and $PLIST"

step "8. Load the agent (this runs the script once, now)"
launchctl bootstrap "$DOMAIN" "$PLIST"
LOADED=1
echo "loaded"

step "9. Wait for that first run to finish (up to 5 minutes)"
finished=0
for _ in $(seq 1 60); do
    info=$(launchctl print "$LABEL" 2>/dev/null || true)
    runs=$(printf '%s\n' "$info" | sed -n 's/^[[:space:]]*runs = \([0-9][0-9]*\).*/\1/p' | head -n 1)
    exit_line=$(printf '%s\n' "$info" | grep 'last exit code' | head -n 1 || true)
    if [ "${runs:-0}" -ge 1 ] \
        && [ -n "$exit_line" ] \
        && ! printf '%s\n' "$exit_line" | grep -q 'never exited' \
        && ! printf '%s\n' "$info" | grep -q 'state = running'; then
        finished=1
        break
    fi
    sleep 5
done
[ "$finished" -eq 1 ] || echo "the first run had not finished after 5 minutes; the lines below may be from before it"
launchctl print "$LABEL" | grep -E 'state =|last exit code|runs =' || true
echo "--- last lines of $LOG"
tail -n 15 "$LOG" || true

cat <<'EOF'

Expected: "last exit code = 0" and a log ending with "running containers: ...".
If the log shows an alert instead, the agent is loaded but did nothing
destructive; read the log before the next login.

Rule while the agent is loaded: never start, stop or restart the VM by hand.
The script takes a lock; manual colima commands do not.
EOF
