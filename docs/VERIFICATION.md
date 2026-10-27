# Verification

## Baseline

Run from the repository root:

```sh
just ci
```

Without just, run the same steps explicitly:

```sh
python3 scripts/check_docs.py
git ls-files -z | bash scripts/sensitive-paths.sh
test ! -e .gitleaksignore
cd checks && python3 -m pytest -q
```

Once commits exist, also run:

```sh
gitleaks git . --redact --no-banner --ignore-gitleaks-allow --config .gitleaks.toml
```

Locally the secret scan covers committed history only: `gitleaks dir .`
would also read gitignored files such as a local `.env`. The pre-commit hook
scans each staged change, and CI scans the files of its clean checkout.

The text checker reads tracked and non-ignored new files. It checks UTF-8,
LF, final newlines, trailing whitespace, simple relative Markdown links, that
the three agent files share an identical core block, that no agent file
names another, that no text file contains hidden Unicode, and that agent
files, `.mcp.json`, agent config directories, hooks and workflows contain
no comments, long base64 blobs or symlinks. It does not validate remote links, anchors, application
behaviour, or all Markdown syntax.

Gitleaks is a required prerequisite; local verification never installs it.
CI downloads a checksum-pinned binary into the disposable runner.

Each CI run also fails while GitHub Pages is on for the repository, unless
the owner has set `PAGES_ALLOWED` in `ci.yml` to `"true"`. It reads GitHub's
API, so `just ci` has no local equivalent; check by hand with
`gh api repos/<owner>/<name> --jq .has_pages`.

## Hooks

`just setup` writes shims into the clone's own hooks directory. Each shim
runs the tracked hook under `.githooks/` and refuses when that hook is
missing, so a commit that deletes the hooks directory is still blocked. Git
resolves its default hooks directory at run time, so the shims survive a
rename and work in linked worktrees. Only when a global or system hooks path
exists does setup pin `core.hooksPath` to this clone (absolute path): re-run
`just setup` after moving such a clone, and whenever a hook stops firing.
Health check: `just setup` prints the resolved hooks directory.

- pre-commit (also on merge commits): corruption and mass-deletion guard,
  sensitive-path refusal from `scripts/sensitive-paths.sh`, Gitleaks on the
  staged change, then `just pre-commit` when defined. `just ci` runs the same
  path list over every tracked file, so CI enforces it without the hook.
- pre-push: corruption guard, Gitleaks over the full history, then `just ci`
  when the pushed commits touch anything but documentation.

Hooks are a guardrail, not an authorisation boundary: `--no-verify` exists,
and a hooks path can be unset. Required checks and restricted write access on
the remote are the enforcement. Hooks never stash or rewrite the working tree.

## Library checks

`just checks` (part of `just ci`) runs the suite under `checks/tests/`
against `deploy/`, the workflows and the repository tooling. It needs
pytest 9.1.1 and PyYAML 6.0.3 (the `dev` extra in `checks/pyproject.toml`),
and Gitleaks on the PATH for the rule tests. Locally, install them in a
virtual environment and put it first on the PATH:

```sh
python3 -m venv checks/.venv
checks/.venv/bin/pip install pytest==9.1.1 pyyaml==6.0.3
```

GitHub CI installs the same two pins, plus pytest's own dependencies at
fixed versions, with `--no-deps`, and runs the same command. New tests
land in the same change as the code they cover. Empty test discovery for
implemented code is a failure.

Rules that hold for everything here:

- Missing tools, empty test discovery, shallow clones, skipped database
  tests and cancelled runs fail; nothing is silently skipped.
- Tests use disposable, labelled data and containers, and refuse production
  names and credentials.
- Generated files are regenerated and compared, never hand-edited.
- Any file a deploy image reads from outside its build directory has an
  explicit build context, guarded by a test; a full-checkout CI cannot catch
  this class of failure.
- A backup that has never been restored is a hope: run and record a restore
  drill after any change to backup, restore or the database image (file,
  size, integrity check, row counts, cleanup, "secret never printed").

## Result record

Record date, commit or uncommitted state, command, result, and limitations.
Local checks do not prove GitHub CI, deployment, device tests, or store
approval. Re-verify checklists against a named SHA and append dated
paragraphs; keep history.

### Library verification

- 2026-10-23, uncommitted baseline: `just docs`, `paths`, `bypasses` and
  `secrets` passed locally (no commits yet, so no history scan). GitHub CI
  not run.

