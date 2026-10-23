"""The product's post-deploy hook runs from the clone's root, and a configured hook that is not there fails the deploy.

POST_DEPLOY_HOOK names an executable in the clone, run after each compose up
with the sha. Set but missing or not executable must fail the step - a
silent skip would record a release whose own steps never ran. deploy() is
lifted from the real loop and run with every other step stubbed as a shell
function, so only the hook is real.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import LIBRARY_ROOT, ProductSettings
from tests.shell import lift

LOOP = LIBRARY_ROOT / "deploy" / "deployer" / "deploy-loop.sh"
SHA = "a" * 40
HOOK = "deploy/post-deploy.sh"


@pytest.fixture
def src(tmp_path: Path) -> Path:
    (tmp_path / "src" / "deploy").mkdir(parents=True)
    return tmp_path / "src"


def make_hook(src: Path, record: Path, mode: int = 0o755, status: int = 0, symlink: bool = False) -> None:
    """A hook that records its sha, its directory and whether it can see the
    token or the alert URL; as a symlink, it points at a real executable."""
    hook = src / HOOK
    target = hook.with_name("real-hook.sh") if symlink else hook
    target.write_text(
        "#!/usr/bin/env bash\n"
        f'printf "%s %s %s %s\\n" "$1" "$(pwd -P)" "${{GITHUB_DEPLOY_TOKEN-unset}}" '
        f'"${{DEPLOY_ALERT_URL-unset}}" > "{record}"\n'
        f"exit {status}\n"
    )
    target.chmod(mode)
    if symlink:
        hook.symlink_to(target.name)


def deploy(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], src: Path, hook: str, **overrides: str
) -> tuple[subprocess.CompletedProcess, Path]:
    env = product_settings(**overrides)
    calls = tmp_path / "docker-calls"
    # The loop's own directory is not the clone, and holds a file an expanded
    # `*` would turn into an argument.
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir(exist_ok=True)
    (elsewhere / "decoy-file").write_text("x")
    state = tmp_path / "deployed-sha"
    stubs = (
        "git_authed() { :; }; git() { :; }; sync_env() { :; }; schema_compatible() { :; }; "
        f'docker() {{ printf "%s\\n" "$*" >> {shlex.quote(str(calls))}; }}; '
        "notify_deployed() { echo notified; }; mark_site_pending() { :; }; "
        'log() { echo "[deployer] $*"; }\n'
    )
    code = (
        "set -euo pipefail\n"
        f". {shlex.quote(str(env.path))}\n"
        f"SRC={shlex.quote(str(src))}; STATE={shlex.quote(str(state))}; HOOK={shlex.quote(hook)}; "
        f'COMPOSE_DIR_ABS="$SRC/$COMPOSE_DIR"; BRANCH=main; FAILED_STEP=""\n'
        + stubs
        + lift(LOOP, "deploy")
        + f'cd {shlex.quote(str(elsewhere))}; if deploy {SHA}; then echo DEPLOYED; else echo "FAILED at $FAILED_STEP"; fi\n'
    )
    # Both exported, as in the deployer's container, so a hook would inherit them.
    run_env = {
        **os.environ,
        "GITHUB_DEPLOY_TOKEN": "fake-token",
        "DEPLOY_ALERT_URL": "https://alerts.invalid/x",
    }
    result = subprocess.run(["bash", "-c", code], capture_output=True, text=True, env=run_env, check=False)
    return result, state


def test_no_hook_configured_deploys(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], src: Path
) -> None:
    result, state = deploy(tmp_path, product_settings, src, hook="")
    assert "DEPLOYED" in result.stdout, result.stdout + result.stderr
    assert state.read_text().strip() == SHA


def test_the_hook_runs_with_the_sha_from_the_clones_root(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], src: Path
) -> None:
    record = tmp_path / "hook-ran"
    make_hook(src, record)
    result, state = deploy(tmp_path, product_settings, src, hook=HOOK)
    assert "DEPLOYED" in result.stdout, result.stdout + result.stderr
    assert record.read_text().split() == [SHA, str(src.resolve()), "unset", "unset"], (
        "the hook must run from the clone's root, without the token or the alert URL"
    )
    assert state.read_text().strip() == SHA


@pytest.mark.parametrize("shape", ["missing", "not-executable"])
def test_a_configured_hook_that_cannot_run_fails_the_deploy(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], src: Path, shape: str
) -> None:
    record = tmp_path / "hook-ran"
    if shape == "not-executable":
        make_hook(src, record, mode=0o644)
    result, state = deploy(tmp_path, product_settings, src, hook=HOOK)
    assert "FAILED at post-deploy hook" in result.stdout, result.stdout + result.stderr
    assert "missing or not executable" in result.stdout
    assert not state.exists(), "a release whose hook never ran was recorded"
    assert not record.exists()


def test_a_failing_hook_fails_the_deploy(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], src: Path
) -> None:
    make_hook(src, tmp_path / "hook-ran", status=3)
    result, state = deploy(tmp_path, product_settings, src, hook=HOOK)
    assert "FAILED at post-deploy hook" in result.stdout, result.stdout + result.stderr
    assert not state.exists()


def test_a_symlinked_hook_is_refused_like_a_missing_one(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], src: Path
) -> None:
    """A symlink could point anywhere; followed, it would run whatever it names."""
    record = tmp_path / "hook-ran"
    make_hook(src, record, symlink=True)
    result, state = deploy(tmp_path, product_settings, src, hook=HOOK)
    assert "FAILED at post-deploy hook" in result.stdout, result.stdout + result.stderr
    assert "is a symlink" in result.stdout
    assert not record.exists() and not state.exists()


def test_the_readiness_commands_are_split_without_globbing(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], src: Path
) -> None:
    """deploy() runs from a directory holding decoy-file, which an expanded `*` would become."""
    result, _ = deploy(
        tmp_path,
        product_settings,
        src,
        hook="",
        API_READY_CMD="fake-api --ready *",
        WORKER_READY_CMD="fake-worker --ready *",
    )
    assert "DEPLOYED" in result.stdout, result.stdout + result.stderr
    calls = (tmp_path / "docker-calls").read_text().splitlines()
    assert any(line.endswith("exec -T fakeapi fake-api --ready *") for line in calls), calls
    assert any(line.endswith("exec -T fakeworker fake-worker --ready *") for line in calls), calls
    assert not any("decoy-file" in line for line in calls), calls
