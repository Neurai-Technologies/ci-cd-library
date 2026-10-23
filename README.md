# ci-cd-library

The shared, versioned CI/CD and ops library for Neurai Technologies Private
Limited products: reusable GitHub workflows, a VM deploy loop, backup and
log-archive images, restore and restore-drill scripts, the macOS LaunchAgent
VM autostart script and its installer, and generic check tests.

## How products use it

Always by the full commit SHA of a release tag, with the tag in a
`# vX.Y.Z` comment; never by the tag alone. Nothing changes in a product
until it bumps the pin.

- Workflows: `uses: Neurai-Technologies/ci-cd-library/.github/workflows/reusable-ci.yml@<full commit SHA> # vX.Y.Z`
  (likewise `reusable-promote.yml` and `reusable-worker-deploy.yml`).
- Deploy, backup and VM scripts: fetch the release tag, check it resolves
  to the pinned SHA (`git rev-parse vX.Y.Z^{commit}`), then
  `git subtree add --prefix <dir> --squash <full SHA>`. To update, do the
  same for the new tag with
  `git subtree pull --prefix <dir> --squash <repository> <full SHA>`.
  Fetching a bare SHA from GitHub works for reachable commits (unverified
  here).

The adoption steps will live in `docs/ADOPT.md`.

## What stays in the product repository

The compose file, the Caddyfile, the LaunchAgent plist, `product.env` (every
product-specific value; the library ships only `deploy/product.env.example`)
and the product's own tests. This repository holds no product name,
hostname, path or secret.

## Checks

`just ci` (Python 3.11+, Git, Gitleaks, `just`); see
[docs/VERIFICATION.md](docs/VERIFICATION.md) and
[CONTRIBUTING.md](CONTRIBUTING.md).

## Licence

Proprietary; see [LICENSE](LICENSE). The repository is public, but that
grants no licence to use, copy or modify it.
