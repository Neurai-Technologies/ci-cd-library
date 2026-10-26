"""tests/shell.py's git_env() keeps fixture git commands inside the fixture.

A pre-push hook exports GIT_DIR, and pytest then runs under it. Before
git_env() dropped inherited GIT_* variables, every fixture `git init`, add and
commit ran against the repository being pushed: test commits landed on its
branches and `git init` set core.bare in its config.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from tests.shell import git_env


def git(repo: Path, *args: str) -> str:
    done = subprocess.run(
        ["git", "-C", str(repo), *args], check=True, capture_output=True, text=True, env=git_env()
    )
    return done.stdout.strip()


def state(repo: Path) -> tuple[str, str, str, str]:
    """What a stray fixture command would change: HEAD, its branch, every branch and core.bare."""
    return (
        git(repo, "rev-parse", "HEAD"),
        git(repo, "symbolic-ref", "HEAD"),
        git(repo, "for-each-ref", "refs/heads"),
        git(repo, "config", "--get", "core.bare"),
    )


def test_an_inherited_git_dir_does_not_reach_the_fixture_repository(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    decoy = tmp_path / "decoy"
    decoy.mkdir()
    git(decoy, "init", "-q", "-b", "main")
    (decoy / "decoy").write_text("decoy")
    git(decoy, "add", "decoy")
    git(decoy, "commit", "-q", "-m", "decoy")
    before = state(decoy)

    # As a hook leaves them for the commands it runs.
    monkeypatch.setenv("GIT_DIR", str(decoy / ".git"))

    fixture = tmp_path / "fixture"
    fixture.mkdir()
    git(fixture, "init", "-q", "-b", "main")
    (fixture / "f").write_text("f")
    git(fixture, "add", "f")
    git(fixture, "commit", "-q", "-m", "fixture")

    assert state(decoy) == before
    assert git(fixture, "log", "--format=%s") == "fixture"
