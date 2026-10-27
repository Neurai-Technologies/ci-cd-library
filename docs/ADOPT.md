# Adopting production-pipeline

How a product repository takes the library into use, by repository type.
Placeholders: `<full SHA>` is the 40-character commit of a release tag,
`vX.Y.Z` that tag, `<LIBRARY_DIR>` the folder the subtree lives in (your choice: use the
same value in `--prefix`, in `LIBRARY_DIR` and in the compose paths; the
examples use `ci-cd-library`), `<ORIGIN_URL>` the product's own
GitHub repository.

## The one check for any pin

A pin is a full commit SHA with the tag in a comment, never the tag alone.
Before writing or bumping a pin, resolve the tag and compare:

```sh
git ls-remote https://github.com/Neurai-Technologies/production-pipeline 'refs/tags/vX.Y.Z^{}'
```

The SHA it prints must equal the SHA you pin. If the line is empty or the
SHA differs, stop: the tag does not exist or is not the release you meant.

## a. Every repository: CI

1. Add one caller workflow, for example `.github/workflows/ci.yml`:

   ```yaml
   name: ci
   on:
     push:
       branches: [main]
     pull_request:
   permissions:
     contents: read
   jobs:
     ci:
       uses: Neurai-Technologies/production-pipeline/.github/workflows/reusable-ci.yml@<full SHA> # vX.Y.Z
       with:
         test-command: just ci
   ```

2. Set the inputs the repository needs; every one has a default:
   - `test-command`: run from the repository root after the baseline
     checks (`just ci`, `pnpm test`). Empty runs only the baseline.
   - `require-repo-scripts`: set `false` when the repository has no
     `scripts/check_docs.py` and `scripts/sensitive-paths.sh`; otherwise
     the job fails on the missing files.
   - `python-version`, `node-version`: set only when the test command needs
     them (for example `"3.12"`, `"22"`); empty skips the setup step.
     `node-cache` (for example `npm`) has setup-node restore that package
     manager's cache; it needs `node-version` set and that package
     manager's lockfile in the repository; empty caches nothing.
   - `rust-toolchain`: an exact Rust version `X.Y.Z` (for example
     `"1.85.0"`) installed with rustup, with rustfmt and clippy, and made
     the default; any other value is refused; empty skips it.
     `cargo-dir`: the folder holding `Cargo.lock` and `target/`; it caches
     `~/.cargo` and that `target/` under a key on the toolchain and that
     `Cargo.lock`; empty skips the cache. A restored cache is trusted
     input: leave `cargo-dir` and `node-cache` empty in a repository where
     any workflow runs untrusted input on the default branch
     (`pull_request_target`, `issue_comment` and the like), because such a
     run can write the default branch's cache entries.
   - `postgres-image`, `redis-image`: service containers on `localhost:5432`
     and `localhost:6379`, health-checked before the steps run; give a full
     version tag. Empty (the default) starts none. `postgres-user`,
     `postgres-password` and `postgres-db` (each `test` by default) set the
     Postgres service's user, password and database; they are a disposable
     test credential, never a real one. The test command always gets them,
     whether or not a Postgres service runs, as `CI_POSTGRES_USER`,
     `CI_POSTGRES_PASSWORD` and `CI_POSTGRES_DB` (never
     `PG*`, which every Postgres tool reads); build the connection URL from
     them inside `test-command`, so the password is never written in the
     caller's workflow (the secret scan's URI rule flags a password of six
     or more characters there).
   - `pages-check` (default true) and `pages-allowed` (default false): the
     job fails while GitHub Pages is on. Only the owner sets
     `pages-allowed: true`, for a website that publishes built files only,
     with a decision entry.
   - `fetch-depth`: keep 0; the history scan needs every commit.
   - `history-since`: leave empty (the default), which scans the whole
     history. Set it only when the repository's history predates its
     adoption of the library and holds findings it cannot remove without
     rewriting history, such as test-only false positives in old commits.
     Name the full SHA of the repository's last commit before it adopted
     the library CI, so the adoption commit itself is scanned: the history
     scan then covers the commits of every fetched branch and tag except
     that one and its ancestors. Write it as a literal SHA, never an expression such as
     `github.event.before` or a pull request's base, and never move it
     forward: changing it is a gate change that gets its own decision
     record. A value that is not a full lowercase SHA, or not an ancestor
     of HEAD, or a shallow checkout, fails the job, so it needs
     `fetch-depth` 0. The current-files scan
     is never narrowed, and the product's own scanner must keep scanning
     the full history: it is the only scan of the commits before that
     point. Like the whole-history default, the narrowed scan covers other
     branches and tags too, not only HEAD's own ancestry.
   - `timeout-minutes` (default 15) and `concurrency-key` (only when one
     caller workflow calls this twice).
