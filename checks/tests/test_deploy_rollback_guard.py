"""The deployer must refuse a commit whose migrations do not contain the schema the database is at.

Rolling back to an older sha after a migration ran would execute old code
against a newer schema. The decision itself is the candidate's own
MIGRATE_CHECK_CMD, run in MIGRATE_SERVICE (the product tests that); here the
loop must build that candidate, ask it, and refuse on any answer but a clean
exit. schema_compatible is lifted from the real deploy loop and run under
bash with a fake docker standing in for compose.
"""

from __future__ import annotations

import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import ProductSettings
from tests.shell import assignment, fake, lift, path_with


@pytest.fixture
def loop(library_root: Path) -> Path:
    return library_root / "deploy" / "deployer" / "deploy-loop.sh"


@pytest.fixture
def env(product_settings: Callable[..., ProductSettings]) -> ProductSettings:
    return product_settings()


@pytest.fixture
def clone(tmp_path: Path, env: ProductSettings) -> Path:
    """A fake checkout: the loop only needs its compose directory."""
    (tmp_path / "src" / env.values["COMPOSE_DIR"]).mkdir(parents=True)
    return tmp_path / "src"


def volume(env: ProductSettings) -> str:
    return f"{env.values['COMPOSE_PROJECT']}_{env.values['DB_VOLUME']}"


def run(
    tmp_path: Path,
    loop: Path,
    env: ProductSettings,
    clone: Path,
    check: str = "schema at head: applied",
    check_exit: int = 0,
    container: str = "abc123",
    build: str = "",
    build_exit: int = 0,
    volumes: str = "",
    volumes_exit: int = 0,
) -> subprocess.CompletedProcess:
    """A fake docker: the container listing, the volume listing, the start,
    the migrate build and the check run each answer as told, and every call
    is recorded in calls.log."""
    v = env.values
    calls = tmp_path / "calls.log"
    fake(
        tmp_path / "bin",
        "docker",
        f'printf "%s\\n" "$*" >> {shlex.quote(str(calls))}\n'
        'case " $* " in\n'
        f'    *" ps -aq {v["DB_SERVICE"]} "*) printf "%s\\n" "{container}"; exit 0 ;;\n'
        # Real docker writes its errors to stderr, so a failing listing must
        # too - otherwise dropping the 2>&1 in deploy-loop.sh looks harmless
        # here while losing the operator's only clue in production.
        f'    *" volume ls --format "*) printf "%s\\n" {shlex.quote(volumes)}'
        f"{' >&2' if volumes_exit else ''}; exit {volumes_exit} ;;\n"
        f'    *" up -d {v["DB_SERVICE"]} "*) exit 0 ;;\n'
        f'    *" build {v["MIGRATE_SERVICE"]} "*) printf "%s\\n" {shlex.quote(build)}'
        f"{' >&2' if build_exit else ''}; exit {build_exit} ;;\n"
        f'    *" run --rm -T {v["MIGRATE_SERVICE"]} {v["MIGRATE_CHECK_CMD"]} "*) printf "%s\\n" {shlex.quote(check)}'
        f"{' >&2' if check_exit else ''}; exit {check_exit} ;;\n"
        "esac\n"
        'echo "unexpected docker call: $*" >&2\nexit 99\n',
    )
    # The real loop runs under set -euo pipefail. Without it a test that
    # depended on errexit or pipefail would quietly prove something else.
    code = (
        "set -euo pipefail\n"
        f". {shlex.quote(str(env.path))}\n"
        f"SRC={shlex.quote(str(clone))}\n"
        f"{assignment(loop, 'COMPOSE_DIR_ABS')}\n"
        'log() { echo "[deployer] $*"; }\n'
        f"{lift(loop, 'schema_compatible')}schema_compatible"
    )
    return subprocess.run(
        ["bash", "-c", code], capture_output=True, text=True, env=path_with(tmp_path / "bin"), check=False
    )


def calls(tmp_path: Path) -> list[str]:
    log = tmp_path / "calls.log"
    return log.read_text().splitlines() if log.exists() else []


def test_a_commit_that_knows_the_database_version_may_deploy(
    tmp_path: Path, loop: Path, env: ProductSettings, clone: Path
) -> None:
    result = run(tmp_path, loop, env, clone)
    assert result.returncode == 0, result.stderr
    assert "schema check: schema at head: applied" in result.stdout


def test_the_check_is_the_candidates_own_build(
    tmp_path: Path, loop: Path, env: ProductSettings, clone: Path
) -> None:
    """Built from the checkout first, then run once, removed after, with no
    terminal: the running image would answer for the old commit."""
    assert run(tmp_path, loop, env, clone).returncode == 0
    made = calls(tmp_path)
    service, command = env.values["MIGRATE_SERVICE"], env.values["MIGRATE_CHECK_CMD"]
    build = next(i for i, call in enumerate(made) if f" build {service}" in call)
    check = next(i for i, call in enumerate(made) if f" run --rm -T {service} {command}" in call)
    assert build < check
    project_dir = f"--project-directory {clone}/{env.values['COMPOSE_DIR']}"
    assert all(
        project_dir in made[i] and f"-p {env.values['COMPOSE_PROJECT']} " in made[i] for i in (build, check)
    )