### Restore drills

- 2026-10-27, commit 5a832c4 (v0.1.5 candidate: `backup.sh once`, the
  `POSTGRES_IMAGE` build argument, the flock start-up check, the cycle lock
  on its own `backup-lock` volume, pinned container paths), on a
  disposable Docker VM, arm64, Docker 29.2.1, Compose 5.1.3. Run twice:
  PostgreSQL 16 with the Dockerfile's default argument
  (`postgres:16.15-alpine`,
  sha256:721873c34ceb9f8d8fc265984940dc982404c105f19ad51be9fdc5970a6080ea)
  and PostgreSQL 17 with `POSTGRES_IMAGE=postgres:17.11-alpine`
  (sha256:266d3c257594e195b309ae9d1805e54c788f1d7b375f56bf6c367aac6c917e7b),
  each against a server of the same image. Backup and restore-drill images
  built from the repository's Dockerfiles; `pg_dump` reported 16.15 and
  17.11. Backup service run with the environment and volumes of
  `deploy/compose.example.yml` (read-only rootfs, all capabilities dropped,
  scripts mounted read-only, a bind-mounted backup directory, the
  heartbeat and lock volumes, `BACKUP_ROOT`, `BACKUP_HEARTBEAT` and
  `BACKUP_LOCK_DIR` pinned). Result: pass for both majors. Key commands
  (`C` is `docker compose -p <project> -f <drill compose file>`, `R` the
  same for the scratch restore stack; 16 omits the build argument):

  ```sh
  docker build --build-arg POSTGRES_IMAGE=postgres:17.11-alpine deploy/backup
  C up -d backup
  C run --rm -T backup once
  C exec -T -d backup sh -c 'flock /lock/backup.lock sleep 20'
  C run --rm -T backup once
  docker compose -p <project> -f <same file without backup-lock> run --rm -T backup
  R up -d --build --wait
  printf X | dd of=<copy>/<dump> bs=1 seek=2000 conv=notrunc
  RESTORE_PROJECT=<scratch> RESTORE_SERVICE=postgres bash deploy/restore.sh <copy>/<dump>
  RESTORE_PROJECT=<scratch> RESTORE_SERVICE=postgres bash deploy/restore.sh <dump>
  psql -tA -c "select count(*), md5(string_agg(t::text, '|' order by id)) from <table> t"
  ```

  The builds ran through `C build backup` and `R up --build`, which pass
  the same argument from the compose files; the query ran on both servers.
  - Dataset: three tables (500, 3000 and 5000 rows) and a sequence; row
    counts and an md5 over each table's ordered rows matched exactly
    between the source and the restored database.
  - Loop mode: the first cycle wrote `<prefix>-*.sql.gz.enc` (about
    159 KB), its 65-byte `.hmac` and the heartbeat; `flock` and `openssl`
    present, `/bin/sh` is busybox ash.
  - Lock: `backup.lock` was created in the `backup-lock` volume, and no
    lock file appeared in the backup directory.
  - `once` with the loop container up exited 0 with its own dump. With the
    running loop container holding the lock for about 20 s, a
    `compose run backup once` waited, then dumped and exited 0 (21 s), so
    the two serialise through the volume. An unknown argument printed the
    usage and exited 2.
  - The service started without the `backup-lock` volume exited 1 with the
    message naming `backup-lock:/lock`, and wrote no dump and no `.part`.
  - Restore: the real `deploy/restore.sh`, unmodified, with
    `RESTORE_PROJECT` naming a scratch stack built from
    `deploy/restore-drill/Dockerfile` and its compose file plus a minimal
    init script for the backup role. A dump with one byte changed was
    refused at the HMAC check (exit 1, scratch database still empty); the
    untouched newest dump restored with exit 0.
  - Secret: the throwaway passphrase did not appear in the backup, `once`,
    refused-start, restore or refused-restore output.
  - Cleanup: every container, network, volume, image and directory the
    drill created was removed; images present before were kept.
  - Not verified: `deploy/restore-drill/run.sh` itself (it reads a live
    stack and uses a fixed project name, so restore.sh was driven directly
    in its place), a product's real init script and data, a missing or
    unwritable heartbeat folder, the nightly host mirror, off-site upload,
    retention pruning, the log archiver, and amd64. The bind-mounted
    directories lived inside the VM, not on a host disk.
