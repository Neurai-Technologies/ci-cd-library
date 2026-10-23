# Adopting ci-cd-library

How a product repository takes the library into use, by repository type.
Placeholders: `<full SHA>` is the 40-character commit of a release tag,
`vX.Y.Z` that tag, `<LIBRARY_DIR>` the folder the subtree lives in (the
example settings use `ci-cd-library`), `<ORIGIN_URL>` the product's own
GitHub repository.

## The one check for any pin

A pin is a full commit SHA with the tag in a comment, never the tag alone.
Before writing or bumping a pin, resolve the tag and compare:

```sh
git ls-remote https://github.com/Neurai-Technologies/ci-cd-library 'refs/tags/vX.Y.Z^{}'
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
       uses: Neurai-Technologies/ci-cd-library/.github/workflows/reusable-ci.yml@<full SHA> # vX.Y.Z
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
   - `pages-check` (default true) and `pages-allowed` (default false): the
     job fails while GitHub Pages is on. Only the owner sets
     `pages-allowed: true`, for a website that publishes built files only,
     with a decision entry.
   - `fetch-depth`: keep 0; the history scan needs every commit.
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
   git fetch https://github.com/Neurai-Technologies/ci-cd-library tag vX.Y.Z
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
   build paths if the compose file is not in `deploy/`.
5. Add the two hooks if the product needs them, as executable files at the
   paths `product.settings` names: `POST_DEPLOY_HOOK` (run after each
   successful deploy, the SHA as `$1`) and `SITE_RELEASE_HOOK` (run when the
   site serves a new SHA, `$1` that SHA, `$2` the last one it succeeded
   for). Leave either key empty to skip it.

   The deployer runs these hooks, the compose file and `product.settings`
   from its deploy-branch clone, with the docker socket and the `.env`
   secrets. Whoever can push the branch humans push controls the VM, so
   that is the branch to protect: the source branch (`ci`) under promote,
   or `main` when a product deploys `main` directly. It gets a ruleset with
   pull request and owner review required, no bypass actors, and
   CODEOWNERS covering the hooks, `product.settings`, the compose file and
   `.github/`; only owners merge. Under promote, `main` gets a ruleset that
   blocks force-push and deletion and lets only the promote token's actor
   (deploy key or fine-grained PAT) push; a PR rule would refuse promotion.
6. Add the promote caller from the header of
   `.github/workflows/reusable-promote.yml` at the pinned SHA. The tests
   workflow it waits for triggers on `push: branches: [<source-branch>]`
   only, never on tags. Keep `PROMOTE_PUSH_TOKEN` in a GitHub environment
   whose only deployment branch is `main`, name it in the `environment`
   input, and pass `push-token` by name.
7. Install the VM autostart from a Mac terminal, not from a sandboxed
   agent (it writes to `~/Library`):

   ```sh
   bash <LIBRARY_DIR>/deploy/vm/install-autostart.sh <ORIGIN_URL> <path to product.settings>
   ```

   Run it from a fresh clone of `<ORIGIN_URL>`'s main, outside any folder
   an agent can write, never from the working tree: the installer verifies
   its sibling files, not itself. At the very least, fetch first and check
   that `git diff origin/main -- <LIBRARY_DIR>` prints nothing.

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

   Run it from a fresh clone, as the installer; again after any change to
   the backup, restore, the init scripts or the database image.
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
   git subtree pull --prefix <LIBRARY_DIR> --squash https://github.com/Neurai-Technologies/ci-cd-library <full SHA>
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
