"""reusable-promote's optional push token: the defaults keep the environment
token path, every action is pinned to a full commit SHA, and promotion pushes
with exactly one token."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import LIBRARY_ROOT

PROMOTE = LIBRARY_ROOT / ".github" / "workflows" / "reusable-promote.yml"
PIN = re.compile(r"^\s*(?:-\s+)?uses:\s+[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")
GO = "needs.gate.outputs.go == 'yes'"
SWITCH = "inputs.github-token-push"


def load(path: Path) -> dict[str, Any]:
    """Return the workflow as data; PyYAML reads the `on` key as True."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def call_inputs(workflow: dict[str, Any]) -> dict[str, Any]:
    return workflow[True]["workflow_call"]["inputs"]


def step(job: dict[str, Any], name: str) -> dict[str, Any]:
    found = [s for s in job["steps"] if s.get("name") == name]
    assert len(found) == 1, f"step {name!r} moved; update this test"
    return found[0]


def conditions(job: dict[str, Any]) -> list[str]:
    """Return the `&&` clauses of a job's `if:`."""
    return [clause.strip() for clause in job["if"].split("&&")]


def test_every_action_is_pinned_to_a_full_sha() -> None:
    lines = [
        line
        for line in PROMOTE.read_text(encoding="utf-8").splitlines()
        if "uses:" in line and not line.lstrip().startswith("#")
    ]
    assert lines
    for line in lines:
        assert PIN.match(line), line


def test_promote_defaults_keep_the_environment_token_path() -> None:
    workflow = load(PROMOTE)
    inputs = call_inputs(workflow)
    assert inputs["github-token-push"]["type"] == "boolean"
    assert inputs["github-token-push"]["default"] is False
    assert inputs["environment"]["default"] == "production"
    assert workflow[True]["workflow_call"]["secrets"]["push-token"]["required"] is False
    job = workflow["jobs"]["promote"]
    assert job["environment"] == "${{ inputs.environment }}"
    assert step(job, "Checkout the gated sha")["with"]["token"] == "${{ secrets.push-token }}"
    assert step(job, "Require the push token")


def test_github_token_push_uses_github_token() -> None:
    workflow = load(PROMOTE)
    job = workflow["jobs"]["promote-github-token"]
    assert "environment" not in job
    assert "permissions" not in workflow
    assert "token" not in step(job, "Checkout the gated sha")["with"]
    assert job["concurrency"] == workflow["jobs"]["promote"]["concurrency"]
    assert job["timeout-minutes"] == 5


def test_the_push_jobs_need_the_gate_and_split_on_the_switch() -> None:
    jobs = load(PROMOTE)["jobs"]
    for name in ("promote", "promote-github-token"):
        assert jobs[name]["needs"] in ("gate", ["gate"]), name
    # Both require the gate's go; exactly one runs for either switch value.
    assert conditions(jobs["promote"]) == [GO, f"!{SWITCH}"]
    assert conditions(jobs["promote-github-token"]) == [GO, SWITCH]


def test_only_the_github_token_job_keeps_the_callers_permissions() -> None:
    # A job without a permissions key gets every grant the caller gives,
    # contents: write included; only the GITHUB_TOKEN push job may.
    jobs = load(PROMOTE)["jobs"]
    assert [name for name, job in jobs.items() if "permissions" not in job] == ["promote-github-token"]


def test_every_other_promote_job_reads_only() -> None:
    jobs = load(PROMOTE)["jobs"]
    for name in ("gate", "promote"):
        assert jobs[name]["permissions"] == {"contents": "read", "actions": "read"}, name


def test_both_push_jobs_push_the_same_way() -> None:
    jobs = load(PROMOTE)["jobs"]
    pushes = {
        name: step(jobs[name], "Fast-forward the target branch")
        for name in ("promote", "promote-github-token")
    }
    assert pushes["promote"] == pushes["promote-github-token"]
    assert "--force" not in pushes["promote"]["run"]


@pytest.mark.parametrize(("token", "code"), [("", 0), ("t0ken", 1)], ids=["absent", "passed"])
def test_a_push_token_without_an_environment_is_refused(token: str, code: int) -> None:
    run = step(load(PROMOTE)["jobs"]["promote-github-token"], "Refuse a push token without an environment")[
        "run"
    ]
    result = subprocess.run(
        ["bash", "-c", run], env={**os.environ, "PUSH_TOKEN": token}, capture_output=True, check=False
    )
    assert result.returncode == code
