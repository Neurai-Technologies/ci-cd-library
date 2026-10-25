"""reusable-ci's history-since input: empty keeps the whole-history scan exactly
as it was, a full SHA that is an ancestor of HEAD narrows the history scan to
the commits after it, and anything else fails before Gitleaks runs.

The step's own script runs under a fake git and a fake gitleaks that log
their arguments, since the sandbox cannot create a repository.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import LIBRARY_ROOT
from tests.shell import fake, path_with

CI = LIBRARY_ROOT / ".github" / "workflows" / "reusable-ci.yml"
SHA = "0123456789abcdef0123456789abcdef01234567"
COUNT = "7"


def load() -> dict[str, Any]:
    """Return the workflow as data; PyYAML reads the `on` key as True."""
    return yaml.safe_load(CI.read_text(encoding="utf-8"))


def step(name: str) -> dict[str, Any]:
    found = [s for s in load()["jobs"]["checks"]["steps"] if s.get("name") == name]
    assert len(found) == 1, f"step {name!r} moved; update this test"
    return found[0]


def base_args(runner_temp: Path) -> list[str]:
    """The history scan's arguments before history-since existed."""
    return [
        "git",
        ".",
        "--redact",
        "--no-banner",
        "--ignore-gitleaks-allow",
        "--config",
        f"{runner_temp}/ci-cd-library/gitleaks.toml",
    ]


def run_history_scan(
    tmp_path: Path, since: str, ancestor: bool = True, shallow: bool = False
) -> tuple[int, Path, Path, str]:
    """Run the history scan step's script with fake git and gitleaks on PATH."""
    run = step("Scan full history")["run"]
    bin_dir = tmp_path / "bin"
    git_log = tmp_path / "git.log"
    gitleaks_log = tmp_path / "gitleaks.log"
    fake(
        bin_dir,
        "git",
        f'printf "%s\\n" "$*" >> "{git_log}"\n'
        'case "$1" in\n'
        f"  rev-parse) echo {'true' if shallow else 'false'} ;;\n"
        f"  merge-base) exit {0 if ancestor else 1} ;;\n"
        f"  rev-list) echo {COUNT} ;;\n"
        "esac\n",
    )
    fake(bin_dir, "gitleaks", f'printf "%s\\n" "$@" > "{gitleaks_log}"\n')
    runner_temp = tmp_path / "runner"
    result = subprocess.run(
        ["bash", "-c", run],
        env={**path_with(bin_dir), "HISTORY_SINCE": since, "RUNNER_TEMP": str(runner_temp)},
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode, git_log, gitleaks_log, result.stderr + result.stdout


def test_history_since_defaults_to_empty_and_is_described() -> None:
    spec = load()[True]["workflow_call"]["inputs"]["history-since"]
    assert spec["type"] == "string"
    assert spec["required"] is False
    assert spec["default"] == ""
    assert "product's own scanner" in spec["description"]
    assert "fetch-depth 0" in spec["description"]


def test_history_since_reaches_the_scan_only_through_env() -> None:
    scan = step("Scan full history")
    assert scan["env"] == {"HISTORY_SINCE": "${{ inputs.history-since }}"}
    assert "${{" not in scan["run"]


def test_current_files_scan_is_untouched_by_history_since() -> None:
    scan = step("Scan current files")
    assert scan == {
        "name": "Scan current files",
        "run": 'gitleaks dir . --redact --no-banner --ignore-gitleaks-allow --config "$RUNNER_TEMP/ci-cd-library/gitleaks.toml"',
    }


def test_empty_history_since_scans_the_whole_history_as_before(tmp_path: Path) -> None:
    returncode, git_log, gitleaks_log, _ = run_history_scan(tmp_path, "")
    assert returncode == 0
    assert gitleaks_log.read_text(encoding="utf-8").splitlines() == base_args(tmp_path / "runner")
    assert not git_log.exists()


def test_an_ancestor_sha_narrows_the_scan_to_the_commits_after_it(tmp_path: Path) -> None:
    returncode, git_log, gitleaks_log, output = run_history_scan(tmp_path, SHA)
    assert returncode == 0
    assert gitleaks_log.read_text(encoding="utf-8").splitlines() == [
        *base_args(tmp_path / "runner"),
        f"--log-opts={SHA}..HEAD",
    ]
    assert git_log.read_text(encoding="utf-8").splitlines() == [
        "rev-parse --is-shallow-repository",
        f"merge-base --is-ancestor {SHA} HEAD",
        f"rev-list --count {SHA}..HEAD",
    ]
    assert (
        f"::notice title=History scan narrowed::The history scan starts after commit {SHA} and covers {COUNT} commits"
        in output
    )


@pytest.mark.parametrize(
    "since",
    [
        "main",
        SHA[:39],
        SHA + "0",
        "x" + SHA,
        SHA + "\n",
        SHA.upper(),
        f"{SHA[:20]} {SHA[20:39]}",
        f"{SHA};id",
    ],
)
def test_anything_but_a_full_lowercase_sha_is_refused(tmp_path: Path, since: str) -> None:
    returncode, git_log, gitleaks_log, output = run_history_scan(tmp_path, since)
    assert returncode != 0
    assert not gitleaks_log.exists()
    assert not git_log.exists()
    # The refusal names the rule, never the rejected value.
    assert since not in output


def test_a_sha_that_is_not_an_ancestor_of_head_is_refused(tmp_path: Path) -> None:
    returncode, git_log, gitleaks_log, output = run_history_scan(tmp_path, SHA, ancestor=False)
    assert returncode != 0
    assert not gitleaks_log.exists()
    assert git_log.read_text(encoding="utf-8").splitlines() == [
        "rev-parse --is-shallow-repository",
        f"merge-base --is-ancestor {SHA} HEAD",
    ]
    assert "not an ancestor of HEAD" in output


def test_a_shallow_checkout_is_refused_even_when_the_sha_is_present(tmp_path: Path) -> None:
    """A shallow clone can hold the named commit yet miss merged-in commits below its depth."""
    returncode, git_log, gitleaks_log, output = run_history_scan(tmp_path, SHA, shallow=True)
    assert returncode != 0
    assert not gitleaks_log.exists()
    assert git_log.read_text(encoding="utf-8").splitlines() == ["rev-parse --is-shallow-repository"]
    assert "shallow" in output
