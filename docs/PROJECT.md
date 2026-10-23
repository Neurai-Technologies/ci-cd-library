# Project brief

- Name and purpose: `ci-cd-library`, the shared, versioned CI/CD and ops
  library for Neurai Technologies Private Limited products.
- Users: the product repositories, which call its workflows by tag or pull
  it by git subtree at a tag.
- Scope: reusable GitHub workflows (ci, promote, worker-deploy), the VM
  deploy loop, backup and log-archive images, restore and restore-drill
  scripts, the macOS LaunchAgent VM autostart script and its installer,
  generic check tests. Non-goals: site tooling; anything product-specific.
- Stack: Bash, Python 3.11+ standard library, YAML (GitHub Actions),
  container images. Tool versions are recorded in
  [VERIFICATION.md](VERIFICATION.md) as code lands.
- Data: none collected or stored. Backups the code makes belong to the
  product that runs it.
- Branch and deployment policy: `main` is pull-request only; no
  deploy-on-push. A release is an immutable tag `vX.Y.Z` on `main`. Release
  owner: the Neurai Technologies Private Limited repository administrator.
- Licensing and visibility: proprietary ([LICENSE](../LICENSE)); public
  repository.
- Open: the GitHub repository does not exist yet. After creating it, apply
  the `main` ruleset once:
  `gh api -X POST repos/Neurai-Technologies/ci-cd-library/rulesets --input .github/rulesets/main.json`
  and confirm GitHub Pages is off.

Use [DECISIONS.md](DECISIONS.md) for rationale and
[DEPENDENCIES.md](DEPENDENCIES.md) for dependency records.
