"""backup-mirror.sh copies the dumps to a second disk, and only there.

The real script runs from a folder of its own with a product.settings beside
it, as the LaunchAgent runs the installed copy. `mount` and `curl` are fakes:
the mount table is a file the test writes, and every alert is recorded
instead of sent.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import pytest

from conftest import LIBRARY_ROOT
from tests.shell import fake

MIRROR_SCRIPT = LIBRARY_ROOT / "deploy" / "vm" / "backup-mirror.sh"
PREFIX = "fakedump"
DUMP = f"{PREFIX}-20260916-030000.sql.gz.enc"


@dataclass
class Host:
    tmp: Path
    app: Path
    src: Path
    volume: Path
    mirror: Path

    @property
    def alerts(self) -> list[str]:
        calls = self.tmp / "alerts"
        return calls.read_text().splitlines() if calls.exists() else []

    @property
    def log(self) -> str:
        return (self.tmp / "home" / "Library" / "Logs" / "fakeproduct-backup-mirror.log").read_text()

    def mount(self) -> None:
        (self.tmp / "mount-table").write_text(f"/dev/disk9s1 on {self.volume} (apfs, local, journaled)\n")

    def unmount(self) -> None:
        (self.tmp / "mount-table").write_text("/dev/disk3s1s1 on / (apfs, sealed, local, read-only)\n")

    def settings(self, **values: str) -> None:
        base = {
            "PRODUCT": "fakeproduct",
            "REPO_DIR": str(self.tmp / "repo"),
            "APP_SUPPORT_DIR": str(self.app),
            "BACKUP_PREFIX": PREFIX,
            "BACKUP_DIR": str(self.src),
            "BACKUP_MIRROR_DIR": str(self.mirror),
            "BACKUP_MIRROR_VOLUME": str(self.volume),
            "BACKUP_MIRROR_KEEP_DAYS": "14",
            **values,
        }
        lines = [f"{key}='{value}'" for key, value in base.items()]
        (self.app / "product.settings").write_text("\n".join(lines) + "\n")

    def run(self) -> subprocess.CompletedProcess:
        env = {
            "PATH": f"{self.tmp / 'bin'}:/usr/bin:/bin:/usr/sbin:/sbin",
            "HOME": str(self.tmp / "home"),
        }
        return subprocess.run(
            ["/bin/bash", str(self.app / "backup-mirror.sh")],
            env=env,
            capture_output=True,
            text=True,
            check=False,
            timeout=60,
        )


def dump(folder: Path, name: str, days_old: int = 0, size: int = 2048) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / name
    path.write_bytes(os.urandom(size))
    if days_old:
        old = time.time() - days_old * 86400
        os.utime(path, (old, old))
    return path


def new_host(tmp_path: Path, src: str = "src", volume: str = "second-disk", mirror: str = "mirror") -> Host:
    h = Host(
        tmp=tmp_path,
        app=tmp_path / "app",
        src=tmp_path / src,
        volume=tmp_path / volume,
        mirror=tmp_path / volume / mirror,
    )
    h.app.mkdir()
    h.src.mkdir()
    h.volume.mkdir()
    shutil.copy(MIRROR_SCRIPT, h.app / "backup-mirror.sh")
    (h.app / "alert-url").write_text("https://alerts.example.com/bot-fake/sendMessage\n")
    (h.app / "alert-url").chmod(0o600)
    fake(tmp_path / "bin", "mount", f'cat "{tmp_path / "mount-table"}"\n')
    fake(
        tmp_path / "bin",
        "curl",
        f'cfg="$(cat)"\nfor a in "$@"; do case "$a" in text=*) printf "%s\\n" "${{a#text=}}" >>"{tmp_path / "alerts"}" ;; esac; done\n'
        f'printf "%s\\n" "$cfg" >>"{tmp_path / "curl-config"}"\n',
    )
    h.settings()
    h.mount()
    return h


@pytest.fixture
def host(tmp_path: Path) -> Host:
    return new_host(tmp_path)


def test_new_dumps_and_sidecars_are_copied_and_nothing_else(host: Host) -> None:
    dump(host.src, DUMP)
    dump(host.src, f"{DUMP}.hmac", size=65)
    dump(host.src, f"{PREFIX}-20260916-090000.sql.gz.enc.part")
    dump(host.src, ".backup.lock", size=0)
    dump(host.src / "logs", "logs-20260916-000000.tar.gz.enc")
    dump(host.src, "notes.txt")

    result = host.run()

    assert result.returncode == 0, host.log
    assert sorted(p.name for p in host.mirror.iterdir()) == [DUMP, f"{DUMP}.hmac"]
    assert (host.mirror / DUMP).read_bytes() == (host.src / DUMP).read_bytes()
    assert (host.mirror / DUMP).stat().st_mtime == pytest.approx((host.src / DUMP).stat().st_mtime, abs=1)
    assert (host.app / "backup-mirror.last-success").exists()


def test_a_copy_the_mirror_has_is_never_replaced(host: Host) -> None:
    dump(host.src, DUMP)
    dump(host.src, f"{DUMP}.hmac", size=65)
    kept = dump(host.mirror, DUMP, size=100).read_bytes()

    assert host.run().returncode != 0, host.log
    assert (host.mirror / DUMP).read_bytes() == kept
    assert "differs from its immutable source" in host.log
    assert not (host.mirror / f"{DUMP}.hmac").exists()
    assert "copied 0 files" in host.log


def test_an_existing_copy_read_error_still_stops_the_run(host: Host) -> None:
    pair(host.src)
    assert host.run().returncode == 0, host.log
    newer = "fakedump-20261031-030000.sql.gz.enc"
    pair(host.src, newer)
    real_cmp = shutil.which("cmp", path="/usr/bin:/bin")
    assert real_cmp
    fake(host.tmp / "bin", "cmp", f'case "$2" in */{DUMP}.hmac) exit 2 ;; esac\nexec "{real_cmp}" "$@"\n')
    assert host.run().returncode != 0
    assert not (host.mirror / newer).exists()
    assert "could not compare existing mirror copy" in host.log


def test_the_prune_takes_only_old_dump_copies(host: Host) -> None:
    old = dump(host.mirror, f"{PREFIX}-20260801-030000.sql.gz.enc", days_old=30)
    old_sidecar = dump(host.mirror, f"{PREFIX}-20260801-030000.sql.gz.enc.hmac", days_old=30)
    recent = dump(host.mirror, f"{PREFIX}-20260910-030000.sql.gz.enc", days_old=5)
    others = [
        dump(host.mirror, "notes.txt", days_old=30),
        dump(host.mirror, "other-20260801-030000.sql.gz.enc", days_old=30),
        dump(host.mirror / "nested", f"{PREFIX}-20260801-030000.sql.gz.enc", days_old=30),
    ]

    assert host.run().returncode == 0, host.log
    assert not old.exists() and not old_sidecar.exists()
    assert recent.exists()
    assert all(p.exists() for p in others), "the prune reached a file that is not this product's dump"


def test_an_unmounted_destination_alerts_once_a_day_and_writes_nothing(host: Host) -> None:
    host.unmount()
    dump(host.src, DUMP)
    dump(host.src, f"{DUMP}.hmac", size=65)

    first, second = host.run(), host.run()

    assert first.returncode == 0 and second.returncode == 0
    assert not host.mirror.exists(), "wrote into the bare mount point"
    assert len(host.alerts) == 1 and "backup mirror: destination not mounted" in host.alerts[0]
    assert host.log.count("destination not mounted") >= 2


def test_a_failed_run_alerts_and_exits_non_zero_then_recovery_alerts_once(host: Host) -> None:
    dump(host.src, DUMP)  # no sidecar: the copy cannot be verified

    failed = host.run()

    assert failed.returncode != 0
    assert len(host.alerts) == 1 and "backup mirror FAILED" in host.alerts[0]
    assert "has no .hmac sidecar" in host.alerts[0]
    assert not (host.app / "backup-mirror.last-success").exists()

    dump(host.src, f"{DUMP}.hmac", size=65)
    assert host.run().returncode == 0, host.log
    assert host.run().returncode == 0, host.log
    assert len(host.alerts) == 2 and "recovered" in host.alerts[1]


def test_the_drive_coming_back_is_a_recovery(host: Host) -> None:
    host.unmount()
    host.run()
    host.mount()
    assert host.run().returncode == 0, host.log
    assert [("not mounted" in a, "recovered" in a) for a in host.alerts] == [(True, False), (False, True)]


def test_success_sends_no_alert(host: Host) -> None:
    dump(host.src, DUMP)
    dump(host.src, f"{DUMP}.hmac", size=65)
    assert host.run().returncode == 0, host.log
    assert host.run().returncode == 0, host.log
    assert host.alerts == []


def test_the_alert_url_travels_by_stdin_not_argv(host: Host) -> None:
    host.unmount()
    host.run()
    assert (
        host.tmp / "curl-config"
    ).read_text().strip() == 'url = "https://alerts.example.com/bot-fake/sendMessage"'


@pytest.mark.parametrize(
    ("mirror", "volume", "reason"),
    [
        ("", "{volume}", "BACKUP_MIRROR_DIR is empty"),
        ("/", "{volume}", "BACKUP_MIRROR_DIR must not be /"),
        ("{src}", "{tmp}", "is BACKUP_DIR or inside it"),
        ("{src}/copies", "{tmp}", "is BACKUP_DIR or inside it"),
        ("{src}/", "{tmp}", "is BACKUP_DIR or inside it"),
        ("{tmp}", "{tmp}", "is inside BACKUP_MIRROR_DIR"),
        ("{volume}", "{volume}", "must be a folder inside BACKUP_MIRROR_VOLUME"),
        ("{volume}/../src/x", "{volume}", "must not contain . or .. parts"),
        ("relative/mirror", "{volume}", "is not an absolute path"),
        ("{volume}/mirror", "", "BACKUP_MIRROR_VOLUME is empty"),
        ("{volume}/mirror", "/", "BACKUP_MIRROR_VOLUME must not be /"),
    ],
)
def test_a_destination_that_could_reach_the_source_is_refused(
    host: Host, mirror: str, volume: str, reason: str
) -> None:
    fill = {"src": str(host.src), "tmp": str(host.tmp), "volume": str(host.volume)}
    host.settings(BACKUP_MIRROR_DIR=mirror.format(**fill), BACKUP_MIRROR_VOLUME=volume.format(**fill))
    (host.tmp / "mount-table").write_text(f"/dev/disk9s1 on {volume.format(**fill)} (apfs, local)\n")
    dump(host.src, DUMP)
    dump(host.src, f"{DUMP}.hmac", size=65)

    result = host.run()

    assert result.returncode != 0
    assert reason in host.log, host.log
    assert len(host.alerts) == 1 and "FAILED" in host.alerts[0]
    assert sorted(p.name for p in host.src.iterdir()) == [DUMP, f"{DUMP}.hmac"]


def test_the_source_inside_the_mirror_is_refused(host: Host) -> None:
    inner = host.mirror / "src"
    host.settings(BACKUP_DIR=str(inner))
    assert host.run().returncode != 0
    assert "is inside BACKUP_MIRROR_DIR" in host.log


def pair(folder: Path, name: str = DUMP, days_old: int = 0) -> None:
    dump(folder, name, days_old=days_old)
    dump(folder, f"{name}.hmac", days_old=days_old, size=65)


@pytest.mark.parametrize("link_at", ["mirror", "parent"])
def test_a_mirror_that_resolves_outside_the_volume_is_refused(host: Host, link_at: str) -> None:
    """The path checks are lexical; a symlink must not carry the copy (or the prune) elsewhere."""
    outside = host.tmp / "system-disk"
    outside.mkdir()
    if link_at == "mirror":
        host.mirror.symlink_to(outside)
        mirror = host.mirror
    else:
        (host.volume / "linked").symlink_to(outside)
        mirror = host.volume / "linked" / "deeper" / "mirror"
    host.settings(BACKUP_MIRROR_DIR=str(mirror))
    pair(host.src)

    assert host.run().returncode != 0
    assert "resolves outside BACKUP_MIRROR_VOLUME" in host.log, host.log
    assert list(outside.iterdir()) == [], "something was written outside the volume"
    assert len(host.alerts) == 1 and "FAILED" in host.alerts[0]


def test_a_mirror_that_resolves_into_the_source_is_refused(host: Host) -> None:
    host.mirror.symlink_to(host.src)
    pair(host.src)

    assert host.run().returncode != 0
    assert "resolves" in host.log, host.log
    assert sorted(p.name for p in host.src.iterdir()) == [DUMP, f"{DUMP}.hmac"]


def test_a_source_past_the_window_is_not_copied_only_to_be_pruned(host: Host) -> None:
    old = f"{PREFIX}-20260801-030000.sql.gz.enc"
    pair(host.src, old, days_old=30)
    pair(host.src)

    assert host.run().returncode == 0, host.log
    assert sorted(p.name for p in host.mirror.iterdir()) == [DUMP, f"{DUMP}.hmac"]
    assert "copied 2 files" in host.log


@pytest.mark.parametrize(
    ("mounted", "configured"),
    [("second-disk", "second-disk 2"), ("second-disk 2", "second-disk")],
)
def test_a_mount_point_that_only_shares_a_prefix_is_not_mounted(
    host: Host, mounted: str, configured: str
) -> None:
    (host.tmp / configured).mkdir(exist_ok=True)
    host.settings(
        BACKUP_MIRROR_VOLUME=str(host.tmp / configured),
        BACKUP_MIRROR_DIR=str(host.tmp / configured / "mirror"),
    )
    (host.tmp / "mount-table").write_text(f"/dev/disk9s1 on {host.tmp / mounted} (apfs, local)\n")
    pair(host.src)

    assert host.run().returncode == 0
    assert "destination not mounted" in host.log
    assert not (host.tmp / configured / "mirror").exists()


def test_an_unfinished_copy_is_cleared(host: Host) -> None:
    leftover = dump(host.mirror, f"{DUMP}.mirroring")
    pair(host.src)

    assert host.run().returncode == 0, host.log
    assert not leftover.exists()
    assert (host.mirror / DUMP).read_bytes() == (host.src / DUMP).read_bytes()


def test_a_copy_of_the_wrong_size_fails_and_alerts(host: Host) -> None:
    fake(host.tmp / "bin", "cp", 'head -c 10 "$2" >"$3"\n')
    pair(host.src)

    assert host.run().returncode != 0
    assert len(host.alerts) == 1 and "differs in size" in host.alerts[0]
    assert not (host.mirror / DUMP).exists() and not (host.mirror / f"{DUMP}.hmac").exists()


def test_a_dump_without_its_sidecar_fails(host: Host) -> None:
    dump(host.src, DUMP)

    assert host.run().returncode != 0
    assert len(host.alerts) == 1 and "has no .hmac sidecar" in host.alerts[0]


def test_paths_with_spaces(tmp_path: Path) -> None:
    host = new_host(tmp_path, src="the backups", volume="Second Disk", mirror="product mirror")
    pair(host.src)

    assert host.run().returncode == 0, host.log
    assert sorted(p.name for p in host.mirror.iterdir()) == [DUMP, f"{DUMP}.hmac"]


def test_a_symlink_named_like_a_dump_is_not_copied(host: Host) -> None:
    """BACKUP_DIR is writable from the containers; a planted link could point at any file of the operator's."""
    secret = dump(host.tmp / "elsewhere", "private.txt")
    (host.src / DUMP).symlink_to(secret)
    (host.src / f"{DUMP}.hmac").symlink_to(secret)
    real = f"{PREFIX}-20260917-030000.sql.gz.enc"
    pair(host.src, real)

    assert host.run().returncode == 0, host.log
    assert sorted(p.name for p in host.mirror.iterdir()) == [real, f"{real}.hmac"]


