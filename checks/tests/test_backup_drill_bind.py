"""The operator drill must prove a host bind is shared before using a database."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from conftest import LIBRARY_ROOT
from tests.shell import fake


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
