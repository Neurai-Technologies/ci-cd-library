# ci-cd-library

The shared, versioned CI/CD and ops library for Neurai Technologies Private
Limited products: reusable GitHub workflows, a VM deploy loop, backup and
log-archive images, restore and restore-drill scripts, the macOS LaunchAgent
VM autostart script and its installer, and generic check tests.

## How products use it

Always by tag. Nothing changes in a product until it bumps the tag.

- Workflows: `uses: Neurai-Technologies/ci-cd-library/.github/workflows/reusable-ci.yml@vX.Y.Z`
  (likewise `reusable-promote.yml` and `reusable-worker-deploy.yml`).
- Deploy, backup and VM scripts: `git subtree add --squash` of a tag into
  the product repository, then `git subtree pull` to a newer tag.

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