def test_a_commit_older_than_the_database_schema_is_refused(
    tmp_path: Path, loop: Path, env: ProductSettings, clone: Path
) -> None:
    refusal = "REFUSED: the database schema is at migration 2, which this build's migrations do not contain"
    result = run(tmp_path, loop, env, clone, check=refusal, check_exit=1)
    assert result.returncode == 1
    assert "REFUSED" in result.stdout and "migration 2" in result.stdout and "restore" in result.stdout


def test_no_container_and_no_volume_passes_with_a_log_line(
    tmp_path: Path, loop: Path, env: ProductSettings, clone: Path
) -> None:
    result = run(tmp_path, loop, env, clone, container="")
    expected = (
        f"no {env.values['DB_SERVICE']} container and no {env.values['DB_VOLUME']} volume, nothing to compare"
    )
    assert result.returncode == 0 and expected in result.stdout, result.stdout


def test_a_volume_without_a_container_refuses(
    tmp_path: Path, loop: Path, env: ProductSettings, clone: Path
) -> None:
    """A named volume outlives its container, and the volume is where the data
    is. Reading "no container" as "fresh VM" would attach a new container -
    carrying the candidate commit's migrations - to the old database.

    `docker volume ls` is daemon-wide, so the database volume sits in the
    middle of other projects' names here: a guard that read only the first or
    last line would pass a tidier fixture and never fire in production.
    """
    result = run(
        tmp_path,
        loop,
        env,
        clone,
        container="",
        volumes=f"1b05de38b44e\nother_cache\n{volume(env)}\nfbc33da17491",
    )
    assert result.returncode == 1
    assert f"{volume(env)} exists" in result.stdout


@pytest.mark.parametrize("shape", ["{}_backup", "x{}"])
def test_a_similarly_named_volume_does_not_answer_for_it(
    tmp_path: Path, loop: Path, env: ProductSettings, clone: Path, shape: str
) -> None:
    """Matched whole-line, both ends: a trailing anchor alone lets x<volume>
    match, and a leading anchor alone lets <volume>_backup match."""
    result = run(tmp_path, loop, env, clone, container="", volumes=shape.format(volume(env)))
    assert result.returncode == 0 and "volume, nothing to compare" in result.stdout, result.stdout


def test_a_failed_volume_read_refuses_instead_of_proceeding(
    tmp_path: Path, loop: Path, env: ProductSettings, clone: Path
) -> None:
    """Piped straight into an if, a docker that cannot answer produces no
    output, grep matches nothing and the branch falls through to proceed -
    a broken docker reading exactly like a machine with no volume."""
    result = run(tmp_path, loop, env, clone, container="", volumes="cannot connect", volumes_exit=2)
    assert result.returncode == 1
    assert "REFUSED: could not list volumes" in result.stdout
    # The docker error itself, not just the refusal: an operator reading the
    # log needs to know WHY it could not answer.
    assert "cannot connect" in result.stdout


def test_the_volume_is_not_consulted_when_a_container_exists(
    tmp_path: Path, loop: Path, env: ProductSettings, clone: Path
) -> None:
    """The volume question only arises when there is no container."""
    result = run(tmp_path, loop, env, clone, volumes=volume(env))
    assert result.returncode == 0
    assert f"{volume(env)} exists" not in result.stdout


def test_a_failed_read_refuses_instead_of_skipping(
    tmp_path: Path, loop: Path, env: ProductSettings, clone: Path
) -> None:
    """A check that cannot reach the database refuses, with its reason."""
    result = run(tmp_path, loop, env, clone, check="migrate failed: connection refused", check_exit=1)
    assert result.returncode == 1
    assert "REFUSED" in result.stdout and "connection refused" in result.stdout
    crashed = run(tmp_path, loop, env, clone, check="", check_exit=137)
    assert crashed.returncode == 1 and "REFUSED: no output" in crashed.stdout


def test_a_migrate_image_that_will_not_build_refuses(
    tmp_path: Path, loop: Path, env: ProductSettings, clone: Path
) -> None:
    result = run(tmp_path, loop, env, clone, build="failed to solve: build step exited 101", build_exit=1)
    assert result.returncode == 1
    assert "REFUSED: the migrate image would not build" in result.stdout and "exited 101" in result.stdout
    assert not [call for call in calls(tmp_path) if " run " in f" {call} "], "no check without the candidate"


def test_the_deploy_step_runs_the_check_before_compose_up(loop: Path) -> None:
    text = loop.read_text(encoding="utf-8")
    assert text.index('FAILED_STEP="schema check"') < text.index('FAILED_STEP="compose up"')
    assert "schema_compatible || return 1" in text
