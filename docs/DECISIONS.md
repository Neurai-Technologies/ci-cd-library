# Decision log

Append-only. Record date, status (proposed, accepted, superseded), decision,
rationale, consequences, and the evidence or owner approving it. Mark a
superseded entry; never delete it. A proposal is not silently turned into an
accepted decision. Agents do not relitigate accepted entries; they propose a
new one.

| Date | Status | Decision | Reason / consequence |
| --- | --- | --- | --- |
| 2026-09-29 | Accepted | Keep the base stack-neutral and free of product code | Projects select their own stack and real application checks |
| 2026-09-29 | Superseded 2026-09-29 | Keep one canonical AGENTS.md that CLAUDE.md and GEMINI.md point to | Replaced: each agent reads only its own file |
| 2026-09-29 | Accepted | Three self-contained agent files; CLAUDE.md is the source of truth; `just sync-agents` copies the shared block and `just ci` fails on drift | Pointer files left Codex and Gemini with stale or missing rules in owned repos; no agent reads another agent's file |
| 2026-09-29 | Accepted | No secret-scan allowlist by default | A new project has no demonstrated false positives; an allowlist nobody reads is worse than none |
| 2026-09-29 | Accepted | Repository-local hooks under `.githooks/`, installed by `just setup`, with the corruption and mass-deletion guard | Machine-global hooks silently died in eleven repositories; a fresh clone elsewhere had no gate at all |
| 2026-09-29 | Accepted | Proprietary LICENSE by default; permissive-only dependency allowlist | Matches the shipped products; source-available or MIT is a deliberate per-project choice |
| 2026-09-29 | Superseded 2026-09-30 | Push is part of delivery unless docs/PROJECT.md says otherwise; never force | Owner rule since 2026-09-07; deploy-on-push repositories must document the trigger |
| 2026-09-29 | Accepted | The OS sandbox in `.claude/settings.json` is the control for secret-file reads and for writes to the gate files; permission deny rules feed it | A `Bash` deny does not stop `bash -c`; only the sandbox fails the access at the syscall |
| 2026-09-29 | Accepted | Lockfiles are the dependency inventory; the register holds vendored assets and binary SDKs only; notices are generated per stack | A hand-kept register drifts from the lockfile it duplicates |
| 2026-09-29 | Accepted | No paid GitHub scanner is assumed on private repositories; the ruleset JSON is applied where the plan allows | Secret protection, CodeQL, dependency review and attestations all need a paid plan on private repos |
| 2026-09-30 | Accepted | `main` is pull-request only with the pinned CI check; the ruleset in `.github/rulesets/main.json` is to be applied once the repo is on GitHub | Rulesets are not inherited; once applied, direct pushes to main are refused |
| 2026-09-29 | Accepted | One incident runbook with dated breach clocks, one data inventory table | Obligations with a clock are executable only when written down with the date |
| 2026-09-30 | Accepted | Push is part of delivery, with no per-project opt-out (an explicit "don't push" from the user in that turn still stands); with a remote that means branch, push, PR, merge on green, never straight to `main`; never force | Owner decision; a deploy-on-push branch is still named in docs/PROJECT.md so the pusher watches the live result |
| 2026-10-01 | Accepted | GitHub Pages stays off unless the repository is a website publishing built files only; CI fails while it is on unless the owner sets `PAGES_ALLOWED` in `ci.yml` | A private repository's Pages site is public short of Enterprise Cloud, and a branch-root source serves every committed file; in an owned repository it stayed on for weeks after deleting `CNAME` was taken for turning it off |
| 2026-10-01 | Accepted | CI secrets (deploy tokens, API keys) live in a GitHub environment whose deployment branches are `main` only, never in repository or organisation secrets; without Pro (user-owned) or Team or Enterprise (organisation-owned) a private repository deploys from the operator's machine, from a clean checkout of merged `main` | Repository secrets have no branch restriction, so a token that can push a workflow to any branch could read them without a pull request; this came up in an owned repository whose deploy read them as repository secrets |
| 2026-10-23 | Accepted | `ci-cd-library` is a public repository under the GitHub organisation Neurai-Technologies | Owner decision; every file is world-readable, so no product name, hostname, path or secret ever enters it |
| 2026-10-23 | Accepted | Proprietary LICENSE (Neurai Technologies Private Limited); public visibility grants no licence to use, copy or modify | Owner decision; the LICENSE says so in plain words |
| 2026-10-23 | Accepted | Scope is pipelines, deploy and backups; site tooling is excluded | Owner decision; site tooling stays in the site repositories |
| 2026-10-23 | Accepted | Consumers pin a release by its full commit SHA (see the 2026-10-23 SHA-pin row), for `uses:` and git subtree alike; nothing changes in a product until it bumps | A library change never reaches a product unannounced; tags are immutable |
| 2026-10-23 | Accepted | Products supply every product-specific value through a `product.env` file kept in the product repository; `deploy/product.env.example` lists them | Keeps the library free of product values |
| 2026-10-23 | Accepted | Built locally first; the GitHub repository is created afterwards | Owner decision; until then the CI run, the ruleset and the Pages check are unverified |
| 2026-10-23 | Accepted | Exception to the no-identifiers rule: the rights holder's contact address may appear in LICENSE and SECURITY.md | A licence and a reporting channel need a contact; product hostnames, paths and names remain banned everywhere |
| 2026-10-23 | Accepted | Consumers pin the full commit SHA of a release tag with a `# vX.Y.Z` comment, never the tag alone | A tag is a movable name; a SHA pin holds even if a tag were moved, and the tag ruleset makes `v*` immutable on GitHub as well |
