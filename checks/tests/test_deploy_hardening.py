"""Settings that could reach outside the clone, the backup folder or the plist are refused.

Each check is a slice of the shipped script run with stubs, never the whole
script: ENV_FILES entries and hook paths at the deploy loop's start, the
split of the *_CMD settings, BACKUP_PREFIX in backup.sh, and the installer's
APP_SUPPORT_DIR and plist-text checks.
"""

from __future__ import annotations

import os
import shlex
import subprocess
from collections.abc import Callable
from pathlib import Path

import pytest

from conftest import LIBRARY_ROOT, ProductSettings
from tests.shell import fake, lift, path_with
from tests.test_deploy_backup_restore import PASSPHRASE

LOOP = LIBRARY_ROOT / "deploy" / "deployer" / "deploy-loop.sh"
BACKUP = LIBRARY_ROOT / "deploy" / "backup" / "backup.sh"
INSTALLER = LIBRARY_ROOT / "deploy" / "vm" / "install-autostart.sh"


def block(script: Path, first: str, last: str, count: int = 1, ceiling: int = 30) -> str:
    """Top-level lines from the one line starting with `first` through the
    `count`-th following line equal to `last`, bounded by `ceiling` lines."""
    lines = script.read_text(encoding="utf-8").splitlines()
    starts = [i for i, line in enumerate(lines) if line.startswith(first)]
    assert len(starts) == 1, f"{script.name}: expected one line starting {first!r}, found {len(starts)}"
    ends = [i for i in range(starts[0], len(lines)) if lines[i] == last]
    assert len(ends) >= count, f"{script.name}: {first!r} block does not close"
    lifted = lines[starts[0] : ends[count - 1] + 1]
    assert len(lifted) <= ceiling, "the slice ran on:\n" + "\n".join(lifted)
    return "\n".join(lifted) + "\n"


# --- ENV_FILES at the loop's start ---------------------------------------------


def env_files_check(value: str) -> subprocess.CompletedProcess:
    code = f"set -euo pipefail\nENV_FILES={shlex.quote(value)}\n" + block(
        LOOP, "read -r -a ENV_FILE_LIST", "done"
    )
    return subprocess.run(
        ["bash", "-c", code + "echo STARTED\n"], capture_output=True, text=True, check=False
    )


@pytest.mark.parametrize("value", [".env app.env", ".env", ""])
def test_plain_env_file_names_start_the_loop(value: str) -> None:
    result = env_files_check(value)
    assert result.returncode == 0 and "STARTED" in result.stdout, result.stderr


@pytest.mark.parametrize("value", [".env sub/app.env", "../app.env", "/etc/passwd"])
def test_an_env_file_entry_with_a_slash_stops_the_loop(value: str) -> None:
    result = env_files_check(value)
    assert result.returncode == 1 and "STARTED" not in result.stdout
    assert "ENV_FILES entries must be file names without a slash" in result.stderr


def test_the_check_runs_before_the_loop_acts() -> None:
    text = LOOP.read_text(encoding="utf-8")
    assert (
        text.index("read -r -a ENV_FILE_LIST")
        < text.index("\nrender_policy ||")
        < text.index("\nensure_clone\n")
    )


# --- the *_CMD settings are split without globbing ---------------------------------


