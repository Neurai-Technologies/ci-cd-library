"""Which commits restart the server, decided by running the real predicate.

A marketing site, a phone app or Markdown files may share the history with
the server, and APP_IGNORE_PATHS (git pathspecs, space-separated) names the
paths whose changes alone should not recreate the app stack: a restart drops
connections and re-runs migrations for files this container never reads.

Getting that wrong is expensive in both directions, which is why these run
the function out of the shipped script against a real repository rather than
reading it for the right substring. Too eager and every documentation commit
is an outage nobody scheduled. Too clever and a backend change hidden in a
mixed commit never reaches production, while the log says it was recorded.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import Path

import pytest

from conftest import LIBRARY_ROOT
from tests.shell import assignment, git_env, lift
from tests.test_deploy_hardening import block

LOOP = LIBRARY_ROOT / "deploy" / "deployer" / "deploy-loop.sh"
# The example's value: a site/ folder, docs/ and Markdown anywhere.
IGNORE = "site docs *.md"


@pytest.fixture(scope="module")
def repo(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """A repository with one commit per kind of change."""
    path = tmp_path_factory.mktemp("repo")
    env = git_env()

    def git(*args: str) -> str:
        done = subprocess.run(
            ["git", "-C", str(path), *args], check=True, capture_output=True, text=True, env=env
        )
        return done.stdout.strip()

    git("init", "-q", "-b", "main")

    def commit(message: str, files: dict[str, str]) -> str:
        for name, body in files.items():
            target = path / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(body)
        git("add", "-A")
        git("commit", "-q", "-m", message)
        return git("rev-parse", "HEAD")

    shas = {
        "base": commit("base", {"backend/main.py": "one", "site/index.html": "one"}),
        "site": commit("site", {"site/index.html": "two"}),
        "docs": commit("docs", {"docs/guide.md": "how it works"}),
        "docs_config": commit("docs config", {"docs/nginx.conf": "one"}),
        "readme": commit("readme", {"README.md": "one", "deploy/README.md": "one"}),
        "markdown_and_backend": commit("mixed docs", {"README.md": "two", "backend/main.py": "two"}),
        "markdown_lookalike": commit("not markdown", {"backend/notes_md.py": "code"}),
        "backend": commit("backend", {"backend/main.py": "three"}),
        "site_and_backend": commit(
            "site and backend", {"site/index.html": "three", "backend/main.py": "four"}
        ),
        "unknown": commit("unknown", {"justfile": "recipes"}),
        "deploy": commit("deploy", {"deploy/compose.yml": "changed"}),
        "lookalike": commit("look-alike folder", {"site-old/notes.txt": "kept"}),
        "other_markdown": commit("markdown", {"backend/CHANGES.md": "notes"}),
        "outside_paths": commit("outside", {"mobile/app.txt": "one"}),
        "inside_paths": commit("inside", {"backend/main.py": "five"}),
        "inside_but_ignored": commit("inside ignored", {"backend/NOTES.md": "notes"}),
        "new_folder": commit("new folder", {"worker/job.py": "one"}),
    }
    shas["path"] = str(path)
    return shas


def app_changed(
    repo: dict[str, str],
    current: str,
    sha: str,
    ignore: str = IGNORE,
    cwd: Path | None = None,
    paths: str = "",
) -> bool:
    """Run the shipped predicate. True when the server would be recreated."""
    harness = (
        "set -u\n"
        f"SRC={shlex.quote(repo['path'])}\nBRANCH=main\nAPP_IGNORE_PATHS={shlex.quote(ignore)}\n"
        f"APP_PATHS={shlex.quote(paths)}\n"
        # The daemon fetches when it has never seen the sha. Here every commit
        # is already present, and a stub that fails proves the fetch is not
        # what any of these answers depends on.
        "git_authed() { return 1; }\n"
        f"{assignment(LOOP, 'EMPTY_TREE')}\n"
        f"{lift(LOOP, 'app_changed')}"
        f'app_changed "{current}" "{sha}"\n'
    )
    # check=False on purpose: the exit code IS the answer here.
    done = subprocess.run(["bash", "-c", harness], capture_output=True, check=False, cwd=cwd, env=git_env())
    # 2 (an APP_PATHS spec matching nothing) or a shell error is neither answer.
    assert done.returncode in (0, 1), done.stdout + done.stderr
    return done.returncode == 0


def test_a_change_inside_an_ignored_folder_alone_does_not_restart_the_server(repo: dict[str, str]) -> None:
    assert not app_changed(repo, repo["base"], repo["site"])


def test_documentation_alone_does_not_restart_the_server(repo: dict[str, str]) -> None:
    """docs/ as a whole and Markdown anywhere (the README, deploy/README.md)."""
    assert not app_changed(repo, repo["site"], repo["docs"])
    assert not app_changed(repo, repo["docs"], repo["docs_config"])
    assert not app_changed(repo, repo["docs_config"], repo["readme"])


def test_a_backend_change_hidden_behind_a_readme_edit_still_restarts_it(repo: dict[str, str]) -> None:
    assert app_changed(repo, repo["readme"], repo["markdown_and_backend"])


def test_a_file_that_only_looks_like_markdown_still_restarts_it(repo: dict[str, str]) -> None:
    """The Markdown rule is on the .md suffix, so notes_md.py is code."""
    assert app_changed(repo, repo["markdown_and_backend"], repo["markdown_lookalike"])


def test_a_backend_change_restarts_the_server(repo: dict[str, str]) -> None:
    assert app_changed(repo, repo["markdown_lookalike"], repo["backend"])


def test_a_backend_change_hidden_in_a_site_commit_still_restarts_it(repo: dict[str, str]) -> None:
    """The direction that costs more. A skipped restart means the code is on
    main, the log says recorded, and production is running the old thing."""
    assert app_changed(repo, repo["backend"], repo["site_and_backend"])


def test_a_file_outside_the_ignored_paths_counts_as_a_change(repo: dict[str, str]) -> None:
    """Ignored is a list, not a guess: a path the list has never heard of is
    the server's, because the failure direction there is one harmless redeploy."""
    assert app_changed(repo, repo["site_and_backend"], repo["unknown"])
    assert app_changed(repo, repo["unknown"], repo["deploy"])