3. Run the pin check above, push a branch, and confirm the run passes in
   its pull request.

## b. Cloudflare Worker repositories

1. Create a GitHub environment (default name `production`) whose only
   deployment branch is the deploy branch (`main`). Put
   `CLOUDFLARE_API_TOKEN` and `CLOUDFLARE_ACCOUNT_ID` there, never in
   repository or organisation secrets, and delete any repository secret of
   the same name.
2. The CI workflow from section a must trigger on
   `push: branches: [<deploy-branch>]` only for pushes, never on tags: a
   tag named like the deploy branch would otherwise pass the head_branch
   check. Pull-request runs are filtered out by the `event == 'push'` guard
   below.
3. Add the deploy caller, `.github/workflows/deploy.yml`, copied from the
   header of `.github/workflows/reusable-worker-deploy.yml` at the pinned
   SHA: `on: workflow_run` of `ci`, the four-part `if:` guard (success,
   push event, deploy branch, same repository), `ref` set to the run's
   `head_sha`, and both secrets passed by name (never `secrets: inherit`).
4. Set `deploy-branch` when it is not `main`, and `working-directory`,
   `wrangler-config`, `build-command` or `environment` when the defaults do
   not fit.
5. Merge one change to the deploy branch and confirm the Worker reports
   the merged SHA.

## c. VM products (deployer, backups, autostart)

1. Add the library as a subtree, from the product's root:

   ```sh
   git fetch https://github.com/Neurai-Technologies/production-pipeline tag vX.Y.Z
   git rev-parse 'vX.Y.Z^{commit}'
   git subtree add --prefix <LIBRARY_DIR> --squash <full SHA>
   ```

   The second command must print the pinned SHA. Fetching a bare SHA from
   GitHub works for reachable commits (unverified here).
2. Copy `<LIBRARY_DIR>/deploy/product.settings.example` to
   `product.settings` next to the product's compose file and fill in every
   key. It holds no secrets; commit it. Secrets (database passwords,
   `BACKUP_PASSPHRASE`, `GITHUB_DEPLOY_TOKEN`, the R2 keys) stay in `.env`,
   which is never committed.
3. Know what compose reads from where. `env_file: product.settings` sets
   the containers' environment only; compose interpolates `${...}` in the
   compose file from the project's `.env`. So `.env` must set
   `BACKUP_DIR`, for the backup and log-archive mounts, to the same value
   as in `product.settings`: an absolute path outside any tree an agent
   can write. Without it compose falls back to `./backups` inside the
   checkout. The deployer's `SRC_DIR` mount is written out
   in the compose file (or interpolated from `.env`) and must equal
   `SRC_DIR` in `product.settings`, the same path on both sides.
