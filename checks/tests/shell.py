"""Helpers for running slices of the library's shell scripts under fake binaries.

A slice is lifted out of the real script rather than retyped, so a test can
only pass on the code that ships. The scripts are never run whole here when
they would drive docker, colima or launchctl.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

DEFINITION = re.compile(r"^\w+\(\) \{")


def lift(script: Path, name: str) -> str:
    """One function of a script, from its `name() {` line to the next column-zero brace.

    An unbounded end is the hazard: a closing brace indented by one space
    extends the slice into the next function, and that text is then run. Both
    runaway shapes swallow a second definition, so counting them bounds it.
    """
    lines = script.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(f"{name}() {{")]
    assert len(starts) == 1, f"{script.name}: expected one {name}(), found {len(starts)}"
    end = next((i for i in range(starts[0] + 1, len(lines)) if lines[i].startswith("}")), None)
    assert end is not None, f"{script.name}: {name}() never closes"
    body = lines[starts[0] : end + 1]
    found = [line for line in body if DEFINITION.match(line)]
    assert len(found) == 1, f"the slice ran past {name}: it lifted {found}"
    return "\n".join(body) + "\n"


def assignment(script: Path, name: str) -> str:
    """The script's one column-zero `NAME=` line, so a test derives a value as the script does."""
    lines = script.read_text(encoding="utf-8").splitlines()
    found = [line for line in lines if line.startswith(f"{name}=")]
    assert len(found) == 1, f"{script.name}: expected one {name}= line, found {found}"
    return found[0]


def fake(bin_dir: Path, name: str, body: str) -> Path:
    """An executable bin_dir/name that runs body under bash."""
    bin_dir.mkdir(parents=True, exist_ok=True)
    path = bin_dir / name
    path.write_text(f"#!/usr/bin/env bash\n{body}")
    path.chmod(0o755)
    return path


def path_with(bin_dir: Path) -> dict[str, str]:
    """This process's environment with bin_dir first on PATH."""
    return {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}


def git_env() -> dict[str, str]:
    """An environment for throwaway test repositories that reads no user or system git config.

    A global config may sign commits or point hooks elsewhere; neither belongs
    in a fixture repository. Every inherited GIT_* variable is dropped too: a
    hook exports GIT_DIR, and with it every fixture git command would run
    against the repository being pushed instead of the fixture.
    """
    return {
        **{k: v for k, v in os.environ.items() if not k.startswith("GIT_")},
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "test",
        "GIT_AUTHOR_EMAIL": "test@example.invalid",
        "GIT_COMMITTER_NAME": "test",
        "GIT_COMMITTER_EMAIL": "test@example.invalid",
    }
