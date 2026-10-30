"""Explicit archive families share the installed mirror, state and volume gate."""

from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from conftest import LIBRARY_ROOT
from tests.shell import fake
from tests.test_backup_mirror import DUMP, Host, new_host, pair

ARCHIVE = "snapshot-20261030-020000.tar.gz.enc"


def configured(tmp_path: Path, records: str | None = None) -> tuple[Host, Path]:
    host = new_host(tmp_path)
    source = tmp_path / "daily archives"
    source.mkdir()
    host.settings(BACKUP_MIRROR_EXTRA_SOURCES=records or f"documents|{source}|snapshot|tar.gz.enc")
    return host, source


def test_multiple_sources_and_duplicate_basenames_stay_separate(tmp_path: Path) -> None:
    host, first = configured(tmp_path)
    second = tmp_path / "another archive source"
    second.mkdir()
    host.settings(BACKUP_MIRROR_EXTRA_SOURCES=f"one|{first}|snapshot|tar.gz.enc\ntwo|{second}|snapshot|tar.gz.enc")
    pair(host.src)
    pair(first, ARCHIVE)
    pair(second, ARCHIVE)
    pair(first / "cache", ARCHIVE)
    pair(first, ARCHIVE + ".writing")
    assert host.run().returncode == 0, host.log
    assert (host.mirror / DUMP).read_bytes() == (host.src / DUMP).read_bytes()
    for namespace, source in [("one", first), ("two", second)]:
        assert sorted(p.name for p in (host.mirror / namespace).iterdir()) == [ARCHIVE, ARCHIVE + ".hmac"]
        assert (host.mirror / namespace / ARCHIVE).read_bytes() == (source / ARCHIVE).read_bytes()
        assert (host.mirror / namespace / ARCHIVE).stat().st_mode & 0o777 == 0o600
    assert host.run().returncode == 0, host.log
    assert "copied 0 files" in host.log
    assert host.alerts == []


@pytest.mark.parametrize("record", ["same|{source}|snapshot|tar.gz.enc\nsame|{source}|other|tar.gz.enc", "Same|{source}|snapshot|tar.gz.enc", "../escape|{source}|snapshot|tar.gz.enc", "good|{source}|star*|tar.gz.enc", "good|{source}|snapshot|anything.enc", "good|relative|snapshot|tar.gz.enc", "good|{source}|snapshot|tar.gz.enc|", "good|{source}|snapshot"])
def test_invalid_or_colliding_families_fail_before_a_copy(tmp_path: Path, record: str) -> None:
    host, source = configured(tmp_path)
    host.settings(BACKUP_MIRROR_EXTRA_SOURCES=record.format(source=source))
    pair(host.src)
    pair(source, ARCHIVE)
    assert host.run().returncode != 0
    assert not host.mirror.exists()
    assert len(host.alerts) == 1 and "FAILED" in host.alerts[0]


def test_missing_extra_source_fails_and_recovery_is_one_global_transition(tmp_path: Path) -> None:
    host, source = configured(tmp_path)
    source.rmdir()
    assert host.run().returncode != 0
    assert not (host.app / "backup-mirror.last-success").exists()
    source.mkdir()
    pair(source, ARCHIVE)
    assert host.run().returncode == 0, host.log
    assert host.run().returncode == 0, host.log
    assert len(host.alerts) == 2 and "recovered" in host.alerts[1]


def test_unmounted_volume_copies_no_extra_family(tmp_path: Path) -> None:
    host, source = configured(tmp_path)
    pair(source, ARCHIVE)
    host.unmount()
    assert host.run().returncode == 0
    assert not host.mirror.exists()
    assert not (host.app / "backup-mirror.last-success").exists()
    assert len(host.alerts) == 1 and "not mounted" in host.alerts[0]


@pytest.mark.parametrize("where", ["source_parent", "source_inside", "source_alias", "namespace_link"])
def test_extra_source_destination_ancestry_and_links_fail(tmp_path: Path, where: str) -> None:
    host, source = configured(tmp_path)
    if where == "source_parent":
        source = host.volume
    elif where == "source_inside":
        source = host.mirror / "input"
        source.mkdir(parents=True)
    elif where == "source_alias":
        alias = tmp_path / "alias"
        alias.symlink_to(host.volume)
        source = alias
    else:
        host.mirror.mkdir()
        (host.mirror / "documents").symlink_to(source)
    host.settings(BACKUP_MIRROR_EXTRA_SOURCES=f"documents|{source}|snapshot|tar.gz.enc")
    pair(source, ARCHIVE)
    assert host.run().returncode != 0
    assert (source / ARCHIVE).exists()
    assert not (host.app / "backup-mirror.last-success").exists()


