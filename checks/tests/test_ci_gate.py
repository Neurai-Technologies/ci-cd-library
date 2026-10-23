"""Exercise the exact gate executable promotion and the CD image share, with the policy each renders.

ci-gate.sh reads ci-policy.json from its own folder. The deployer renders it
from product.settings at start (render_policy) and reusable-promote.yml from its
inputs; both are run here as they ship, and the gate is driven with the
deployer's policy.
"""

from __future__ import annotations

import copy
import json
import os
import shlex
import shutil
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
import yaml

from conftest import LIBRARY_ROOT, ProductSettings
from tests.shell import assignment, lift

LOOP = LIBRARY_ROOT / "deploy" / "deployer" / "deploy-loop.sh"
SHA = "a" * 40


def render(env: ProductSettings, folder: Path) -> dict:
    """render_policy, lifted from the loop, with the loop's own defaults applied to product.settings."""
    code = (
        "set -euo pipefail\n"
        f". {shlex.quote(str(env.path))}\n"
        + "".join(
            f"{assignment(LOOP, name)}\n"
            for name in ("BRANCH", "TESTS_WORKFLOW", "PROMOTE_WORKFLOW", "CI_BRANCH")
        )
        + f"SCRIPT_DIR={shlex.quote(str(folder))}\n"
        + f"{lift(LOOP, 'render_policy')}render_policy\n"
    )
    subprocess.run(["bash", "-c", code], check=True)
    return json.loads((folder / "ci-policy.json").read_text())


@pytest.fixture
def env(product_settings: Callable[..., ProductSettings]) -> ProductSettings:
    return product_settings()


@pytest.fixture
def gate(tmp_path: Path, env: ProductSettings) -> tuple[Path, dict]:
    """A copy of the gate beside the policy the deployer renders, as the image holds them."""
    folder = tmp_path / "gate"
    folder.mkdir()
    for name in ("ci-gate.sh", "ci-gate.jq"):
        shutil.copy(LOOP.parent / name, folder / name)
    return folder / "ci-gate.sh", render(env, folder)


def test_the_policy_is_the_products_settings(gate: tuple[Path, dict], env: ProductSettings) -> None:
    _, policy = gate
    v = env.values
    assert policy == {
        "required_workflows": [v["TESTS_WORKFLOW"]],
        "release_branches": [v["SOURCE_BRANCH"], v["DEPLOY_BRANCH"]],
        "post_release_workflows": [v["PROMOTE_WORKFLOW"]],
    }


def test_the_policy_is_written_into_the_image_never_read_from_the_clone() -> None:
    """A commit must not relax its own gate: render_policy writes beside the
    baked gate and runs before the clone is touched."""
    text = LOOP.read_text(encoding="utf-8")
    assert '>"$SCRIPT_DIR/ci-policy.json"' in lift(LOOP, "render_policy")
    assert text.index("\nrender_policy ||") < text.index("\nensure_clone\n")
    assert 'bash "$SCRIPT_DIR/ci-gate.sh"' in lift(LOOP, "ci_green")


POST_RELEASE = [
    ".github/workflows/fake-promote.yml",
    ".github/workflows/fake-promote.yml .github/workflows/fake-index.yml",
    "  .github/workflows/fake-promote.yml   .github/workflows/fake-index.yml ",
]


@pytest.mark.parametrize("post", POST_RELEASE, ids=["one", "two", "extra-spaces"])
def test_the_post_release_workflows_are_a_space_split_list(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], post: str
) -> None:
    """PROMOTE_WORKFLOW may name several post-release workflows, space-separated;
    each is its own entry, and no empty entry comes from extra spaces."""
    policy = render(product_settings(PROMOTE_WORKFLOW=post), tmp_path)
    assert policy["post_release_workflows"] == post.split()


@pytest.mark.parametrize("post", POST_RELEASE, ids=["one", "two", "extra-spaces"])
def test_promotion_renders_the_same_policy(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], post: str
) -> None:
    """reusable-promote.yml's policy step, run as written, from the same settings."""
    env = product_settings(PROMOTE_WORKFLOW=post)
    workflow = yaml.safe_load(
        (LIBRARY_ROOT / ".github/workflows/reusable-promote.yml").read_text(encoding="utf-8")
    )
    steps = [
        step
        for job in workflow["jobs"].values()
        for step in job.get("steps", [])
        if step.get("name") == "Write the release policy"
    ]
    assert len(steps) == 1, "the policy step moved; update this test"
    work = tmp_path / "work"
    work.mkdir()
    (work / "ci-cd-library").symlink_to(LIBRARY_ROOT)
    v = env.values
    subprocess.run(
        ["bash", "-c", steps[0]["run"]],
        cwd=work,
        env={
            **os.environ,
            "RUNNER_TEMP": str(tmp_path / "runner"),
            "TESTS_WORKFLOW": v["TESTS_WORKFLOW"],
            "SOURCE_BRANCH": v["SOURCE_BRANCH"],
            "TARGET_BRANCH": v["DEPLOY_BRANCH"],
            "POST_RELEASE": v["PROMOTE_WORKFLOW"],
        },
        check=True,
        capture_output=True,
    )
    promoted = json.loads((tmp_path / "runner" / "ci-gate" / "ci-policy.json").read_text())
    deployed = render(env, tmp_path)
    assert promoted == deployed


def run(gate: tuple[Path, dict], env: ProductSettings, path: str, **updates: Any) -> dict:
    return {
        "path": path,
        "head_sha": SHA,
        "head_repository": {"full_name": env.values["GITHUB_REPO"]},
        "event": "push",
        "head_branch": gate[1]["release_branches"][0],
        "status": "completed",
        "conclusion": "success",
        **updates,
    }


