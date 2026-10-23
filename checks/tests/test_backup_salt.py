"""backup.sh, logarchive.sh and restore.sh refuse a BACKUP_HMAC_SALT that is not exactly 8 bytes.

openssl keys the .hmac sidecars on at most 8 bytes of the salt and drops the
rest (test_openssl_keeps_only_8_bytes_of_the_salt is the positive control),
so a longer salt silently means less than it says and two salts that share
8 bytes are one key. Each script is run for real up to its check, with fake
tools that would show it went further: a backup written, an archive
written, or a dump handed to psql.
"""

from __future__ import annotations

import os
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import LIBRARY_ROOT, ProductSettings
from tests.shell import fake
from tests.test_deploy_backup_restore import PASSPHRASE

BACKUP = LIBRARY_ROOT / "deploy" / "backup" / "backup.sh"
ARCHIVE = LIBRARY_ROOT / "deploy" / "backup" / "logarchive.sh"
RESTORE = LIBRARY_ROOT / "deploy" / "restore.sh"
WRONG = ["short", "nine-byte", "a-salt-of-many-bytes"]


def refused(result: subprocess.CompletedProcess) -> None:
    assert result.returncode not in (0, -15), (
        f"exit {result.returncode}; the script went on:\n{result.stderr}"
    )
    assert "BACKUP_HMAC_SALT" in result.stderr, f"the refusal does not name the setting:\n{result.stderr}"


def test_the_example_salt_is_8_bytes() -> None:
    """A product that copies the example must not start with a salt every script refuses."""
    text = (LIBRARY_ROOT / "deploy" / "product.settings.example").read_text(encoding="utf-8")
    (line,) = [x for x in text.splitlines() if x.startswith("BACKUP_HMAC_SALT=")]
    assert len(line.split("=", 1)[1].strip("\"'").encode()) == 8, line


@pytest.mark.parametrize("salt", WRONG)
def test_backup_refuses_a_salt_that_is_not_8_bytes(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], salt: str
) -> None:
    env = product_settings()
    bin_dir = tmp_path / "bin"
    fake(bin_dir, "pg_dump", "head -c 4096 /dev/urandom\n")
    fake(bin_dir, "sleep", "kill $PPID\n")
    out = tmp_path / "backups"
    out.mkdir()
    result = subprocess.run(
        ["bash", str(BACKUP)],
        env={
            **os.environ,
            **env.values,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "BACKUP_PASSPHRASE": PASSPHRASE,
            "BACKUP_HMAC_SALT": salt,
            "BACKUP_ROOT": str(out),
            "BACKUP_HEARTBEAT": str(tmp_path / "beat"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    refused(result)
    assert not list(out.iterdir()), "a dump was written under a refused salt"


@pytest.mark.parametrize("salt", WRONG)
def test_logarchive_refuses_a_salt_that_is_not_8_bytes(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], salt: str
) -> None:
    env = product_settings()
    logs = tmp_path / "docker-logs" / ("a" * 64)
    logs.mkdir(parents=True)
    (logs / "config.v2.json").write_text(f'{{"Name":"/{env.values["COMPOSE_PROJECT"]}-api-1"}}')
    (logs / f"{'a' * 64}-json.log").write_text("line\n")
    out = tmp_path / "log-archive"
    out.mkdir()
    (tmp_path / "stage").mkdir()
    result = subprocess.run(
        ["bash", str(ARCHIVE), "once"],
        env={
            **os.environ,
            **env.values,
            "BACKUP_PASSPHRASE": PASSPHRASE,
            "BACKUP_HMAC_SALT": salt,
            "LOG_ARCHIVE_ROOT": str(out),
            "LOG_ARCHIVE_STAGE": str(tmp_path / "stage"),
            "DOCKER_LOGS": str(tmp_path / "docker-logs"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    refused(result)
    assert not list(out.iterdir()), "an archive was written under a refused salt"


@pytest.mark.parametrize("salt", WRONG)
def test_restore_refuses_a_salt_that_is_not_8_bytes(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], salt: str
) -> None:
    """Refused for the salt itself, not later for a sidecar that cannot match."""
    env = product_settings(BACKUP_HMAC_SALT=salt)
    dump = tmp_path / f"{env.values['BACKUP_PREFIX']}-20260101-000000.sql.gz.enc"
    dump.write_bytes(b"x" * 2048)
    Path(f"{dump}.hmac").write_text("0" * 64 + "\n")
    received = tmp_path / "psql-stdin"
    bin_dir = tmp_path / "bin"
    fake(bin_dir, "docker", f'cat >"{received}"\n')
    result = subprocess.run(
        ["bash", str(RESTORE), str(dump)],
        env={
            **os.environ,
            "PATH": f"{bin_dir}:{os.environ['PATH']}",
            "PRODUCT_SETTINGS": str(env.path),
            "BACKUP_PASSPHRASE": PASSPHRASE,
            "TMPDIR": str(tmp_path),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    refused(result)
    assert "does not match its .hmac sidecar" not in result.stderr, (
        "refused by the sidecar, not by the salt check"
    )
    assert not received.exists()