def test_a_real_dump_with_a_planted_sidecar_link_fails(host: Host) -> None:
    dump(host.src, DUMP)
    (host.src / f"{DUMP}.hmac").symlink_to(dump(host.tmp / "elsewhere", "private.txt"))

    assert host.run().returncode != 0
    assert "has no .hmac sidecar" in host.alerts[0]
    assert not host.mirror.exists() or list(host.mirror.iterdir()) == []


@pytest.mark.parametrize("odd", ["with space", "star*", "two  spaces"])
def test_odd_dump_names_are_copied_as_they_are(host: Host, odd: str) -> None:
    name = f"{PREFIX}-{odd}.sql.gz.enc"
    pair(host.src, name)
    pair(host.src)

    assert host.run().returncode == 0, host.log
    assert sorted(p.name for p in host.mirror.iterdir()) == sorted(
        [name, f"{name}.hmac", DUMP, f"{DUMP}.hmac"]
    )
    assert host.alerts == []


@pytest.mark.parametrize("problem", ["mode 644", "mode 640", "a symlink", "another owner"])
def test_an_alert_url_file_others_could_touch_stops_the_run(host: Host, problem: str) -> None:
    url_file = host.app / "alert-url"
    if problem == "mode 644":
        url_file.chmod(0o644)
    elif problem == "mode 640":
        url_file.chmod(0o640)
    elif problem == "a symlink":
        real = host.tmp / "url-elsewhere"
        url_file.rename(real)
        url_file.symlink_to(real)
    else:
        fake(host.tmp / "bin", "id", "echo 54321\n")
    pair(host.src)

    assert host.run().returncode != 0
    assert "REFUSED" in host.log and "mode 600" in host.log
    assert not (host.tmp / "curl-config").exists(), "the URL was used anyway"
    assert not host.mirror.exists()


