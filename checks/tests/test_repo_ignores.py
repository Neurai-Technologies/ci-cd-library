"""What must never be committable, asked of git rather than assumed.

The rules rot silently: a trailing slash matches a directory only, so a
pattern written `.venv/` never ignores a `.venv` symlink. So both directions
are checked by git's own matcher: every shape of secret or runtime file the
library's scripts handle is ignored, and the sources and templates it ships
are not.
"""

from __future__ import annotations

import subprocess

import pytest

from conftest import LIBRARY_ROOT

SECRETS = (
    ".env",
    ".env.local",
    ".envrc",
    ".dev.vars",
    "deploy/.env",
    "deploy/app.env",  # ENV_FILES the deployer copies
    "server.pem",
    "deploy.key",
    "cert.p12",
    "cert.pfx",
    "upload.jks",
    "release.keystore",
    "backup.sql.gz",
    "backups/fakedump-20260101-000000.sql.gz.enc",
    "snapshot.dump",
    "data.db",
    "deploy/restore-drill/.init/01-roles.sql",  # run.sh's per-run copy of the init scripts
    "tmp/scratch.sh",
    "checks/.venv",
    "checks/.pytest_cache",
)

NEEDED = (
    "deploy/product.settings.example",
    "deploy/compose.example.yml",
    "deploy/restore.sh",
    "deploy/restore-drill/Dockerfile",
    "deploy/vm/launchagent.plist.template",
    "deploy/backup/backup.sh",
    "checks/pyproject.toml",
    "checks/conftest.py",
    "checks/tests/test_repo_ignores.py",
    ".github/workflows/reusable-ci.yml",
)


def ignored(path: str) -> bool:
    """git's own answer, so this cannot drift from what a commit would do."""
    done = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", path], cwd=LIBRARY_ROOT, capture_output=True, check=False
    )
    assert done.returncode in (0, 1), done.stderr.decode()[:200]
    return done.returncode == 0


def test_a_slash_only_pattern_would_not_have_been_enough() -> None:
    """The mechanism itself, so the next reader does not restore the slash."""
    text = (LIBRARY_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "\n.venv\n" in text, ".venv needs a slashless line, not only .venv/"


@pytest.mark.parametrize("path", SECRETS)
def test_every_shape_of_secret_is_ignored(path: str) -> None:
    assert ignored(path), f"{path} could be committed; .gitignore does not cover it"


@pytest.mark.parametrize("path", NEEDED)
def test_what_the_library_ships_is_still_committable(path: str) -> None:
    assert not ignored(path), f"{path} is ignored and the library ships it"
