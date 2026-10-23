"""The deployer's env copy must tolerate a mount that already is the destination.

Recreating the deployer from inside its own container made compose resolve
`./.env` against the deployed clone, so the mount and the copy target were one
inode and every deploy failed on cp's "same file" refusal. The files copied
are the ones ENV_FILES names in product.settings.
"""

from __future__ import annotations

import os
import re
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import ProductSettings
from tests.shell import assignment, lift


@pytest.fixture
def loop(library_root: Path) -> Path:
    return library_root / "deploy" / "deployer" / "deploy-loop.sh"


def run_sync_env(
    loop: Path, env: ProductSettings, envdir: Path, src: Path, cwd: Path | None = None
) -> subprocess.CompletedProcess[str]:
    # The real log() writes to stdout; a renamed logger would otherwise be
    # hidden by this stub while the deploy loop fell through silently.
    assert re.search(r"^log\(\) \{", loop.read_text(encoding="utf-8"), re.MULTILINE), (
        "log() moved; update this stub"
    )
    code = (
        f". {shlex.quote(str(env.path))}\n"
        f"ENVDIR={shlex.quote(str(envdir))}\nSRC={shlex.quote(str(src))}\n"
        f"{assignment(loop, 'COMPOSE_DIR_ABS')}\n"
        'log() { echo "[deployer] $*"; }\n'
        f"{lift(loop, 'sync_env')}sync_env"
    )
    return subprocess.run(["bash", "-c", code], capture_output=True, text=True, check=False, cwd=cwd)


@pytest.fixture
def layout(tmp_path: Path) -> tuple[Path, Path]:
    envdir = tmp_path / "host-env"
    src = tmp_path / "repo"
    envdir.mkdir()
    (src / "deploy").mkdir(parents=True)
    (envdir / ".env").write_text("A=1\n")
    (envdir / "app.env").write_text("B=2\n")
    return envdir, src


def test_distinct_files_are_copied(
    loop: Path, product_settings: Callable[..., ProductSettings], layout: tuple[Path, Path]
) -> None:
    envdir, src = layout
    result = run_sync_env(loop, product_settings(ENV_FILES=".env app.env"), envdir, src)
    assert result.returncode == 0, result.stderr
    assert (src / "deploy/.env").read_text() == "A=1\n"
    assert (src / "deploy/app.env").read_text() == "B=2\n"


def test_only_the_named_files_are_copied(
    loop: Path, product_settings: Callable[..., ProductSettings], layout: tuple[Path, Path]
) -> None:
    envdir, src = layout
    result = run_sync_env(loop, product_settings(ENV_FILES=".env"), envdir, src)
    assert result.returncode == 0, result.stderr
    assert (src / "deploy/.env").exists() and not (src / "deploy/app.env").exists()


def test_self_referential_mount_is_not_a_failure(
    loop: Path, product_settings: Callable[..., ProductSettings], layout: tuple[Path, Path]
) -> None:
    envdir, src = layout
    for name in (".env", "app.env"):
        os.link(envdir / name, src / "deploy" / name)
    result = run_sync_env(loop, product_settings(ENV_FILES=".env app.env"), envdir, src)
    assert result.returncode == 0, result.stderr
    assert "same file" not in result.stderr
    assert result.stdout.count("WARNING") == 2, result.stdout
    assert (src / "deploy/.env").read_text() == "A=1\n"


def test_missing_source_still_fails(
    loop: Path, product_settings: Callable[..., ProductSettings], layout: tuple[Path, Path]
) -> None:
    envdir, src = layout
    (envdir / "app.env").unlink()
    result = run_sync_env(loop, product_settings(ENV_FILES=".env app.env"), envdir, src)
    assert result.returncode != 0


def test_a_glob_in_env_files_is_not_expanded(
    loop: Path, product_settings: Callable[..., ProductSettings], layout: tuple[Path, Path]
) -> None:
    """Run from the host-env folder, an expanded `*` would copy every file in
    it; split without globbing, it names a file `*` that is not there."""
    envdir, src = layout
    result = run_sync_env(loop, product_settings(ENV_FILES="*"), envdir, src, cwd=envdir)
    assert result.returncode != 0
    assert not (src / "deploy/app.env").exists() and not (src / "deploy/.env").exists()