4. Merge the services of `<LIBRARY_DIR>/deploy/compose.example.yml` into
   the product's compose file: `backup`, `logarchive`, `deployer`, the
   `x-log-bounds` anchor, the `backup-heartbeat` volume and the
   `logarchive` network. Keep the `depends_on` name equal to `DB_SERVICE`
   and the `/host-env` mounts equal to `ENV_FILES`. Adjust the relative
   build paths if the compose file is not in `deploy/`. Set the
   `POSTGRES_IMAGE` build arg of `backup` and `logarchive` to the same
   value as `POSTGRES_IMAGE` in `product.settings`: an exact version tag
   (`postgres:<major>.<minor>-alpine`, optionally followed by an
   `@sha256:` digest for a stronger pin) whose major equals the product's
   database server major, because pg_dump refuses a newer server. The
   library's default is the 16 line; a product on 17 sets a 17 tag in both
   places.
5. Add the hooks if the product needs them, as executable files at the
   paths `product.settings` names: `PRE_DEPLOY_HOOK` (run before anything is
   replaced, such as a database backup, the SHA as `$1`; its failure fails
   the deploy), `POST_DEPLOY_HOOK` (run after each
   successful deploy, the SHA as `$1`) and `SITE_RELEASE_HOOK` (run when the
   site serves a new SHA, `$1` that SHA, `$2` the last one it succeeded
   for). Leave any key empty to skip it. The site is watched through
   `SITE_URL`, its JSON release marker (`{"sha": "<40-hex>"}`): after each
   deploy the deployer waits for the marker to show that SHA and alerts
   when it has not within `DEPLOY_SITE_GRACE_SECONDS`. `SITE_URL` is
   optional; empty means no site check, no site alerts, and the grace
   period is ignored. `SITE_RELEASE_HOOK` needs `SITE_URL`: the deployer
   refuses to start with the hook set and the URL empty.
   `PRE_DEPLOY_HOOK` runs again on
   every retry of a failed deploy, so a backup hook must be idempotent and
   keep its own retention. A database backup hook can run the backup
   service for one cycle,
   `docker compose -p <project> --project-directory <dir> run --rm -T backup once`,
   so the pre-deploy dump gets the same encryption, naming and retention as
   the scheduled ones; it exits non-zero when the dump fails, or when
   another backup cycle holds the lock for about 5 minutes. The deployer
   stops the hook after 900 seconds, so that wait plus one dump must fit
   inside it: check the dump time against the database's size. Stopping
   the hook may not stop the container `run` started, which then keeps
   the lock and makes the retry wait; give it a fixed `--name` and
   remove it with `docker rm -f <name>` in a trap on exit. Each run
   adds a full dump kept for `BACKUP_KEEP_DAYS`, so frequent deploys grow
   the backup directory: size it for the deploys in that window as well as
   the scheduled dumps. If `APP_PATHS` (the optional include list in
   `product.settings`: only changes under those paths redeploy the stack)
   is set, list the compose file's directory, `product.settings` and the
   hook paths in it too, or a change to them alone is not deployed. A
   pathspec in it that matches no file at the commit under consideration
   fails the attempt with an alert; it is never counted as no app change.

   The deployer runs these hooks, the compose file and `product.settings`
   from its deploy-branch clone, with the docker socket and the `.env`
   secrets. Whoever can push the branch humans push controls the VM, so
   that is the branch to protect: the source branch (`ci`) under promote,
   or `main` when a product deploys `main` directly. It gets a ruleset with
   pull request and owner review required, no bypass actors, and
   CODEOWNERS covering the hooks, `product.settings`, the compose file and
   `.github/`; only owners merge. Under promote, `main` gets a ruleset that
   blocks force-push and deletion and requires the tests workflow's status
   check; on the token path it also restricts updates to the promote
   token's actor (fine-grained PAT or GitHub App token). A PR rule would
   block either path.
