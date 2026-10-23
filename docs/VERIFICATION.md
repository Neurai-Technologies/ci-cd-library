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

The baseline above checks the repository itself. As code lands under
`deploy/` and `checks/`, its tests (shell syntax, Python lint and tests,
workflow and image build checks) are added to `just ci` and the CI workflow
in the same change, with the commands and required versions documented here.
Empty test discovery for implemented code is a failure.

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
