"""The Gitleaks rules this repository adds fire, and the allow comment does not silence them.

Fixtures are assembled from parts so this file never carries a secret-shaped
line of its own; CI scans it too. Gitleaks is required, not optional: a
missing scanner fails here rather than skipping.
"""

from __future__ import annotations

import base64
import json
import random
import re
import shutil
import string
import subprocess
import urllib.parse
from pathlib import Path
from typing import Any

import pytest

from conftest import LIBRARY_ROOT

CONFIG = LIBRARY_ROOT / ".gitleaks.toml"


def body(length: int, seed: int, alphabet: str = string.ascii_letters + string.digits) -> str:
    # One generator per body: a fresh Random(seed) per character repeats one
    # letter, and a run of one letter has no entropy for the rules to see.
    generator = random.Random(seed)
    return "".join(generator.choice(alphabet) for _ in range(length))


def scan(directory: Path) -> list[dict[str, Any]]:
    if shutil.which("gitleaks") is None:
        pytest.fail("gitleaks is required for the rule tests; verification never skips the secret scan")
    report = directory.parent / "report.json"
    subprocess.run(
        [
            "gitleaks",
            "dir",
            str(directory),
            "--no-banner",
            "--redact",
            "--ignore-gitleaks-allow",
            "--config",
            str(CONFIG),
            "--report-format",
            "json",
            "--report-path",
            str(report),
        ],
        check=False,
        capture_output=True,
    )
    return json.loads(report.read_text() or "[]")


def findings(directory: Path) -> set[tuple[str, str]]:
    return {(item["RuleID"], Path(item["File"]).name) for item in scan(directory)}


def test_the_added_rules_fire_and_an_allow_comment_does_not_silence_them(tmp_path: Path) -> None:
    files = tmp_path / "files"
    files.mkdir()
    # Variable names are split too: a scanner reads `secret = "..."` as a
    # secret whatever the value.
    resend_name = "resend_" + "api_key"
    razorpay_name = "RAZORPAY_KEY_" + "SECRET"
    password_name = "pass" + "word"
    # Apart from "://" in a variable: Python folds adjacent constants, and the
    # compiled module would then carry a whole URI prefix of its own.
    scheme = "postgres" + "ql"
    samples = {
        "supabase_secret.py": f'value = "{"sb_" + "secret_" + body(22, 1) + "_" + body(8, 2)}"\n',
        "supabase_pat.py": f'value = "{"sbp_" + body(40, 3, "0123456789abcdef")}"\n',
        "resend.py": f'{resend_name} = "{"re_" + body(8, 4) + "_" + body(30, 5)}"\n',
        "razorpay.py": f'{razorpay_name} = "{body(24, 6)}"\n',
        "uri.py": f'url = "{scheme}://app:{body(14, 7)}@db.invalid/app"\n',
        "assignment.py": f'{password_name} = "{body(12, 8)}"\n',
        "sk.py": f'value = "{"sk-" + "ant-" + body(40, 9)}"  # gitleaks:allow\n',
        "google.py": f'value = "{"ya29." + body(40, 10)}"\n',
        "clean.py": 'url = "postgresql://app:${DB_PASSWORD}@db.invalid/app"\n',
    }
    for name, text in samples.items():
        (files / name).write_text(text)
    found = findings(files)
    expected = {
        ("supabase-secret-key", "supabase_secret.py"),
        ("supabase-personal-access-token", "supabase_pat.py"),
        ("resend-api-key", "resend.py"),
        ("razorpay-key-secret", "razorpay.py"),
        ("uri-embedded-credentials", "uri.py"),
        ("quoted-secret-assignment", "assignment.py"),
        ("sk-prefixed-key", "sk.py"),
        ("google-oauth-access-token", "google.py"),
    }
    assert expected <= found, f"missing: {sorted(expected - found)}"
    assert not any(name == "clean.py" for _, name in found), "a URI built from a variable is not a secret"


