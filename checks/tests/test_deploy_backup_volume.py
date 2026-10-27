"""The backup containers must not grow an anonymous volume per recreate, and the drill must restore like production.

The backup image is built from the database image, which declares a VOLUME
for the data directory. The backup never writes there, but with no mount of
its own docker created an anonymous volume for that path on every recreate
and kept the old one. A tmpfs in its place is empty, private to the
container and freed with it.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml

DATA_DIR = "/var/lib/postgresql/data"


def compose(library_root: Path) -> dict:
    return yaml.safe_load((library_root / "deploy" / "compose.example.yml").read_text(encoding="utf-8"))


@pytest.mark.parametrize("name", ["backup", "logarchive"])
def test_every_service_on_the_backup_image_covers_the_inherited_data_volume_with_a_tmpfs(
    library_root: Path, name: str
) -> None:
    service = compose(library_root)["services"][name]
    assert service["build"]["context"].endswith("/deploy/backup"), (
        f"{name} is not built from the backup image"
    )
    assert DATA_DIR in service.get("tmpfs", []), (
        f"{name}: the inherited VOLUME path needs a tmpfs or every recreate leaks a volume"
    )
    assert not any(DATA_DIR in str(v) for v in service.get("volumes", [])), (
        f"{name}: the data directory is not a volume of ours"
    )


def test_the_heartbeat_is_written_by_the_backup_on_its_own_volume(library_root: Path) -> None:
    """The alarm for stopped backups reads a heartbeat, never the dumps: one
    file on its own named volume, writable here. The reader is the product's
    own service and is checked there."""
    stack = compose(library_root)
    assert "backup-heartbeat" in stack["volumes"]
    assert "backup-heartbeat:/heartbeat" in stack["services"]["backup"]["volumes"], (
        "the writer must have it writable"
    )
    assert not any("heartbeat" in str(v) for v in stack["services"]["logarchive"]["volumes"])


def test_the_drill_restores_into_the_production_image_with_the_production_init_script(
    library_root: Path,
) -> None:
    """The first drill found that a bare image refuses the dump on the roles
    the init script creates, so the drill builds the pinned image with the
    product's init scripts baked in, and keeps no data between runs."""
    drill_dir = library_root / "deploy" / "restore-drill"
    drill = yaml.safe_load((drill_dir / "docker-compose.yml").read_text(encoding="utf-8"))
    postgres = drill["services"]["postgres"]
    assert postgres["build"]["context"] == "." and postgres["build"]["dockerfile"] == "Dockerfile"
    dockerfile = (drill_dir / "Dockerfile").read_text(encoding="utf-8")
    assert "COPY .init/ /docker-entrypoint-initdb.d/" in dockerfile
    assert "FROM ${POSTGRES_IMAGE}\n" in dockerfile
    assert "volumes" not in postgres, "a bind-mounted Mac path reached the VM empty once"
    assert DATA_DIR in postgres["tmpfs"], "the scratch database must be gone with `down -v`"
    run = (drill_dir / "run.sh").read_text(encoding="utf-8")
    assert 'cp -R "${REPO_DIR}/${POSTGRES_INIT_DIR}" "$HERE/.init"' in run
    assert "RESTORE_PROJECT=drill" in run, "the drill restores into its own project, never the live one"
    assert ".init/" in (drill_dir / ".gitignore").read_text(encoding="utf-8").splitlines()


def test_only_the_backup_service_mounts_the_lock_volume(library_root: Path) -> None:
    """Any container that can open backup.lock can hold it (flock works on a
    read-only file too) and stall every cycle; logarchive takes no such lock."""
    stack = compose(library_root)
    assert "backup-lock" in stack["volumes"]
    assert "backup-lock:/lock" in stack["services"]["backup"]["volumes"]
    # environment: overrides env_file, so product.settings cannot move the lock.
    assert stack["services"]["backup"]["environment"]["BACKUP_LOCK_DIR"] == "/lock"
    others = {
        name: service.get("volumes", [])
        for name, service in stack["services"].items()
        if name != "backup" and any(str(v).startswith("backup-lock:") for v in service.get("volumes", []))
    }
    assert others == {}, f"backup-lock mounted outside the backup service: {others}"