def test_without_an_alert_url_file_the_run_goes_on(host: Host) -> None:
    (host.app / "alert-url").unlink()
    pair(host.src)

    assert host.run().returncode == 0, host.log
    assert len(list(host.mirror.iterdir())) == 2


def test_the_log_and_heartbeat_are_owner_only(host: Host) -> None:
    pair(host.src)

    assert host.run().returncode == 0, host.log
    log = host.tmp / "home" / "Library" / "Logs" / "fakeproduct-backup-mirror.log"
    for path in (log, host.app / "backup-mirror.last-success"):
        assert path.stat().st_mode & 0o077 == 0, f"{path.name} is readable by others"


def test_a_dump_swapped_for_a_symlink_at_copy_time_is_not_mirrored(host: Host) -> None:
    """The loop's symlink check and the copy are two steps; a container can swap
    the file in between. This cp does the swap, then runs the real cp."""
    secret = dump(host.tmp / "elsewhere", "private.txt")
    real_cp = shutil.which("cp", path="/bin:/usr/bin")
    assert real_cp
    fake(
        host.tmp / "bin",
        "cp",
        f'src="${{@: -2:1}}"\nrm -f "$src"\nln -s "{secret}" "$src"\nexec {real_cp} "$@"\n',
    )
    pair(host.src)

    assert host.run().returncode != 0
    assert "was not a plain file when copied" in host.alerts[0]
    assert list(host.mirror.iterdir()) == [], "a swapped link reached the mirror"
