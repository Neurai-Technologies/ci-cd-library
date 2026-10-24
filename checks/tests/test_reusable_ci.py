"""reusable-ci's optional inputs: their defaults keep the old behaviour, every
action is pinned to a full commit SHA, and the services start only when given
an image."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import LIBRARY_ROOT

CI = LIBRARY_ROOT / ".github" / "workflows" / "reusable-ci.yml"
PIN = re.compile(r"^\s*(?:-\s+)?uses:\s+[\w.-]+/[\w./-]+@[0-9a-f]{40} # v\d+\.\d+\.\d+$")


def load(path: Path) -> dict[str, Any]:
    """Return the workflow as data; PyYAML reads the `on` key as True."""
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def call_inputs(workflow: dict[str, Any]) -> dict[str, Any]:
    return workflow[True]["workflow_call"]["inputs"]


def step(job: dict[str, Any], name: str) -> dict[str, Any]:
    found = [s for s in job["steps"] if s.get("name") == name]
    assert len(found) == 1, f"step {name!r} moved; update this test"
    return found[0]


def test_every_action_is_pinned_to_a_full_sha() -> None:
    lines = [
        line
        for line in CI.read_text(encoding="utf-8").splitlines()
        if "uses:" in line and not line.lstrip().startswith("#")
    ]
    assert lines
    for line in lines:
        assert PIN.match(line), line


NEW_CI_DEFAULTS = {
    "node-cache": "",
    "rust-toolchain": "",
    "cargo-dir": "",
    "postgres-image": "",
    "redis-image": "",
}


@pytest.mark.parametrize("name", sorted(NEW_CI_DEFAULTS))
def test_new_ci_inputs_default_to_off(name: str) -> None:
    spec = call_inputs(load(CI))[name]
    assert spec["required"] is False
    assert spec["default"] == NEW_CI_DEFAULTS[name]


def test_services_start_only_with_an_image() -> None:
    """An empty image is GitHub's documented way to not start a service."""
    services = load(CI)["jobs"]["checks"]["services"]
    assert services["postgres"]["image"] == "${{ inputs.postgres-image }}"
    assert services["redis"]["image"] == "${{ inputs.redis-image }}"
    assert services["postgres"]["ports"] == ["5432:5432"]
    assert services["redis"]["ports"] == ["6379:6379"]
    for service in services.values():
        assert "--health-cmd" in service["options"]


@pytest.mark.parametrize("name", ["postgres-db", "postgres-password", "postgres-user"])
def test_postgres_credentials_default_to_test(name: str) -> None:
    spec = call_inputs(load(CI))[name]
    assert spec["required"] is False
    assert spec["default"] == "test"


def test_postgres_service_gets_the_credential_inputs() -> None:
    env = load(CI)["jobs"]["checks"]["services"]["postgres"]["env"]
    assert env == {
        "POSTGRES_USER": "${{ inputs.postgres-user }}",
        "POSTGRES_PASSWORD": "${{ inputs.postgres-password }}",
        "POSTGRES_DB": "${{ inputs.postgres-db }}",
    }


def test_each_service_options_hold_its_health_command() -> None:
    services = load(CI)["jobs"]["checks"]["services"]
    assert '--health-cmd "pg_isready -U $POSTGRES_USER"' in services["postgres"]["options"]
    assert '--health-cmd "redis-cli ping"' in services["redis"]["options"]


def test_no_input_reaches_service_options() -> None:
    """Options go to docker create unparsed, before any step or secret scan runs."""
    for service in load(CI)["jobs"]["checks"]["services"].values():
        assert not re.search(r"\$\{\{", service["options"])


def test_inputs_reach_shell_only_through_env() -> None:
    """An input expanded into `run:` is pasted into the script before bash parses it."""
    for job in load(CI)["jobs"].values():
        for s in job["steps"]:
            assert not re.search(r"\$\{\{\s*inputs\.", s.get("run", "")), s.get("name")


