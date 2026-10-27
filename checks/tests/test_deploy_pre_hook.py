"""The product's pre-deploy hook runs before anything is replaced, and its failure fails the deploy.

PRE_DEPLOY_HOOK names an executable in the clone (a database backup, say),
run after the schema check and before the log archive and compose up. A
failure must leave the running stack alone: no compose up, no recorded sha,
one alert naming the step. deploy() is lifted from the real loop and run with
every other step stubbed as a shell function, so only the hook is real.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import LIBRARY_ROOT, ProductSettings
from tests.shell import lift
from tests.test_deploy_hardening import block

LOOP = LIBRARY_ROOT / "deploy" / "deployer" / "deploy-loop.sh"
SHA = "b" * 40
HOOK = "deploy/pre-deploy.sh"


@pytest.fixture
def src(tmp_path: Path) -> Path:
    (tmp_path / "src" / "deploy").mkdir(parents=True)
    return tmp_path / "src"


def make_hook(src: Path, calls: Path, status: int = 0) -> None:
    """A hook that writes its sha into the same call log docker writes to."""
    hook = src / HOOK
    hook.write_text(f'#!/usr/bin/env bash\nprintf "pre-hook %s\\n" "$1" >> "{calls}"\nexit {status}\n')
    hook.chmod(0o755)


def deploy(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], src: Path, hook: str
) -> tuple[subprocess.CompletedProcess, Path, Path]:
    env = product_settings()
    calls = tmp_path / "calls"
    calls.touch()
    state = tmp_path / "deployed-sha"
    alerted = tmp_path / "alerted"
    stubs = (
        "git_authed() { :; }; git() { :; }; sync_env() { :; }; schema_compatible() { :; }; "
        f'docker() {{ printf "docker %s\\n" "$*" >> {shlex.quote(str(calls))}; }}; '
        "notify_deployed() { :; }; mark_site_pending() { :; }; "
        'log() { echo "[deployer] $*"; }; alert() { echo "[alert] $*"; }\n'
    )
    code = (
        "set -euo pipefail\n"
        f". {shlex.quote(str(env.path))}\n"
        f"SRC={shlex.quote(str(src))}; STATE={shlex.quote(str(state))}; HOOK=''; "
        f"PRE_HOOK={shlex.quote(hook)}; ALERTED={shlex.quote(str(alerted))}; POLL=90; "
        f'COMPOSE_DIR_ABS="$SRC/$COMPOSE_DIR"; BRANCH=main; FAILED_STEP=""\n'
        + stubs
        + lift(LOOP, "deploy")
        + lift(LOOP, "notify_failure")
        + f'if deploy {SHA}; then echo DEPLOYED; else echo "FAILED at $FAILED_STEP"; '
        f"notify_failure {SHA}; notify_failure {SHA}; fi\n"
    )
    result = subprocess.run(["bash", "-c", code], capture_output=True, text=True, check=False)
    return result, state, calls


def test_the_hook_runs_before_the_log_archive_and_compose_up(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], src: Path
) -> None:
    calls_path = tmp_path / "calls"
    make_hook(src, calls_path)
    result, state, calls = deploy(tmp_path, product_settings, src, hook=HOOK)
    assert "DEPLOYED" in result.stdout, result.stdout + result.stderr
    lines = calls.read_text().splitlines()
    assert lines[0] == f"pre-hook {SHA}", lines
    assert any(" up -d --build" in line for line in lines[1:]), lines
    assert state.read_text().strip() == SHA


def test_a_failing_hook_stops_the_deploy_before_anything_is_replaced(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], src: Path
) -> None:
    make_hook(src, tmp_path / "calls", status=3)
    result, state, calls = deploy(tmp_path, product_settings, src, hook=HOOK)
    assert "FAILED at pre-deploy hook" in result.stdout, result.stdout + result.stderr
    assert not any(line.startswith("docker") for line in calls.read_text().splitlines())
    assert not state.exists()
    alerts = [line for line in result.stdout.splitlines() if line.startswith("[alert]")]
    assert len(alerts) == 1 and "FAILED at pre-deploy hook" in alerts[0], alerts


def test_a_configured_hook_that_is_missing_fails_the_deploy(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], src: Path
) -> None:
    result, state, calls = deploy(tmp_path, product_settings, src, hook=HOOK)
    assert "FAILED at pre-deploy hook" in result.stdout, result.stdout + result.stderr
    assert "missing or not executable" in result.stdout
    assert calls.read_text() == "" and not state.exists()


def test_no_hook_configured_skips_it(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], src: Path
) -> None:
    result, _, calls = deploy(tmp_path, product_settings, src, hook="")
    assert "DEPLOYED" in result.stdout, result.stdout + result.stderr
    assert "pre-deploy hook" not in result.stdout
    assert not any(line.startswith("pre-hook") for line in calls.read_text().splitlines())


def path_check(hook: str) -> subprocess.CompletedProcess:
    code = (
        "set -euo pipefail\n"
        f"PRE_HOOK={shlex.quote(hook)}\n"
        'log() { echo "[deployer] $*"; }\n'
        + lift(LOOP, "hook_path_ok")
        + block(LOOP, 'if [ -n "$PRE_HOOK" ] && ! hook_path_ok', "fi", count=1, ceiling=5)
        + 'echo "STARTED"\n'
    )
    return subprocess.run(["bash", "-c", code], capture_output=True, text=True, check=False)


@pytest.mark.parametrize("hook", [HOOK, ""])
def test_a_pre_hook_inside_the_clone_is_accepted(hook: str) -> None:
    result = path_check(hook)
    assert result.returncode == 0 and "STARTED" in result.stdout, result.stdout + result.stderr


@pytest.mark.parametrize("hook", ["/usr/local/bin/hook.sh", "../outside.sh", "deploy/../../outside.sh"])
def test_a_pre_hook_outside_the_clone_stops_the_loop(hook: str) -> None:
    result = path_check(hook)
    assert result.returncode == 1 and "STARTED" not in result.stdout
    assert "PRE_DEPLOY_HOOK must be a path inside the repository" in result.stdout