def test_a_folder_that_only_starts_with_an_ignored_name_counts_as_a_change(repo: dict[str, str]) -> None:
    """Exclusions match whole directory names: site-old/ is not site/."""
    assert app_changed(repo, repo["deploy"], repo["lookalike"])


def test_the_pathspecs_are_not_globbed_by_the_shell(repo: dict[str, str], tmp_path: Path) -> None:
    """*.md must reach git as a pathspec. Expanded by the shell in a directory
    holding one.md, it would exclude only one.md and a change to any other
    Markdown file would restart the server."""
    (tmp_path / "one.md").write_text("x")
    assert not app_changed(repo, repo["lookalike"], repo["other_markdown"], cwd=tmp_path)


def test_an_empty_list_means_every_change_deploys(repo: dict[str, str]) -> None:
    """product.settings.example documents "empty: every change deploys". Under
    the loop's `set -u` an empty array expansion is an unbound-variable error
    on bash before 4.4 (macOS ships 3.2), which ends the run and reads as
    "nothing changed"."""
    assert app_changed(repo, repo["base"], repo["site"], ignore="")
    assert app_changed(repo, repo["site"], repo["docs"], ignore="")


def test_a_first_deploy_always_counts_as_a_change(repo: dict[str, str]) -> None:
    """Nothing to diff against, so there is nothing to be sure about."""
    assert app_changed(repo, "", repo["backend"])


def test_a_sha_this_clone_has_never_seen_counts_as_a_change(repo: dict[str, str]) -> None:
    """The fetch stub fails, so this is the ambiguous case: a missing object
    must not be read as "nothing changed"."""
    assert app_changed(repo, repo["base"], "0" * 40)


def test_a_marker_that_has_gone_missing_counts_as_a_change(repo: dict[str, str]) -> None:
    assert app_changed(repo, "0" * 40, repo["backend"])


def test_a_change_outside_app_paths_does_not_restart_the_server(repo: dict[str, str]) -> None:
    """APP_PATHS is an include list: a commit touching none of it is not the app."""
    assert not app_changed(repo, repo["other_markdown"], repo["outside_paths"], paths="backend/ deploy/")


def test_a_change_inside_app_paths_restarts_the_server(repo: dict[str, str]) -> None:
    assert app_changed(repo, repo["outside_paths"], repo["inside_paths"], paths="backend/ deploy/")


def test_an_ignored_path_inside_app_paths_does_not_restart_the_server(repo: dict[str, str]) -> None:
    """Both lists combine: APP_IGNORE_PATHS still excludes from within APP_PATHS."""
    assert not app_changed(repo, repo["inside_paths"], repo["inside_but_ignored"], paths="backend/ deploy/")


def test_blank_app_paths_means_the_whole_tree(repo: dict[str, str]) -> None:
    """Empty or blank APP_PATHS keeps today's behaviour, also under set -u."""
    assert app_changed(repo, repo["other_markdown"], repo["outside_paths"], paths="")
    assert app_changed(repo, repo["other_markdown"], repo["outside_paths"], paths="  ")


