#!/bin/bash
# The restore rehearsal restore.sh's header asks for. Record each run in
# docs/restore-rehearsal.md.
#
#   PRODUCT_SETTINGS=<path>/product.settings bash <library>/deploy/restore-drill/run.sh              # newest dump
#   PRODUCT_SETTINGS=<path>/product.settings bash <library>/deploy/restore-drill/run.sh <dump.sql.gz.enc>
#
# Runs the REAL restore.sh, unmodified, with RESTORE_PROJECT=drill against a
# scratch postgres built from ./Dockerfile (production image + production init
# script), so the exact HMAC -> decrypt -> header/trailer -> psql pipeline is
# what gets proven. The scratch stack is created empty and destroyed with its
# data at the end, whatever happened.
#
# BACKUP_PASSPHRASE is honoured from the environment. When it is unset, it is
# taken from the running backup container into this process - never argv,
# never a file - and the drill's log is checked for the value afterwards; the
# run fails if it appears. The live stack is only ever READ from here (a dump
# copied out, row counts), never written to.
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
DEPLOY="$(dirname "$HERE")"
# Settings: the product's product.settings, named by PRODUCT_SETTINGS and passed on to
# restore.sh. docker-compose.yml here reads the exported values.
# shellcheck source=/dev/null
. "${PRODUCT_SETTINGS:?PRODUCT_SETTINGS must name the product.settings of the product}"
: "${PRODUCT:?}"
: "${COMPOSE_PROJECT:?}"
: "${COMPOSE_DIR:?}"
: "${DB_NAME:?}"
: "${DB_USER:?}"
: "${REPO_DIR:?}"
: "${BACKUP_PREFIX:?}"
POSTGRES_IMAGE="${POSTGRES_IMAGE:-postgres:16.15-alpine}"
: "${POSTGRES_INIT_DIR:?}"
export PRODUCT_SETTINGS POSTGRES_IMAGE DB_USER DB_NAME
BACKUP_CONTAINER="${COMPOSE_PROJECT}-backup-1"
LIVE_POSTGRES="${COMPOSE_PROJECT}-${DB_SERVICE:-postgres}-1"
COMPOSE=(docker compose -p drill -f "$HERE/docker-compose.yml")
WORK="$(mktemp -d "${TMPDIR:-/tmp}/restore-drill.XXXXXX")"
LOG="$WORK/restore.log"

cleanup() {
    "${COMPOSE[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    rm -rf "$WORK" || :
}
trap cleanup EXIT

# 1. The dump and its sidecar: a file given as $1, else the newest in the
#    backup container.
if [ $# -ge 1 ]; then
    cp "$1" "$WORK/"
    cp "$1.hmac" "$WORK/"
    name="$(basename "$1")"
else
    name="$(docker exec "$BACKUP_CONTAINER" sh -c "ls -1 /backups/${BACKUP_PREFIX}-*.sql.gz.enc | sort | tail -1 | xargs basename")"
    docker cp "${BACKUP_CONTAINER}:/backups/${name}" "$WORK/${name}"
    docker cp "${BACKUP_CONTAINER}:/backups/${name}.hmac" "$WORK/${name}.hmac"
fi
echo "dump:      ${name} ($(wc -c <"$WORK/${name}" | tr -d ' ') bytes, sidecar $(wc -c <"$WORK/${name}.hmac" | tr -d ' ') bytes)"

# 2. An empty scratch postgres under project "drill". The init scripts are
#    copied fresh into the build context (.init/, git-ignored) every run.
rm -rf "$HERE/.init"
cp -R "${REPO_DIR}/${POSTGRES_INIT_DIR}" "$HERE/.init"
"${COMPOSE[@]}" up -d --build --wait >/dev/null 2>&1
before="$("${COMPOSE[@]}" exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -tAc "select count(*) from pg_tables where schemaname='public'")"
roles="$("${COMPOSE[@]}" exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -tAc "select string_agg(rolname, ' ') from pg_roles where rolname like '${PRODUCT}%'")"
echo "scratch:   project drill, ${before} tables in public, roles: ${roles}"

# 3. The real restore.sh.
if [ -z "${BACKUP_PASSPHRASE:-}" ]; then
    BACKUP_PASSPHRASE="$(docker exec "$BACKUP_CONTAINER" printenv BACKUP_PASSPHRASE)"
    export BACKUP_PASSPHRASE
fi
start=$(date +%s)
set +e
RESTORE_PROJECT=drill RESTORE_SERVICE=postgres bash "$DEPLOY/restore.sh" "$WORK/${name}" >"$LOG" 2>&1
status=$?
set -e
echo "restore:   exit ${status} in $(( $(date +%s) - start ))s"

# 4. The log must not carry the passphrase, whichever way it arrived.
if grep -qF -- "$BACKUP_PASSPHRASE" "$LOG"; then
    echo "LEAK: the passphrase appears in the restore log; not shown" >&2
    exit 9
fi
grep -n "REFUSED\|ERROR\|complete$\|nothing is owed\|Until step 1" "$LOG" | sed 's/^/           | /' || true
[ "$status" -eq 0 ] || { echo "the restore did not complete; the log is in the lines above" >&2; exit "$status"; }

# 5. Counts only, live against the restored copy. The live database keeps
#    moving, so a small difference on a busy table is drift; a missing table
#    or a large one is not.
echo "counts (live vs restored, public tables with rows in either):"
tables="$(docker exec "$LIVE_POSTGRES" psql -U "$DB_USER" -d "$DB_NAME" -tAc "select tablename from pg_tables where schemaname='public' order by 1")"
mismatch=0; compared=0
for t in $tables; do
    live="$(docker exec "$LIVE_POSTGRES" psql -U "$DB_USER" -d "$DB_NAME" -tAc "select count(*) from \"$t\"")"
    copy="$("${COMPOSE[@]}" exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -tAc "select count(*) from \"$t\"" 2>/dev/null || echo MISSING)"
    compared=$((compared + 1))
    if [ "$live" != "$copy" ]; then
        printf '  %-28s live %-6s restored %s\n' "$t" "$live" "$copy"
        mismatch=$((mismatch + 1))
    elif [ "$live" != "0" ]; then
        printf '  %-28s %s\n' "$t" "$live"
    fi
done
after="$("${COMPOSE[@]}" exec -T postgres psql -U "$DB_USER" -d "$DB_NAME" -tAc "select count(*) from pg_tables where schemaname='public'")"
echo "after:     ${after} tables in public; ${compared} compared, ${mismatch} differ"