@pytest.mark.parametrize("kind", ["source_file", "source_dir", "destination_dir"])
def test_writable_by_other_users_is_refused(tmp_path: Path, kind: str) -> None:
    host, source = configured(tmp_path)
    pair(source, ARCHIVE)
    if kind == "source_file":
        (source / ARCHIVE).chmod(0o666)
    elif kind == "source_dir":
        source.chmod(0o777)
    else:
        (host.mirror / "documents").mkdir(parents=True)
        (host.mirror / "documents").chmod(0o777)
    try:
        assert host.run().returncode != 0
        assert not (host.app / "backup-mirror.last-success").exists()
    finally:
        source.chmod(0o755)


def test_family_retention_never_prunes_cache_or_unrelated_names(tmp_path: Path) -> None:
    host, source = configured(tmp_path)
    target = host.mirror / "documents"
    pair(source, ARCHIVE)
    old = "snapshot-20260101-000000.tar.gz.enc"
    pair(target, old, days_old=30)
    untouched = ["notes.txt", "snapshot-keep-this.tar.gz.enc", "different-20260101-000000.tar.gz.enc"]
    for name in untouched:
        pair(target, name, days_old=30)
    pair(target / "cache", old, days_old=30)
    assert host.run().returncode == 0, host.log
    assert not (target / old).exists() and not (target / (old + ".hmac")).exists()
    assert all((target / name).exists() for name in untouched)
    assert (target / "cache" / old).exists()


@pytest.mark.parametrize("damage", ["truncated", "same_size"])
def test_corrupt_existing_copy_fails_without_overwriting(tmp_path: Path, damage: str) -> None:
    host, source = configured(tmp_path)
    pair(source, ARCHIVE)
    assert host.run().returncode == 0, host.log
    target = host.mirror / "documents" / ARCHIVE
    changed = target.read_bytes()[:-1] if damage == "truncated" else b"X" * target.stat().st_size
    target.write_bytes(changed)
    assert host.run().returncode != 0
    assert target.read_bytes() == changed
    assert len(host.alerts) == 1 and "differs" in host.alerts[0]


def test_missing_sidecar_never_publishes_an_extra_bundle(tmp_path: Path) -> None:
    host, source = configured(tmp_path)
    pair(source, ARCHIVE)
    (source / (ARCHIVE + ".hmac")).unlink()
    assert host.run().returncode != 0
    assert list((host.mirror / "documents").iterdir()) == []


def test_temp_destination_symlink_cannot_overwrite_another_file(tmp_path: Path) -> None:
    host, source = configured(tmp_path)
    pair(source, ARCHIVE)
    target = host.mirror / "documents"
    target.mkdir(parents=True)
    outside = tmp_path / "keep"
    outside.write_bytes(b"kept")
    (target / (ARCHIVE + ".hmac.mirroring")).symlink_to(outside)
    assert host.run().returncode != 0
    assert outside.read_bytes() == b"kept"
    assert not (target / ARCHIVE).exists()


@pytest.mark.parametrize("publication", ["hmac", "ciphertext"])
def test_interrupted_publication_removes_only_the_incomplete_pair(tmp_path: Path, publication: str) -> None:
    host, source = configured(tmp_path)
    pair(source, ARCHIVE)
    real_ln = shutil.which("ln", path="/bin:/usr/bin")
    assert real_ln
    pattern = "*.hmac.mirroring" if publication == "hmac" else "*.enc.mirroring"
    fake(host.tmp / "bin", "ln", f'"{real_ln}" "$@" || exit $?\ncase "$1" in {pattern}) kill -TERM "$PPID" ;; esac\n')
    assert host.run().returncode != 0
    assert list((host.mirror / "documents").iterdir()) == []
    assert not (host.app / "backup-mirror.last-success").exists()


def test_changed_copy_bytes_are_detected_before_publication(tmp_path: Path) -> None:
    host, source = configured(tmp_path)
    pair(source, ARCHIVE)
    real_cp = shutil.which("cp", path="/bin:/usr/bin")
    assert real_cp
    fake(host.tmp / "bin", "cp", f'"{real_cp}" "$@" || exit $?\npython3 - "$3" <<\'PY\'\nfrom pathlib import Path\nimport sys\np=Path(sys.argv[1]); b=bytearray(p.read_bytes()); b[0]^=1; p.write_bytes(b)\nPY\n')
    assert host.run().returncode != 0
    assert list((host.mirror / "documents").iterdir()) == []
    assert "differs in content" in host.log


def test_installer_and_template_keep_extra_sources_in_the_same_checked_job() -> None:
    installer = (LIBRARY_ROOT / "deploy/vm/install-autostart.sh").read_text()
    template = (LIBRARY_ROOT / "deploy/vm/backup-mirror.plist.template").read_text()
    assert "BACKUP_MIRROR_EXTRA_SOURCES requires BACKUP_MIRROR_DIR" in installer
    assert 'cp "$WORK/product.settings" "$DEST_ENV"' in installer
    assert "BACKUP_MIRROR_EXTRA_SOURCES" in template
    assert template.count("<key>StartCalendarInterval</key>") == 1
