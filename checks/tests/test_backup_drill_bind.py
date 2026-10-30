"""The operator drill must prove a host bind is shared before using a database."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from conftest import LIBRARY_ROOT
from tests.shell import fake, lift


def assert_report(report: Path, output: str, success: bool) -> None:
    """The saved evidence identifies the source and has one terminal result."""
    sha = subprocess.check_output(["git", "-C", str(LIBRARY_ROOT), "rev-parse", "HEAD"], text=True).strip()
    lines = report.read_text().splitlines()
    assert lines.count(f"Commit: {sha}") == 1
    markers = [line for line in lines if line in ("PASS: backup restore drill", "FAIL: backup restore drill")]
    assert markers == ["PASS: backup restore drill" if success else "FAIL: backup restore drill"]
    assert lines[-1] == markers[0]
    announcement = "".join(f"{line}\n" for line in lines if line.startswith("Docker daemon: "))
    assert output == announcement + report.read_text()


@pytest.mark.parametrize("shared", [False, True])
def test_bind_probe_precedes_database_start(tmp_path: Path, shared: bool) -> None:
    parent = tmp_path / "shared parent"
    parent.mkdir(mode=0o700)
    calls = tmp_path / "calls"
    fake(tmp_path / "bin", "docker", '''
printf '%s\n' "$*" >>"$DRILL_CALLS"
case "$1" in
    info) printf 'fake-drill-daemon\n' ;;
    image|network|volume|container) [ "$2" != inspect ] || exit 1 ;;
    compose)
        case " $* " in
            *" up "*) exit 1 ;;
            *" run "*)
                if [ "$DRILL_SHARED" = yes ]; then
                    cp "$PIPELINE_DRILL_HOST_BACKUPS/.host-marker" "$PIPELINE_DRILL_HOST_BACKUPS/.container-marker"
                    ln "$PIPELINE_DRILL_HOST_BACKUPS/.container-marker" "$PIPELINE_DRILL_HOST_BACKUPS/.container-marker-link"
                fi ;;
        esac ;;
esac
''')
    result = subprocess.run(
        ["bash", str(LIBRARY_ROOT / "checks" / "backup-restore-drill.sh"), str(tmp_path / "report"), str(parent)],
        env={**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
             "DOCKER_HOST": "unix:///fixture/docker.sock", "DRILL_CALLS": str(calls),
             "DRILL_SHARED": "yes" if shared else "no"},
        capture_output=True, text=True, check=False, timeout=20,
    )
    assert result.returncode != 0, "the fixture refuses database startup"
    if shared:
        assert "Host bind sharing and hard links verified:" in result.stdout
        assert " up " in calls.read_text()
    else:
        assert "REFUSED: host bind sharing or hard links could not be confirmed" in result.stdout
        assert " up " not in calls.read_text()
    assert parent.exists() and not list(parent.iterdir()), "only the drill's own scratch may be removed"
    assert_report(tmp_path / "report", result.stdout, False)


@pytest.mark.parametrize("mode", ["missing", "empty", "down", "nameless", "ready"])
def test_drill_requires_and_announces_its_explicit_daemon(tmp_path: Path, mode: str) -> None:
    calls = tmp_path / "daemon-calls"
    fake(tmp_path / "bin", "docker", '''
printf '%s|%s|%s\n' "${DOCKER_HOST-absent}" "${DOCKER_CONTEXT-absent}" "$*" >>"$DRILL_CALLS"
case "$1" in
    info)
        case "$DRILL_DAEMON_MODE" in
            down) exit 1 ;;
            nameless) exit 0 ;;
            *) printf 'fake-drill-daemon\n' ;;
        esac ;;
    container) exit 1 ;;
esac
''')
    run_env = {**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}",
               "DOCKER_HOST": "unix:///fixture/docker.sock", "DOCKER_CONTEXT": "refuse-inherited-context",
               "DRILL_DAEMON_MODE": mode, "DRILL_CALLS": str(calls)}
    if mode == "missing":
        run_env.pop("DOCKER_HOST")
    elif mode == "empty":
        run_env["DOCKER_HOST"] = ""
    result = subprocess.run(
        ["bash", str(LIBRARY_ROOT / "checks" / "backup-restore-drill.sh"), str(tmp_path / "report"), str(tmp_path)],
        env=run_env, capture_output=True, text=True, check=False, timeout=20,
    )
    assert result.returncode != 0, "the fixture refuses resource creation"
    if mode in ("missing", "empty"):
        assert "REFUSED: set DOCKER_HOST explicitly" in result.stdout
        assert not calls.exists(), "no Docker command may run before an endpoint is selected"
    elif mode in ("down", "nameless"):
        assert "REFUSED:" in result.stdout
        assert len(calls.read_text().splitlines()) == 2
    else:
        assert "Docker daemon: fake-drill-daemon" in result.stdout
        assert all(line.startswith("unix:///fixture/docker.sock|absent|") for line in calls.read_text().splitlines())
        assert "info --format {{.Name}}" in calls.read_text().splitlines()[1]
    assert_report(tmp_path / "report", result.stdout, False)


@pytest.mark.parametrize("mode", ["success", "failure", "cleanup_failure"])
def test_final_result_accounts_for_cleanup(tmp_path: Path, mode: str) -> None:
    report = tmp_path / "report"
    logs = tmp_path / "logs"
    logs.mkdir()
    sha = subprocess.check_output(["git", "-C", str(LIBRARY_ROOT), "rev-parse", "HEAD"], text=True).strip()
    fake(tmp_path / "bin", "docker", 'case " $* " in *" --format "*) printf wrong-owner ;; esac\n')
    code = lift(LIBRARY_ROOT / "checks" / "backup-restore-drill.sh", "cleanup") + '''
WORK="" PROJECT=fixture IMAGE=""
[ "$TEST_MODE" != cleanup_failure ] || IMAGE=fixture-image
LOG_FILE="$REPORT.pending"
exec 3>&1
exec >"$LOG_FILE" 2>&1
printf 'Commit: %s\n' "$TEST_SHA"
trap cleanup EXIT
[ "$TEST_MODE" != failure ] || exit 7
exit 0
'''
    result = subprocess.run(
        ["bash", "-c", code],
        env={**os.environ, "PATH": f"{tmp_path / 'bin'}:{os.environ['PATH']}", "TEST_MODE": mode,
             "TEST_SHA": sha, "REPORT": str(report), "LOG_WORK": str(logs)},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == (0 if mode == "success" else 7 if mode == "failure" else 1)
    assert_report(report, result.stdout, mode == "success")
