"""docs/DEPENDENCIES.md names the release each workflow action is pinned at.

A Dependabot bump moved pnpm/action-setup to v6.1.0 in the workflow while
the "Tools, actions and images" table still said v4.3.0, and nothing
noticed. The table is the record of what the library runs, so each pinned
action's row must name the release its pin's comment records.
"""

from __future__ import annotations

import re

from conftest import LIBRARY_ROOT

# `uses: owner/repo[/path]@<commit> # vX.Y.Z`; the row is named by owner/repo.
PIN = re.compile(r"uses:\s*([\w.-]+/[\w.-]+)(?:/\S+)?@[0-9a-f]{40}\s*#\s*(v\d+\.\d+\.\d+)")
VERSION = re.compile(r"v\d+\.\d+\.\d+")


def pinned_versions() -> dict[str, set[str]]:
    """Each action the workflows pin, with every version its pins record.
    Comment lines (a caller's usage example) are not steps."""
    found: dict[str, set[str]] = {}
    for workflow in sorted((LIBRARY_ROOT / ".github" / "workflows").glob("*.yml")):
        for line in workflow.read_text(encoding="utf-8").splitlines():
            if line.lstrip().startswith("#"):
                continue
            if match := PIN.search(line):
                found.setdefault(match.group(1), set()).add(match.group(2))
    return found


def documented_versions() -> dict[str, str]:
    """The "Version / pin" cell of each row of the "Tools, actions and images" table."""
    text = (LIBRARY_ROOT / "docs" / "DEPENDENCIES.md").read_text(encoding="utf-8")
    section = text.partition("## Tools, actions and images\n")[2].split("\n## ", 1)[0]
    rows = {}
    for line in section.splitlines():
        if line.startswith("|"):
            cells = [cell.strip() for cell in line.strip("|").split("|")]
            rows[cells[0]] = cells[1]
    return rows


def test_every_pinned_action_names_its_version_in_dependencies() -> None:
    pins = pinned_versions()
    assert pins, "no pinned actions found; this guard would pass vacuously"
    rows = documented_versions()
    wrong = {}
    for action, versions in sorted(pins.items()):
        row = rows.get(action, "no row")
        if len(versions) != 1:
            wrong[action] = f"pinned at more than one version: {sorted(versions)}"
            continue
        (version,) = versions
        if VERSION.findall(row) != [version]:
            wrong[action] = f"pinned at {version}, docs/DEPENDENCIES.md says {row!r}"
    assert not wrong, wrong