def test_the_one_allowlist_exempts_only_checksum_lines_in_a_podfile_lock(tmp_path: Path) -> None:
    """A SPEC CHECKSUMS line (two-space indent, pod name, 40 lowercase hex) is
    allowed in a file named Podfile.lock; the same line elsewhere, another
    indent, and any other line in that file are still reported."""
    files = tmp_path / "files"
    files.mkdir()
    hex_digits = "0123456789abcdef"
    checksums = [
        f"  RNKeychain: {body(40, 11, hex_digits)}",
        f"  React-cxxstableapi: {body(40, 12, hex_digits)}",
    ]
    key_name = "api" + "_key"
    lock = [
        "SPEC CHECKSUMS:",
        *checksums,
        f"RNKeychain: {body(40, 13, hex_digits)}",
        f"    React-cxxstableapi: {body(40, 14, hex_digits)}",
        f'{key_name} = "{body(32, 15)}"',
        # The line must end at the 40th hex digit: trailing text means it is
        # not a podspec checksum.
        f"  React-cxxstableapi: {body(40, 17, hex_digits)} trailing",
    ]
    (files / "Podfile.lock").write_text("\n".join(lock) + "\n")
    (files / "notes.txt").write_text("\n".join(checksums) + "\n")
    reported = {(Path(item["File"]).name, item["StartLine"]) for item in scan(files)}
    assert {("notes.txt", 1), ("notes.txt", 2)} <= reported, "the checksum shape outside Podfile.lock"
    assert not {("Podfile.lock", 2), ("Podfile.lock", 3)} & reported, "SPEC CHECKSUMS lines are allowed"
    others = {("Podfile.lock", line) for line in range(4, 8)}
    assert others <= reported, f"missing: {sorted(others - reported)}"


def test_an_encoded_key_beside_a_checksum_line_is_still_reported(tmp_path: Path) -> None:
    """For a decoded finding the allowlist sees the decoded text, which can
    hold several lines; one checksum-shaped line in it must not exempt a key
    encoded with it, base64 or percent-encoded."""
    files = tmp_path / "files"
    files.mkdir()
    checksum = f"  React-cxxstable{'ap'}i: {body(40, 3, '0123456789abcdef')}"
    token_name = "client_auth" + "_token"
    blob = f"\n{checksum}\n{token_name}: {body(32, 21)}\n"
    lock = [
        "PODS:",
        f"  blob: {base64.b64encode(blob.encode()).decode()}",
        f'  :git => "https://x.example/p?q={urllib.parse.quote(blob, safe="")}"',
        checksum,
    ]
    (files / "Podfile.lock").write_text("\n".join(lock) + "\n")
    reported = {item["StartLine"] for item in scan(files) if Path(item["File"]).name == "Podfile.lock"}
    assert {2, 3} <= reported, "an encoded key is exempt because a checksum line is decoded with it"
    assert 4 not in reported, "the plain checksum line stays allowed"


def test_every_scan_ignores_inline_allow_comments_and_refuses_an_ignore_file() -> None:
    """A `gitleaks:allow` comment is not a review, and .gitleaksignore silently
    honours fingerprints; the config's allowlist is the one place an
    exemption may live, with its reason."""
    sources = {
        "justfile": (LIBRARY_ROOT / "justfile").read_text(encoding="utf-8"),
        "ci.yml": (LIBRARY_ROOT / ".github" / "workflows" / "ci.yml").read_text(encoding="utf-8"),
    }
    for name, text in sources.items():
        scans = [
            line
            for line in text.splitlines()
            if not line.lstrip().startswith("#") and re.search(r"\bgitleaks (git|dir) ", line)
        ]
        assert scans, f"{name} runs no gitleaks scan"
        for line in scans:
            assert "--ignore-gitleaks-allow" in line and "--config .gitleaks.toml" in line, (
                f"{name}: {line.strip()}"
            )
        assert "test ! -e .gitleaksignore" in text, f"{name} does not refuse a .gitleaksignore"