def test_the_migrate_check_command_is_split_without_globbing(
    tmp_path: Path, product_settings: Callable[..., ProductSettings]
) -> None:
    """A `*` in MIGRATE_CHECK_CMD reaches the container literally; expanded, it
    would turn into the names of whatever sits in the loop's directory."""
    env = product_settings(MIGRATE_CHECK_CMD="fake-migrate --check *")
    here = tmp_path / "cwd"
    here.mkdir()
    (here / "secret-looking-file").write_text("x")
    calls = tmp_path / "calls"
    fake(
        tmp_path / "bin",
        "docker",
        f'printf "%s\\n" "$*" >> {shlex.quote(str(calls))}\n'
        'case " $* " in *" ps -aq "*) echo abc123 ;; esac\nexit 0\n',
    )
    code = (
        "set -euo pipefail\n"
        f". {shlex.quote(str(env.path))}\n"
        'SRC=/nonexistent; COMPOSE_DIR_ABS="$SRC/$COMPOSE_DIR"; log() { echo "[deployer] $*"; }\n'
        + lift(LOOP, "schema_compatible")
        + "schema_compatible\n"
    )
    result = subprocess.run(
        ["bash", "-c", code],
        cwd=here,
        capture_output=True,
        text=True,
        env=path_with(tmp_path / "bin"),
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    run_line = [line for line in calls.read_text().splitlines() if " run --rm -T " in line]
    assert run_line and run_line[0].endswith("fake-migrate --check *"), run_line
    assert "secret-looking-file" not in calls.read_text()


# --- hook paths at the loop's start -------------------------------------------------


def hook_checks(hook: str, site_hook: str) -> subprocess.CompletedProcess:
    code = (
        "set -euo pipefail\n"
        f"HOOK={shlex.quote(hook)}; SITE_HOOK={shlex.quote(site_hook)}; PRODUCT=fakeproduct\n"
        'log() { echo "[deployer] $*"; }; alert() { echo "[alert] $*"; }\n'
        + lift(LOOP, "hook_path_ok")
        + block(LOOP, 'if [ -n "$HOOK" ] && ! hook_path_ok', "fi", count=2, ceiling=12)
        + 'echo "STARTED site=[$SITE_HOOK]"\n'
    )
    return subprocess.run(["bash", "-c", code], capture_output=True, text=True, check=False)


@pytest.mark.parametrize("hook", ["deploy/post-deploy.sh", "post-deploy.sh", "deploy/..hidden.sh", ""])
def test_a_hook_inside_the_clone_is_accepted(hook: str) -> None:
    result = hook_checks(hook, hook)
    assert result.returncode == 0 and f"STARTED site=[{hook}]" in result.stdout, result.stdout + result.stderr
    assert "[alert]" not in result.stdout


@pytest.mark.parametrize(
    "hook", ["/usr/local/bin/hook.sh", "../outside.sh", "deploy/../../outside.sh", "deploy/.."]
)
def test_a_deploy_hook_outside_the_clone_stops_the_loop(hook: str) -> None:
    result = hook_checks(hook, "")
    assert result.returncode == 1 and "STARTED" not in result.stdout
    assert "POST_DEPLOY_HOOK must be a path inside the repository" in result.stdout


@pytest.mark.parametrize("hook", ["/usr/local/bin/hook.sh", "deploy/../../outside.sh"])
def test_a_site_hook_outside_the_clone_is_alerted_and_switched_off(hook: str) -> None:
    result = hook_checks("", hook)
    assert result.returncode == 0 and "STARTED site=[]" in result.stdout, result.stdout
    assert "[alert] fakeproduct site release hook OFF" in result.stdout


# --- BACKUP_PREFIX ------------------------------------------------------------------


@pytest.mark.parametrize("prefix", ["*", "bad/prefix", "two words", "../up", "x?"])
def test_backup_refuses_a_prefix_that_could_widen_its_prunes(
    tmp_path: Path, product_settings: Callable[..., ProductSettings], prefix: str
) -> None:
    env = product_settings()
    out = tmp_path / "backups"
    out.mkdir()
    keep = out / "unrelated-file.sql.gz.enc"
    keep.write_text("x")
    old = 1_000_000_000
    os.utime(keep, (old, old))
    fake(tmp_path / "bin", "pg_dump", "head -c 4096 /dev/urandom\n")
    fake(tmp_path / "bin", "sleep", "kill $PPID\n")
    result = subprocess.run(
        ["bash", str(BACKUP)],
        env={
            **os.environ,
            **env.values,
            "PATH": path_with(tmp_path / "bin")["PATH"],
            "BACKUP_PASSPHRASE": PASSPHRASE,
            "BACKUP_PREFIX": prefix,
            "BACKUP_ROOT": str(out),
            "BACKUP_HEARTBEAT": str(tmp_path / "beat"),
        },
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1, result.stderr
    assert "BACKUP_PREFIX may hold only letters, digits, _ and -" in result.stderr
    assert keep.exists() and [p.name for p in out.iterdir()] == [keep.name]


# --- the installer's folder and plist checks ----------------------------------------


def stop_stub() -> str:
    return 'stop() { echo "STOP: $1"; exit 1; }\n'


def support_dir_check(home: Path, folder: str) -> subprocess.CompletedProcess:
    code = (
        "set -eu\n"
        f"HOME={shlex.quote(str(home))}; APP_SUPPORT_DIR={shlex.quote(folder)}\n"
        + stop_stub()
        + block(INSTALLER, 'case "$APP_SUPPORT_DIR" in', "esac", ceiling=8)
        + block(INSTALLER, 'd="$APP_SUPPORT_DIR"', "done", ceiling=8)
        + "echo ACCEPTED\n"
    )
    return subprocess.run(["bash", "-c", code], capture_output=True, text=True, check=False)


def test_a_support_folder_under_library_is_accepted(tmp_path: Path) -> None:
    folder = tmp_path / "Library" / "Application Support" / "fakeproduct"
    folder.mkdir(parents=True)
    result = support_dir_check(tmp_path, str(folder))
    assert "ACCEPTED" in result.stdout, result.stdout


@pytest.mark.parametrize(
    ("folder", "reason"),
    [
        ("{home}/Library/../Documents/x", "must not contain .."),
        ("{home}/Library/x/..", "must not contain .."),
        ("/srv/fakeproduct", "must be under"),
        ("{home}/Libraryx/fakeproduct", "must be under"),
        ("{home}/Documents/fakeproduct", "must be under"),
    ],
)
def test_a_support_folder_elsewhere_is_refused(tmp_path: Path, folder: str, reason: str) -> None:
    result = support_dir_check(tmp_path, folder.format(home=tmp_path))
    assert result.returncode == 1 and "ACCEPTED" not in result.stdout
    assert reason in result.stdout, result.stdout


def test_a_symlink_on_the_way_to_the_support_folder_is_refused(tmp_path: Path) -> None:
    (tmp_path / "elsewhere").mkdir()
    (tmp_path / "Library").mkdir()
    (tmp_path / "Library" / "link").symlink_to(tmp_path / "elsewhere")
    result = support_dir_check(tmp_path, str(tmp_path / "Library" / "link" / "fakeproduct"))
    assert result.returncode == 1 and "is a symlink" in result.stdout, result.stdout


def plist_text_check(label: str, folder: str, log_base: str) -> subprocess.CompletedProcess:
    code = (
        "set -eu\n"
        f"LAUNCH_LABEL={shlex.quote(label)}; APP_SUPPORT_DIR={shlex.quote(folder)}; LOG_BASE={shlex.quote(log_base)}\n"
        + stop_stub()
        + block(INSTALLER, 'for v in "$LAUNCH_LABEL" "$APP_SUPPORT_DIR" "$LOG_BASE"', "done", ceiling=8)
        + "echo ACCEPTED\n"
    )
    return subprocess.run(["bash", "-c", code], capture_output=True, text=True, check=False)


def test_plain_plist_values_are_accepted() -> None:
    result = plist_text_check(
        "invalid.fake.vm-autostart", "/h/Library/Application Support/x", "/h/Library/Logs/x"
    )
    assert "ACCEPTED" in result.stdout, result.stdout


@pytest.mark.parametrize("bad", ["<", ">", "&", '"', "'"])
@pytest.mark.parametrize("field", ["label", "folder", "log"])
def test_xml_special_characters_in_plist_values_are_refused(bad: str, field: str) -> None:
    values = {"label": "invalid.fake.vm-autostart", "folder": "/h/Library/x", "log": "/h/Library/Logs/x"}
    values[field] += bad
    result = plist_text_check(values["label"], values["folder"], values["log"])
    assert result.returncode == 1 and "ACCEPTED" not in result.stdout
    assert "must not contain any of" in result.stdout


def test_the_installer_checks_the_folder_before_installing_and_names_the_origin() -> None:
    text = INSTALLER.read_text(encoding="utf-8")
    assert text.index('case "$APP_SUPPORT_DIR" in') < text.index('DEST_DIR="$APP_SUPPORT_DIR"')
    assert text.index('for v in "$LAUNCH_LABEL" "$APP_SUPPORT_DIR" "$LOG_BASE"') < text.index(
        "s|__LAUNCH_LABEL__|"
    )
    ok = [line for line in text.splitlines() if line.startswith('echo "ok: all five files match')]
    assert len(ok) == 1 and "$ORIGIN_URL" in ok[0], ok


def test_the_example_support_folder_is_under_library() -> None:
    home = "/home/fake-user"
    value = subprocess.run(
        [
            "bash",
            "-c",
            f'HOME={home}; . "$1"; printf %s "$APP_SUPPORT_DIR"',
            "-",
            str(LIBRARY_ROOT / "deploy" / "product.settings.example"),
        ],
        capture_output=True,
        text=True,
        check=True,
    ).stdout
    assert value.startswith(f"{home}/Library/") and "/../" not in value, value
