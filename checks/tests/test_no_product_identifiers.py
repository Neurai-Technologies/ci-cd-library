"""The library is public: no product, account, host or Mac path appears in what it ships.

Every product-specific value comes from product.settings in the product
repository. This scans every tracked or new, not-ignored file under deploy/,
.github/, checks/ and docs/, case-insensitively, for two kinds of thing:

- generic shapes, kept here: Mac home and volume paths, and any .com
  hostname but the placeholder domain, GitHub's and Apple's (the plist
  DOCTYPE names www.apple.com/DTDs);
- the names of the products, owners and machines that must never appear.
  Those are not written here, because a public file listing them would
  publish them: they come from the environment variable BANNED_WORDS
  (comma-separated) and from checks/banned-words.local (one per line,
  gitignored), whichever are present. Without either, only the generic shapes
  are checked, and the run says so.

.github/CODEOWNERS names owners by handle, so an `@handle` there is not a hit.
"""

from __future__ import annotations

import os
import re
import subprocess
import warnings

import pytest

from conftest import LIBRARY_ROOT
from tests.shell import git_env

SCANNED = ("deploy", ".github", "checks", "docs")
LOCAL_LIST = LIBRARY_ROOT / "checks" / "banned-words.local"
# Assembled from parts so this file does not match itself.
MAC_PATHS = re.compile("|".join(re.escape(p) for p in ("/" + "Users/", "/" + "Volumes/")), re.IGNORECASE)
HOSTNAME = re.compile(r"\b(?:[a-z0-9-]+\.)+com\b", re.IGNORECASE)
ALLOWED_HOSTS = re.compile(r"(?:^|\.)(?:example|github|apple)\.com$", re.IGNORECASE)
HANDLE = re.compile(r"@\S+")


def local_words() -> list[str]:
    """The banned names from BANNED_WORDS and checks/banned-words.local, lower-cased."""
    words = [w.strip() for w in os.environ.get("BANNED_WORDS", "").split(",")]
    if LOCAL_LIST.is_file():
        lines = LOCAL_LIST.read_text(encoding="utf-8").splitlines()
        words += [line.strip() for line in lines if not line.strip().startswith("#")]
    return sorted({w.lower() for w in words if w})


def scanned_files() -> list[str]:
    names = subprocess.run(
        ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z", "--", *SCANNED],
        cwd=LIBRARY_ROOT,
        capture_output=True,
        check=True,
        env=git_env(),
    ).stdout.decode("utf-8")
    return sorted({name for name in names.split("\0") if name and (LIBRARY_ROOT / name).is_file()})


def hits_in(name: str, text: str, words: list[str]) -> list[str]:
    banned = re.compile("|".join(re.escape(w) for w in words), re.IGNORECASE) if words else None
    hits = []
    for number, line in enumerate(text.splitlines(), 1):
        found = [m.group(0) for m in MAC_PATHS.finditer(line)]
        found += [m.group(0) for m in HOSTNAME.finditer(line) if not ALLOWED_HOSTS.search(m.group(0))]
        if banned:
            checked = HANDLE.sub("", line) if name == ".github/CODEOWNERS" else line
            found += [m.group(0) for m in banned.finditer(checked)]
        # The word itself is not repeated in the report: the report may be public too.
        hits += [f"{name}:{number}: a banned {kind(word)}" for word in found]
    return hits


def kind(word: str) -> str:
    if MAC_PATHS.fullmatch(word):
        return "Mac path"
    if HOSTNAME.fullmatch(word):
        return "hostname"
    return "name (from the local word list)"


def test_the_scan_reads_the_library() -> None:
    files = scanned_files()
    for expected in (
        "deploy/deployer/deploy-loop.sh",
        "deploy/product.settings.example",
        "checks/conftest.py",
    ):
        assert expected in files, f"{expected} is not scanned; the scan would pass vacuously"


def test_the_check_catches_each_shape() -> None:
    """The positive control, with a made-up word standing in for a real name."""
    words = ["zorblaxco"]
    assert hits_in("docs/x.md", "made by ZorblaxCo", words)
    assert hits_in("docs/x.md", "see https://app.zorblax" + ".com/login", [])
    assert hits_in("docs/x.md", "cd /" + "Volumes/disk/repo", [])
    assert hits_in("docs/x.md", "@zorblaxco owns this", words)
    assert not hits_in(".github/CODEOWNERS", "/deploy/ @zorblaxco", words)
    assert not hits_in(
        "docs/x.md", "https://app.example.com and https://github.com/x and api.github.com", words
    )


def test_no_scanned_file_names_a_product_owner_or_machine() -> None:
    words = local_words()
    if not words:
        warnings.warn(
            "no local word list (BANNED_WORDS or checks/banned-words.local): "
            "only Mac paths and hostnames were checked",
            stacklevel=1,
        )
    hits = []
    for name in scanned_files():
        try:
            text = (LIBRARY_ROOT / name).read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = (LIBRARY_ROOT / name).read_bytes().decode("latin-1")
        hits += hits_in(name, text, words)
    assert not hits, "product identifiers in the public library:\n" + "\n".join(hits)


def test_the_local_word_list_is_never_committable() -> None:
    done = subprocess.run(
        ["git", "check-ignore", "-q", "--no-index", "checks/banned-words.local"],
        cwd=LIBRARY_ROOT,
        capture_output=True,
        check=False,
    )
    assert done.returncode == 0, "checks/banned-words.local could be committed; it would publish the names"


@pytest.mark.parametrize("value", ["", " , ,", "ZorblaxCo, other-made-up "])
def test_the_environment_list_is_split_and_lower_cased(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("BANNED_WORDS", value)
    monkeypatch.setattr("tests.test_no_product_identifiers.LOCAL_LIST", LIBRARY_ROOT / "no-such-file")
    expected = sorted({w.strip().lower() for w in value.split(",") if w.strip()})
    assert local_words() == expected
