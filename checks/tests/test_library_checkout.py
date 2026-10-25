"""The reusable workflows that check out the library's own code use that
checkout only when it is the called repository at the called sha.

actions/checkout falls back to the caller's own repository and commit when
its repository or ref input is empty, and GitHub documents
job.workflow_repository and job.workflow_sha on github.com only. Without the
check, reusable-ci would scan with the caller's own .gitleaks.toml.

The step's own script runs under a fake git that logs its arguments, since
the sandbox cannot create a repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import LIBRARY_ROOT
from tests.shell import fake, path_with

WORKFLOWS = LIBRARY_ROOT / ".github" / "workflows"
CHECKOUT = "Checkout ci-cd-library at the called ref"
VERIFY = "Verify the library checkout"
# Names the folder only to refuse a caller path there, before the checkout.
REFUSE = "Refuse a caller .ci-cd-library"
# Workflow file, job, and the folder the library is checked out to.
CALLED = [
    ("reusable-ci.yml", "checks", ".ci-cd-library"),
    ("reusable-promote.yml", "gate", "ci-cd-library"),
]
SERVER = "https://github.com"
REPOSITORY = "fake-org/ci-cd-library"
SHA = "0123456789abcdef0123456789abcdef01234567"
CALLER_REPOSITORY = "fake-org/fake-repo"
CALLER_SHA = "fedcba9876543210fedcba9876543210fedcba98"


def load(path: Path) -> dict[str, Any]:
    """Return the workflow as data; PyYAML reads the `on` key as True."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def steps(workflow: str, job: str) -> list[dict[str, Any]]:
    return load(WORKFLOWS / workflow)["jobs"][job]["steps"]


def step(workflow: str, job: str, name: str) -> dict[str, Any]:
    found = [s for s in steps(workflow, job) if s.get("name") == name]
    assert len(found) == 1, f"step {name!r} moved; update this test"
    return found[0]


def test_every_library_checkout_is_listed() -> None:
    """A new workflow that checks out the library must get the check too."""
    found = [
        (path.name, name)
        for path in sorted(WORKFLOWS.glob("*.yml"))
        for name, job in load(path)["jobs"].items()
        for s in job.get("steps", [])
        if s.get("uses", "").startswith("actions/checkout@")
        and s.get("with", {}).get("repository") == "${{ job.workflow_repository }}"
    ]
    assert found == [(workflow, job) for workflow, job, _ in CALLED]


@pytest.mark.parametrize(("workflow", "job", "folder"), CALLED)
def test_the_check_runs_right_after_the_checkout_and_before_any_reader(
    workflow: str, job: str, folder: str
) -> None:
    names = [s.get("name") for s in steps(workflow, job)]
    checkout = names.index(CHECKOUT)
    assert step(workflow, job, CHECKOUT)["with"]["path"] == folder
    assert names[checkout + 1] == VERIFY
    readers = [
        i
        for i, s in enumerate(steps(workflow, job))
        if s.get("name") not in (VERIFY, REFUSE) and folder in s.get("run", "")
    ]
    assert readers, f"no step reads {folder}; update this test"
    assert min(readers) > names.index(VERIFY)


@pytest.mark.parametrize(("workflow", "job", "folder"), CALLED)
def test_the_contexts_reach_the_check_only_through_env(workflow: str, job: str, folder: str) -> None:
    verify = step(workflow, job, VERIFY)
    assert verify["env"] == {
        "WORKFLOW_REPOSITORY": "${{ job.workflow_repository }}",
        "WORKFLOW_SHA": "${{ job.workflow_sha }}",
    }
    assert "${{" not in verify["run"]


def run_verify(
    tmp_path: Path, workflow: str, job: str, folder: str, repository: str, sha: str, head: str, origin: str
) -> tuple[int, Path, str]:
    """Run the check's script with a fake git whose checkout at folder is origin at head.

    An empty head is a folder that is not a repository: git fails as it does there.
    """
    run = step(workflow, job, VERIFY)["run"]
    bin_dir = tmp_path / "bin"
    git_log = tmp_path / "git.log"
    fake(
        bin_dir,
        "git",
        f'printf "%s\\n" "$*" >> "{git_log}"\n'
        f'if [ "$1" != -C ] || [ "$2" != "{folder}" ] || [ -z "{head}" ]; then\n'
        '  echo "fatal: not a git repository" >&2\n'
        "  exit 128\n"
        "fi\n"
        'case "$3" in\n'
        f"  rev-parse) echo {head} ;;\n"
        f"  config) echo {origin} ;;\n"
        "esac\n",
    )
    result = subprocess.run(
        ["bash", "-c", run],
        cwd=tmp_path,
        env={
            **path_with(bin_dir),
            "GITHUB_SERVER_URL": SERVER,
            "WORKFLOW_REPOSITORY": repository,
            "WORKFLOW_SHA": sha,
        },
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, git_log, result.stderr + result.stdout


@pytest.mark.parametrize(("workflow", "job", "folder"), CALLED)
def test_the_called_repository_at_the_called_sha_passes(
    tmp_path: Path, workflow: str, job: str, folder: str
) -> None:
    returncode, git_log, output = run_verify(
        tmp_path, workflow, job, folder, REPOSITORY, SHA, SHA, f"{SERVER}/{REPOSITORY}"
    )
    assert returncode == 0, output
    assert git_log.read_text(encoding="utf-8").splitlines() == [
        f"-C {folder} rev-parse HEAD",
        f"-C {folder} config --get remote.origin.url",
    ]


@pytest.mark.parametrize(("workflow", "job", "folder"), CALLED)
@pytest.mark.parametrize(
    ("repository", "sha"), [("", SHA), (REPOSITORY, "")], ids=["empty-repository", "empty-sha"]
)
def test_an_empty_context_fails_before_reading_the_checkout(
    tmp_path: Path, workflow: str, job: str, folder: str, repository: str, sha: str
) -> None:
    returncode, git_log, output = run_verify(
        tmp_path, workflow, job, folder, repository, sha, SHA, f"{SERVER}/{REPOSITORY}"
    )
    assert returncode != 0
    assert "::error::" in output
    assert not git_log.exists()


@pytest.mark.parametrize(("workflow", "job", "folder"), CALLED)
@pytest.mark.parametrize(
    ("head", "origin"),
    [
        (CALLER_SHA, f"{SERVER}/{REPOSITORY}"),
        (SHA, f"{SERVER}/{CALLER_REPOSITORY}"),
        (CALLER_SHA, f"{SERVER}/{CALLER_REPOSITORY}"),
        ("", ""),
    ],
    ids=["caller-commit", "caller-repository", "caller-checkout", "not-a-repository"],
)
def test_any_other_checkout_fails(
    tmp_path: Path, workflow: str, job: str, folder: str, head: str, origin: str
) -> None:
    returncode, _, output = run_verify(tmp_path, workflow, job, folder, REPOSITORY, SHA, head, origin)
    assert returncode != 0
    assert "::error::" in output