6. Add the promote caller from the header of
   `.github/workflows/reusable-promote.yml` at the pinned SHA. The tests
   workflow it waits for triggers on `push: branches: [<source-branch>]`
   only, never on tags. Keep `PROMOTE_PUSH_TOKEN` in a GitHub environment
   whose only deployment branch is `main`, name it in the `environment`
   input, and pass `push-token` by name.

   Or push with the caller's own `GITHUB_TOKEN`: set
   `github-token-push: true`, pass no secret, and grant the calling job
   exactly `contents: write` and `actions: read`; the `main` ruleset must
   then let GitHub Actions push it. That lets every workflow run in the
   repository with `contents: write` update `main`, not only this job, so
   on this path everyone with write access controls `main`, limited only by
   the required status check: use it only where the people with write
   access are exactly the people allowed to push the source branch, and
   use the token path otherwise. A `GITHUB_TOKEN` push does not trigger the
   push workflows on `main`, which is why a caller may prefer it: nothing
   re-runs there, and a deployer that gates on the source-branch runs of
   the same sha is unaffected. A fast-forward to commits already on the
   source branch has been seen to succeed with this token when those
   commits change files under `.github/workflows/`.

   ```yaml
   jobs:
     promote:
       permissions:
         contents: write
         actions: read
       uses: Neurai-Technologies/production-pipeline/.github/workflows/reusable-promote.yml@<full SHA> # vX.Y.Z
       with:
         tests-workflow: .github/workflows/tests.yml
         github-token-push: true
   ```
7. Install the VM autostart from a Mac terminal, not from a sandboxed
   agent (it writes to `~/Library`):

   ```sh
   bash <LIBRARY_DIR>/deploy/vm/install-autostart.sh <ORIGIN_URL> <path to product.settings>
   ```

   Run it from the checkout at `REPO_DIR`: the installer refuses any other
   checkout, and the login job runs restore.sh and compose from there.
   Keep that checkout outside agent-writable folders where possible. The
   installer verifies its sibling files, not itself, so first fetch and
   confirm `git diff origin/main --stat -- <LIBRARY_DIR>` prints nothing.
   It checks its files against a fresh clone of `<ORIGIN_URL>`'s main,
   then loads the LaunchAgent, which runs at once. `APP_SUPPORT_DIR` must
   be under `~/Library`. Before running it:
   - `product.settings` is committed and on `<ORIGIN_URL>`'s main: the
     installer compares it with a fresh clone and refuses a difference.
   - The stack has been started once, so the database volume
     `${COMPOSE_PROJECT}_${DB_VOLUME}` exists.
   - `flock`, `docker`, `colima` and `git` are on the PATH.
   - The agent is not already loaded.
8. Run the restore drill and record the result in the product's
   `docs/restore-rehearsal.md`:

   ```sh
   PRODUCT_SETTINGS=<path>/product.settings bash <LIBRARY_DIR>/deploy/restore-drill/run.sh
   ```

   Run it from the checkout at `REPO_DIR` after the same `git diff` check;
   again after any change to backup, restore, init scripts or the image.
9. Run the library's checks with pytest and PyYAML (the `dev` extra in
   `<LIBRARY_DIR>/checks/pyproject.toml`):

   ```sh
   cd <LIBRARY_DIR>/checks && python -m pytest
   ```

   The identifier check needs the product's own names, supplied through
   the `BANNED_WORDS` environment variable (comma-separated) or a
   `checks/banned-words.local` file inside the subtree (one per line;
   gitignored). Without either it checks only Mac paths and hostnames,
   and warns.

## d. Updating

1. Pick the new tag and run the pin check for it.
2. Replace the SHA and the `# vX.Y.Z` comment in every `uses:` line.
3. For a subtree, fetch the new tag, check the SHA as in c.1, then:

   ```sh
   git subtree pull --prefix <LIBRARY_DIR> --squash https://github.com/Neurai-Technologies/production-pipeline <full SHA>
   ```

4. Compare `deploy/product.settings.example` with your `product.settings`
   and add any new key.
5. Run the product's checks and the library checks, then release the
   product through its normal path. Nothing changes in a product until it
   does this.

## e. What stays in the product repository

- The compose file, the Caddyfile and `product.settings`. The installer
  renders the LaunchAgent plist onto the Mac from the library's template.
- `.env` and every secret, outside Git.
- The product's own tests, its caller workflows, its hooks and its restore
  rehearsal record.
