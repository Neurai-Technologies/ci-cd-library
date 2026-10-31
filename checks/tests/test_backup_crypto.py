"""The shared source-only API preserves the historical backup wire format."""

from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from conftest import LIBRARY_ROOT
from tests.shell import fake

CRYPTO = LIBRARY_ROOT / "deploy" / "backup" / "crypto.sh"
PASSPHRASE = "disposable encryption exercise"
SALT = "fakesalt"
DATA = b"arbitrary archive bytes\x00\xff\n" * 200


def invoke(function: str, *args: Path | str, data: bytes | None = None, **env: str) -> subprocess.CompletedProcess:
    """Call the actual source-only module, without any database configuration."""
    return subprocess.run(
        ["bash", "-c", '. "$1"; shift; "$@"', "crypto-test", str(CRYPTO), function, *map(str, args)],
        input=data,
        env={**os.environ, "BACKUP_PASSPHRASE": PASSPHRASE, "BACKUP_HMAC_SALT": SALT, **env},
        capture_output=True,
        check=False,
        timeout=15,
    )


def legacy_pair(path: Path, data: bytes = DATA) -> None:
    """Build the old format independently of the new helper, with fixture secrets."""
    result = subprocess.run(
        ["openssl", "enc", "-aes-256-cbc", "-pbkdf2", "-salt", "-pass", "env:BACKUP_PASSPHRASE"],
        input=data,
        env={**os.environ, "BACKUP_PASSPHRASE": PASSPHRASE},
        capture_output=True,
        check=True,
    )
    path.write_bytes(result.stdout)
    # Old mac_key() uses the first 32 bytes OpenSSL derives for AES-256-CBC.
    key = hashlib.pbkdf2_hmac("sha256", PASSPHRASE.encode(), SALT.encode(), 10000, dklen=48)[:32]
    Path(f"{path}.hmac").write_text(hmac.new(key, result.stdout, "sha256").hexdigest() + "\n")


def test_import_is_side_effect_free_and_requires_no_settings(tmp_path: Path) -> None:
    result = subprocess.run(
        ["bash", "-c", '. "$1"', "import-test", str(CRYPTO)],
        cwd=tmp_path,
        env={"PATH": os.environ["PATH"]},
        capture_output=True,
        check=False,
    )
    assert result.returncode == 0 and not result.stdout and not result.stderr
    assert list(tmp_path.iterdir()) == []


def test_old_ciphertext_decrypts_and_new_ciphertext_uses_old_algorithm(tmp_path: Path) -> None:
    old = tmp_path / "old.enc"
    legacy_pair(old)
    assert invoke("backup_verify", old, TMPDIR=str(tmp_path)).returncode == 0
    decoded = invoke("backup_decrypt", old, TMPDIR=str(tmp_path))
    assert decoded.returncode == 0 and decoded.stdout == DATA, decoded.stderr
    new = tmp_path / "new file.enc"
    encrypted = invoke("backup_encrypt", new, data=DATA)
    assert encrypted.returncode == 0, encrypted.stderr
    key = hashlib.pbkdf2_hmac("sha256", PASSPHRASE.encode(), SALT.encode(), 10000, dklen=48)[:32]
    assert Path(f"{new}.hmac").read_text().strip() == hmac.new(key, new.read_bytes(), "sha256").hexdigest()
    result = subprocess.run(
        ["openssl", "enc", "-d", "-aes-256-cbc", "-pbkdf2", "-pass", "env:BACKUP_PASSPHRASE", "-in", str(new)],
        env={**os.environ, "BACKUP_PASSPHRASE": PASSPHRASE}, capture_output=True, check=True,
    )
    assert result.stdout == DATA
    assert new.stat().st_mode & 0o777 == 0o600
    assert Path(f"{new}.hmac").stat().st_mode & 0o777 == 0o600
    assert not list(tmp_path.glob("*crypto*"))


@pytest.mark.parametrize("damage", ["missing", "empty", "malformed", "wrong_mac", "modified", "truncated", "password", "salt"])
def test_bad_authentication_emits_no_plaintext_or_decrypt_call(tmp_path: Path, damage: str) -> None:
    path = tmp_path / "archive.enc"
    legacy_pair(path)
    sidecar = Path(f"{path}.hmac")
    env = {}
    if damage == "missing":
        sidecar.unlink()
    elif damage == "empty":
        sidecar.write_text("")
    elif damage == "malformed":
        sidecar.write_text("z" * 64 + "\n")
    elif damage == "wrong_mac":
        sidecar.write_text("0" * 64 + "\n")
    elif damage == "modified":
        path.write_bytes(path.read_bytes()[:30] + b"changed" + path.read_bytes()[37:])
    elif damage == "truncated":
        path.write_bytes(path.read_bytes()[:-1])
    elif damage == "password":
        env["BACKUP_PASSPHRASE"] = "wrong fixture"
    elif damage == "salt":
        env["BACKUP_HMAC_SALT"] = "othersal"
    called = tmp_path / "decrypt-called"
    real = shutil.which("openssl")
    assert real
    fake(tmp_path / "bin", "openssl", f'case " $* " in *" -d "*) touch "{called}" ;; esac\nexec "{real}" "$@"\n')
    result = invoke("backup_decrypt", path, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}", TMPDIR=str(tmp_path), **env)
    assert result.returncode != 0 and not result.stdout, result.stderr
    assert not called.exists(), "decryption was invoked before authentication succeeded"
    assert not list(tmp_path.glob("backup-crypto.*"))