def verdict(
    gate: tuple[Path, dict], env: ProductSettings, runs: list[dict], *, total: int | None = None
) -> subprocess.CompletedProcess:
    payload = {"total_count": len(runs) if total is None else total, "workflow_runs": runs}
    return subprocess.run(
        ["bash", str(gate[0]), SHA, env.values["GITHUB_REPO"]],
        input=json.dumps(payload),
        text=True,
        capture_output=True,
        check=False,
    )


def required(gate: tuple[Path, dict]) -> list[str]:
    return gate[1]["required_workflows"]


def test_required_release_pushes_pass(gate: tuple[Path, dict], env: ProductSettings) -> None:
    result = verdict(gate, env, [run(gate, env, path) for path in required(gate)])
    assert result.returncode == 0, result.stderr


def test_a_push_on_the_target_branch_counts_too(gate: tuple[Path, dict], env: ProductSettings) -> None:
    target = gate[1]["release_branches"][1]
    result = verdict(gate, env, [run(gate, env, path, head_branch=target) for path in required(gate)])
    assert result.returncode == 0, result.stderr


def test_missing_required_workflow_blocks(gate: tuple[Path, dict], env: ProductSettings) -> None:
    result = verdict(gate, env, [run(gate, env, ".github/workflows/other.yml")])
    assert result.returncode != 0
    assert required(gate)[0] in result.stderr


@pytest.mark.parametrize("conclusion", ["failure", "cancelled", "skipped", "neutral", "timed_out", None])
def test_non_success_blocks(gate: tuple[Path, dict], env: ProductSettings, conclusion: str | None) -> None:
    runs = [run(gate, env, path, conclusion=conclusion) for path in required(gate)]
    assert verdict(gate, env, runs).returncode != 0


@pytest.mark.parametrize(
    "updates",
    [
        {"event": "pull_request"},
        {"event": "workflow_dispatch"},
        {"head_branch": "feature/untrusted"},
        {"head_repository": {"full_name": "someone-else/fake-repo"}},
        {"head_sha": "b" * 40},
        {"status": "in_progress"},
    ],
)
def test_wrong_source_or_incomplete_run_cannot_replace_release_evidence(
    gate: tuple[Path, dict], env: ProductSettings, updates: dict
) -> None:
    assert verdict(gate, env, [run(gate, env, path, **updates) for path in required(gate)]).returncode != 0


def test_truncated_response_blocks(gate: tuple[Path, dict], env: ProductSettings) -> None:
    assert verdict(gate, env, [run(gate, env, path) for path in required(gate)], total=101).returncode != 0


def test_no_runs_blocks(gate: tuple[Path, dict], env: ProductSettings) -> None:
    assert verdict(gate, env, []).returncode != 0


def test_unrelated_authored_failure_still_blocks(gate: tuple[Path, dict], env: ProductSettings) -> None:
    runs = [run(gate, env, path) for path in required(gate)]
    runs.append(run(gate, env, ".github/workflows/extra.yml", conclusion="failure"))
    assert verdict(gate, env, runs).returncode != 0


def test_only_known_post_release_and_dynamic_runs_are_excluded(
    gate: tuple[Path, dict], env: ProductSettings
) -> None:
    runs = [run(gate, env, path) for path in required(gate)]
    runs.extend(
        [
            run(
                gate,
                env,
                "dynamic/dependabot/update-graph",
                event="dynamic",
                status="queued",
                conclusion=None,
            ),
            run(
                gate,
                env,
                env.values["PROMOTE_WORKFLOW"],
                event="workflow_run",
                status="in_progress",
                conclusion=None,
            ),
        ]
    )
    assert verdict(gate, env, runs).returncode == 0


def test_cancelled_duplicate_is_not_hidden_by_a_green_sibling(
    gate: tuple[Path, dict], env: ProductSettings
) -> None:
    runs = [run(gate, env, path) for path in required(gate)]
    runs.append(run(gate, env, required(gate)[0], event="pull_request", conclusion="cancelled"))
    assert verdict(gate, env, runs).returncode != 0


def test_successful_rerun_of_same_record_recovers(gate: tuple[Path, dict], env: ProductSettings) -> None:
    runs = [run(gate, env, path) for path in required(gate)]
    failed = copy.deepcopy(runs)
    failed[0]["conclusion"] = "cancelled"
    assert verdict(gate, env, failed).returncode != 0
    assert verdict(gate, env, runs).returncode == 0


def test_malformed_response_fails_closed(gate: tuple[Path, dict], env: ProductSettings) -> None:
    result = subprocess.run(
        ["bash", str(gate[0]), SHA, env.values["GITHUB_REPO"]],
        input='{"message":"forbidden"}',
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0


def test_a_gate_without_its_policy_fails_closed(tmp_path: Path, env: ProductSettings) -> None:
    """No rendered policy, no verdict: the gate must not pass on a missing file."""
    folder = tmp_path / "bare"
    folder.mkdir()
    for name in ("ci-gate.sh", "ci-gate.jq"):
        shutil.copy(LOOP.parent / name, folder / name)
    payload = json.dumps({"total_count": 0, "workflow_runs": []})
    result = subprocess.run(
        ["bash", str(folder / "ci-gate.sh"), SHA, env.values["GITHUB_REPO"]],
        input=payload,
        text=True,
        capture_output=True,
        check=False,
    )
    assert result.returncode != 0
