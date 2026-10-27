"""The library's shell scripts say what they are for, run strict, parse, and agree with their installer.

Every script under deploy/ runs against production with the product's
settings in its environment, from an image, a LaunchAgent or an operator's
terminal. These guards hold what a reader cannot see at a glance: a missing
explanation, a script that runs without strict flags, one that no longer
parses, and an installer whose checks no longer match the script it installs.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path

import pytest

from conftest import LIBRARY_ROOT

SCRIPTS = sorted((LIBRARY_ROOT / "deploy").rglob("*.sh"))
VM = LIBRARY_ROOT / "deploy" / "vm"


def test_the_scripts_are_found() -> None:
    assert len(SCRIPTS) >= 8, [p.name for p in SCRIPTS]


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: str(p.relative_to(LIBRARY_ROOT)))
def test_every_script_says_what_it_is_for_and_runs_strict(script: Path) -> None:
    lines = script.read_text(encoding="utf-8").splitlines()
    assert lines[0] in ("#!/bin/bash", "#!/bin/sh", "#!/usr/bin/env bash"), f"{script.name}: {lines[0]!r}"
    assert any(line.startswith("#") and not line.startswith("#!") for line in lines[:6]), (
        f"{script.name} has no leading comment saying what it does"
    )
    assert any(re.match(r"\s*set -[a-z]*[eu]", line) for line in lines[:40]), (
        f"{script.name} sets no strict shell flags (set -e / set -u) near the top"
    )


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: str(p.relative_to(LIBRARY_ROOT)))
def test_every_script_parses(script: Path) -> None:
    done = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True, check=False)
    assert done.returncode == 0, done.stderr


def test_the_installer_fills_every_placeholder_of_the_plist_templates() -> None:
    """install-autostart.sh refuses a rendered plist that still holds a
    placeholder, so a placeholder added to a template without a sed line
    would make every install stop; this says so before an install does."""
    templates = [
        (VM / name).read_text(encoding="utf-8")
        for name in ("launchagent.plist.template", "backup-mirror.plist.template")
    ]
    installer = (VM / "install-autostart.sh").read_text(encoding="utf-8")
    placeholders = {found for template in templates for found in re.findall(r"__[A-Z_]+__", template)}
    filled = set(re.findall(r'-e "s\|(__[A-Z_]+__)\|', installer))
    assert placeholders, "the template has no placeholders; update this test"
    assert placeholders == filled, {"in template": sorted(placeholders), "filled": sorted(filled)}


def test_the_installer_checks_for_guards_the_script_has() -> None:
    """The installer refuses a script that lacks its guards, by name: a guard
    renamed in vm-autostart.sh alone would make every install stop."""
    installer = (VM / "install-autostart.sh").read_text(encoding="utf-8")
    script = (VM / "vm-autostart.sh").read_text(encoding="utf-8")
    required = re.findall(r"grep -q '\^(\w+)\(\)' \"\$WORK/vm-autostart.sh\"", installer)
    assert required, "the installer checks no guard by name; update this test"
    for name in required:
        assert re.search(rf"^{name}\(\) \{{", script, re.MULTILINE), f"vm-autostart.sh has no {name}()"


def test_the_installed_script_reads_the_product_settings_installed_beside_it() -> None:
    """The LaunchAgent runs the installed copy, which sources product.settings from
    its own folder; the installer must put the checked copy exactly there."""
    installer = (VM / "install-autostart.sh").read_text(encoding="utf-8")
    script = (VM / "vm-autostart.sh").read_text(encoding="utf-8")
    assert '. "$(dirname "$0")/product.settings"' in script
    assert 'DEST="$DEST_DIR/vm-autostart.sh"' in installer
    targets = re.findall(r'^(\w+)="\$DEST_DIR/product\.settings"$', installer, re.MULTILINE)
    assert len(targets) == 1, "the installer does not name $DEST_DIR/product.settings once"
    assert re.search(rf'^cp "\$WORK/[^"]+" "\${targets[0]}"$', installer, re.MULTILINE), (
        "the checked copy is not installed beside the script"
    )


def test_the_installer_never_kickstarts_the_agent() -> None:
    """Loading the agent runs the script once (RunAtLoad); a kickstart on top
    would race it against itself."""
    installer = (VM / "install-autostart.sh").read_text(encoding="utf-8")
    code = [line for line in installer.splitlines() if not line.lstrip().startswith("#")]
    assert not any("kickstart" in line for line in code)


def installer_code() -> list[str]:
    text = (VM / "install-autostart.sh").read_text(encoding="utf-8")
    return [line for line in text.splitlines() if not line.lstrip().startswith("#")]


def test_the_installer_refuses_an_origin_off_github_before_it_clones() -> None:
    code = installer_code()
    case = [i for i, line in enumerate(code) if line.strip() == 'case "$ORIGIN_URL" in']
    assert len(case) == 1, "the installer no longer checks ORIGIN_URL"
    allowed = code[case[0] + 1].strip()
    assert allowed.startswith("https://github.com/* | git@github.com:*)"), allowed
    assert "exit 1" in code[case[0] + 2], "an origin off GitHub must stop the run"
    clone = next(i for i, line in enumerate(code) if " clone " in line)
    assert case[0] < clone


def test_the_installer_sources_only_the_clones_settings_after_comparing() -> None:
    """The working tree is writable by sandboxed agents, so its product.settings
    is compared with the clone's and never sourced; only the clone's copy is."""
    code = installer_code()
    compares = [i for i, line in enumerate(code) if re.search(r"\bcmp -s .*product\.settings", line)]
    assert compares, "the installer no longer compares product.settings with the clone"
    sourced = [(i, line) for i, line in enumerate(code) if re.match(r"\s*(\.|source)\s", line)]
    assert sourced, "the installer sources no settings at all"
    for i, line in sourced:
        assert i > compares[0], f"settings sourced before the comparison: {line.strip()}"
        assert '"$WORK/main-' in line, f"sources something other than the clone's copy: {line.strip()}"


def test_origin_url_is_an_installer_argument_not_a_setting() -> None:
    example = (LIBRARY_ROOT / "deploy" / "product.settings.example").read_text(encoding="utf-8")
    assert "ORIGIN_URL" not in example
    for script in SCRIPTS:
        text = script.read_text(encoding="utf-8")
        if script.name == "install-autostart.sh":
            assigned = re.findall(r"^ORIGIN_URL=(.*)$", text, re.MULTILINE)
            assert assigned == ['"${1:?$USAGE}"'], assigned
            continue
        assert "ORIGIN_URL" not in text, f"{script.name} reads ORIGIN_URL"