@pytest.mark.parametrize("function", ["backup_encrypt", "backup_verify", "backup_decrypt"])
@pytest.mark.parametrize("setting,value", [("BACKUP_PASSPHRASE", ""), ("BACKUP_HMAC_SALT", "short")])
def test_bad_configuration_fails_closed(tmp_path: Path, function: str, setting: str, value: str) -> None:
    result = invoke(function, tmp_path / "new.enc", data=DATA, **{setting: value})
    assert result.returncode != 0 and not result.stdout
    assert list(tmp_path.iterdir()) == []


@pytest.mark.parametrize("failure", ["encrypt", "mac", "signal"])
def test_failed_or_interrupted_encrypt_publishes_no_pair(tmp_path: Path, failure: str) -> None:
    real = shutil.which("openssl")
    assert real
    if failure == "mac":
        body = f'case " $* " in *" dgst "*) exit 1 ;; esac\nexec "{real}" "$@"\n'
    else:
        body = 'if [ "$1" = enc ]; then\n  while [ "$1" != -out ]; do shift; done\n  printf partial >"$2"\n'
        body += '  kill -TERM "$PPID"\n' if failure == "signal" else ""
        body += "  exit 1\nfi\n"
    fake(tmp_path / "bin", "openssl", body)
    result = invoke("backup_encrypt", tmp_path / "new.enc", data=DATA, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    assert result.returncode != 0, result.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bin"]


def test_existing_output_is_not_overwritten(tmp_path: Path) -> None:
    path = tmp_path / "kept.enc"
    path.write_bytes(b"kept")
    result = invoke("backup_encrypt", path, data=DATA)
    assert result.returncode != 0 and path.read_bytes() == b"kept"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["kept.enc"]


def test_decrypt_uses_the_authenticated_snapshot_not_a_changed_source(tmp_path: Path) -> None:
    path = tmp_path / "archive.enc"
    legacy_pair(path)
    real = shutil.which("openssl")
    assert real
    fake(tmp_path / "bin", "openssl", f'case " $* " in *" -d "*) printf changed >"{path}" ;; esac\nexec "{real}" "$@"\n')
    result = invoke("backup_decrypt", path, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}", TMPDIR=str(tmp_path))
    assert result.returncode == 0 and result.stdout == DATA, result.stderr
    assert path.read_bytes() == b"changed"
    assert not list(tmp_path.glob("backup-crypto.*"))


@pytest.mark.parametrize("publication", ["sidecar", "ciphertext"])
def test_signal_immediately_after_publication_removes_only_our_links(tmp_path: Path, publication: str) -> None:
    fake(tmp_path / "bin", "ln", f'/bin/ln "$@" || exit $?\ncase "$1" in */{publication}) kill -TERM "$PPID" ;; esac\n')
    result = invoke("backup_encrypt", tmp_path / "new.enc", data=DATA, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    assert result.returncode != 0, result.stderr
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bin"]


def test_publication_failure_does_not_remove_a_competing_output(tmp_path: Path) -> None:
    fake(tmp_path / "bin", "ln", '/bin/ln "$@" || exit $?\ncase "$1" in */sidecar) printf competing >"${2%.hmac}" ;; esac\n')
    out = tmp_path / "new.enc"
    result = invoke("backup_encrypt", out, data=DATA, PATH=f"{tmp_path / 'bin'}:{os.environ['PATH']}")
    assert result.returncode != 0, result.stderr
    assert out.read_bytes() == b"competing"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bin", "new.enc"]


def test_authenticated_but_undecryptable_ciphertext_emits_no_plaintext(tmp_path: Path) -> None:
    path = tmp_path / "bad-padding.enc"
    legacy_pair(path)
    truncated = path.read_bytes()[:-1]
    path.write_bytes(truncated)
    key = hashlib.pbkdf2_hmac("sha256", PASSPHRASE.encode(), SALT.encode(), 10000, dklen=48)[:32]
    Path(f"{path}.hmac").write_text(hmac.new(key, truncated, "sha256").hexdigest() + "\n")
    assert invoke("backup_verify", path, TMPDIR=str(tmp_path)).returncode == 0
    result = invoke("backup_decrypt", path, TMPDIR=str(tmp_path))
    assert result.returncode != 0 and not result.stdout
    assert not list(tmp_path.glob("backup-crypto.*"))