def tick(repo: dict[str, str], tmp_path: Path, current: str, sha: str, paths: str) -> str:
    """One tick of the shipped loop, every step but app_changed and the failure alert stubbed.

    The state and alert files live in tmp_path, so a second call is the next tick.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    state = tmp_path / "deployed-sha"
    if not state.exists():
        state.write_text(current + "\n")
    harness = (
        "set -euo pipefail\n"
        f"SRC={shlex.quote(repo['path'])}\nBRANCH=main\nAPP_IGNORE_PATHS={shlex.quote(IGNORE)}\n"
        f"APP_PATHS={shlex.quote(paths)}\nSTATE={shlex.quote(str(state))}\n"
        f"ALERTED={shlex.quote(str(tmp_path / 'alerted-sha'))}\n"
        'POLL=90\nPRODUCT=product\nCOMPOSE_PROJECT=product\nFAILED_STEP=""\n'
        'log() { echo "[deployer] $*"; }; alert() { echo "[alert] $*"; }; git_authed() { return 1; }\n'
        "check_ci() { :; }; check_site() { :; }; check_self() { :; }; ci_green() { :; }\n"
        f"latest_sha() {{ echo {sha}; }}\n"
        'deploy() { echo "DEPLOYED $1"; echo "$1" >"$STATE"; }\n'
        "notify_no_app_change() { echo SKIPPED; }; mark_site_pending() { :; }\n"
        # The loop's last step ends the run, so exactly one tick runs.
        "sleep() { exit 0; }\n"
        + assignment(LOOP, "EMPTY_TREE")
        + "\n"
        + lift(LOOP, "app_changed")
        + lift(LOOP, "notify_failure")
        + block(LOOP, "while true; do", "done", ceiling=45)
    )
    done = subprocess.run(["bash", "-c", harness], capture_output=True, text=True, check=False, env=git_env())
    assert done.returncode == 0, done.stdout + done.stderr
    return done.stdout


def test_an_app_path_that_matches_nothing_fails_the_attempt(repo: dict[str, str], tmp_path: Path) -> None:
    """A typo or a renamed folder would leave every diff empty: every sha
    recorded as "no app change" and the server never redeployed, silently.
    It fails like any other step instead: alerted once, not recorded, retried."""
    current, sha = repo["inside_paths"], repo["inside_but_ignored"]
    first = tick(repo, tmp_path, current, sha, paths="backend/ backnd/")
    second = tick(repo, tmp_path, current, sha, paths="backend/ backnd/")
    assert f"APP_PATHS pathspec backnd/ matches no file at {sha}" in first
    assert (
        f"deploy of {sha} FAILED at APP_PATHS" in first and f"deploy of {sha} FAILED at APP_PATHS" in second
    )
    assert "SKIPPED" not in first + second and "DEPLOYED" not in first + second
    assert (first + second).count("[alert]") == 1 and "[alert] product deploy FAILED at APP_PATHS" in first
    assert (tmp_path / "deployed-sha").read_text().strip() == current


def test_app_paths_that_all_match_decide_as_before(repo: dict[str, str], tmp_path: Path) -> None:
    out = tick(
        repo, tmp_path / "skip", repo["other_markdown"], repo["outside_paths"], paths="backend/ deploy/"
    )
    assert "SKIPPED" in out and "[alert]" not in out
    out = tick(
        repo, tmp_path / "deploy", repo["outside_paths"], repo["inside_paths"], paths="backend/ deploy/"
    )
    assert f"DEPLOYED {repo['inside_paths']}" in out and "[alert]" not in out


@pytest.mark.parametrize("paths", ["", "  "])
def test_blank_app_paths_decide_as_before(repo: dict[str, str], tmp_path: Path, paths: str) -> None:
    out = tick(repo, tmp_path, repo["other_markdown"], repo["outside_paths"], paths=paths)
    assert f"DEPLOYED {repo['outside_paths']}" in out and "[alert]" not in out


def test_an_app_path_added_in_the_new_commit_counts(repo: dict[str, str], tmp_path: Path) -> None:
    """worker/ does not exist at the deployed sha, only at the new one: the
    check reads the tree of the sha under consideration, not the checkout."""
    out = tick(repo, tmp_path, repo["inside_but_ignored"], repo["new_folder"], paths="backend/ worker/")
    assert f"DEPLOYED {repo['new_folder']}" in out and "[alert]" not in out


def test_a_glob_app_path_that_matches_passes(repo: dict[str, str], tmp_path: Path) -> None:
    """*.md reaches git as a pathspec and matches Markdown anywhere in the tree."""
    out = tick(repo, tmp_path, repo["outside_paths"], repo["inside_paths"], paths="*.md backend/")
    assert f"DEPLOYED {repo['inside_paths']}" in out and "[alert]" not in out


def test_a_glob_app_path_that_matches_nothing_fails_the_attempt(repo: dict[str, str], tmp_path: Path) -> None:
    current, sha = repo["outside_paths"], repo["inside_paths"]
    out = tick(repo, tmp_path, current, sha, paths="*.zzz")
    assert f"APP_PATHS pathspec *.zzz matches no file at {sha}" in out
    assert "[alert] product deploy FAILED at APP_PATHS" in out
    assert "DEPLOYED" not in out and "SKIPPED" not in out
    assert (tmp_path / "deployed-sha").read_text().strip() == current