def test_the_test_command_gets_the_postgres_credentials_under_library_names() -> None:
    """A caller builds its URL from these, never writes the password; no PG* export."""
    run = step(load(CI)["jobs"]["checks"], "Run test command")
    env = run["env"]
    assert env["CI_POSTGRES_USER"] == "${{ inputs.postgres-user }}"
    assert env["CI_POSTGRES_PASSWORD"] == "${{ inputs.postgres-password }}"
    assert env["CI_POSTGRES_DB"] == "${{ inputs.postgres-db }}"
    assert not [name for name in env if name.startswith("PG")]


def test_toolchain_and_cache_steps_are_skipped_by_default() -> None:
    job = load(CI)["jobs"]["checks"]
    assert step(job, "Install the Rust toolchain")["if"] == "inputs.rust-toolchain != ''"
    assert step(job, "Cache cargo")["if"] == "inputs.cargo-dir != ''"
    node = step(job, "Set up Node")
    assert node["if"] == "inputs.node-version != ''"
    # setup-node caches nothing for an empty `cache` input.
    assert node["with"]["cache"] == "${{ inputs.node-cache }}"


def test_secret_scans_run_before_any_setup_or_caller_code() -> None:
    names = [s.get("name") for s in load(CI)["jobs"]["checks"]["steps"]]
    last_scan = names.index("Scan full history")
    assert names.index("Scan current files") < last_scan
    for later in (
        "Set up Python",
        "Set up Node",
        "Install the Rust toolchain",
        "Cache cargo",
        "Run test command",
    ):
        assert names.index(later) > last_scan, later


def test_rust_steps_run_between_the_history_scan_and_the_test_command() -> None:
    names = [s.get("name") for s in load(CI)["jobs"]["checks"]["steps"]]
    for name in ("Cache cargo", "Install the Rust toolchain"):
        assert names.index("Scan full history") < names.index(name) < names.index("Run test command"), name


def test_cargo_cache_key_names_the_toolchain_and_the_lockfile() -> None:
    """A toolchain bump with the same Cargo.lock must not restore a stale target/."""
    key = step(load(CI)["jobs"]["checks"], "Cache cargo")["with"]["key"]
    assert "${{ inputs.rust-toolchain }}" in key
    assert "hashFiles(format('{0}/Cargo.lock', inputs.cargo-dir))" in key


def run_rust_step(tmp_path: Path, toolchain: str) -> tuple[int, Path, str]:
    """Run the Rust step's script with a fake rustup that logs its arguments."""
    run = step(load(CI)["jobs"]["checks"], "Install the Rust toolchain")["run"]
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log = tmp_path / "rustup.log"
    fake = bin_dir / "rustup"
    fake.write_text(f'#!/bin/bash\nprintf "%s\\n" "$*" >> "{log}"\n', encoding="utf-8")
    fake.chmod(0o755)
    result = subprocess.run(
        ["bash", "-c", run],
        env={**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}", "RUST_TOOLCHAIN": toolchain},
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, log, result.stderr + result.stdout


def test_rust_step_installs_the_given_toolchain(tmp_path: Path) -> None:
    returncode, log, _ = run_rust_step(tmp_path, "1.99.0")
    assert returncode == 0
    assert log.read_text(encoding="utf-8").splitlines() == [
        "toolchain install 1.99.0 --profile minimal --component rustfmt --component clippy",
        "default 1.99.0",
    ]


@pytest.mark.parametrize("toolchain", ["stable", "1.99", "-x", "1.99.0;id"])
def test_rust_step_refuses_anything_but_an_exact_version(tmp_path: Path, toolchain: str) -> None:
    returncode, log, output = run_rust_step(tmp_path, toolchain)
    assert returncode != 0
    assert not log.exists()
    # The refusal names the rule, never the rejected value.
    assert toolchain not in output
