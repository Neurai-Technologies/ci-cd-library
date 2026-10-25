"""reusable-ci stops when the caller's repository holds .ci-cd-library at its root.

The library is checked out to .ci-cd-library inside the caller's checkout.
actions/checkout removes a file at that path, and empties a folder there that
does not already hold the library, before it checks the library out in its
place. A folder, file or symlink the caller commits there would be gone, or
would send that removal into the caller's own tree, before the current-files
scan reads the workspace.

The step's own script runs in a temporary folder that stands in for the
caller's checkout.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import LIBRARY_ROOT

CI = LIBRARY_ROOT / ".github" / "workflows" / "reusable-ci.yml"
CALLER_CHECKOUT = "Checkout caller repository"
REFUSE = "Refuse a caller .ci-cd-library"
LIBRARY_CHECKOUT = "Checkout ci-cd-library at the called ref"


def load() -> dict[str, Any]:
    """Return the workflow as data; PyYAML reads the `on` key as True."""
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def steps() -> list[dict[str, Any]]:
    return load()["jobs"]["checks"]["steps"]


def step(name: str) -> dict[str, Any]:
    found = [s for s in steps() if s.get("name") == name]
    assert len(found) == 1, f"step {name!r} moved; update this test"
    return found[0]


def folder() -> str:
    """The folder the library is checked out to, as the workflow names it."""
    return step(LIBRARY_CHECKOUT)["with"]["path"]


def test_the_check_runs_between_the_caller_checkout_and_the_library_checkout() -> None:
    names = [s.get("name") for s in steps()]
    caller = names.index(CALLER_CHECKOUT)
    assert "path" not in step(CALLER_CHECKOUT).get("with", {})
    assert folder() == ".ci-cd-library"
    assert names[caller + 1] == REFUSE
    assert names[caller + 2] == LIBRARY_CHECKOUT
    assert "${{" not in step(REFUSE)["run"]


def run_refuse(workspace: Path) -> tuple[int, str]:
    """Run the check's script from workspace, the stand-in for the caller's checkout."""
    result = subprocess.run(
        ["bash", "-c", step(REFUSE)["run"]],
        cwd=workspace,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, result.stderr + result.stdout


def make_folder(workspace: Path, name: str) -> None:
    (workspace / name).mkdir()


def make_file(workspace: Path, name: str) -> None:
    (workspace / name).write_text("caller file\n", encoding="utf-8")


def make_symlink_to_folder(workspace: Path, name: str) -> None:
    (workspace / "config").mkdir()
    (workspace / name).symlink_to("config")


def make_dangling_symlink(workspace: Path, name: str) -> None:
    (workspace / name).symlink_to("missing")


@pytest.mark.parametrize(
    "make",
    [make_folder, make_file, make_symlink_to_folder, make_dangling_symlink],
    ids=["folder", "file", "symlink-to-folder", "dangling-symlink"],
)
def test_anything_at_the_library_folder_fails(tmp_path: Path, make: Any) -> None:
    make(tmp_path, folder())
    returncode, output = run_refuse(tmp_path)
    assert returncode != 0
    assert "::error::" in output
    assert (tmp_path / folder()).is_symlink() or (tmp_path / folder()).exists()


def test_a_caller_without_the_library_folder_passes(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir()
    (tmp_path / "README.md").write_text("caller file\n", encoding="utf-8")
    returncode, output = run_refuse(tmp_path)
    assert returncode == 0, output
    assert "::error::" not in output
