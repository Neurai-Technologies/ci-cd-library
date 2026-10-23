"""Every container's log has a ceiling.

Docker's json-file driver appends forever by default, and the VM disk it
fills is the one the database and its backups live on. Each service is
bounded to five files of 10 MB, rotated by docker; a service added without
the block would grow without limit, which is why this reads every service.
"""

from __future__ import annotations

from pathlib import Path

import yaml

BOUNDS = {"driver": "json-file", "options": {"max-size": "10m", "max-file": "5"}}


def test_every_service_bounds_its_log(library_root: Path) -> None:
    stack = yaml.safe_load((library_root / "deploy" / "compose.example.yml").read_text(encoding="utf-8"))
    assert stack["x-log-bounds"] == BOUNDS, "the anchor a product merges must carry the bounds"
    services = stack["services"]
    assert sorted(services) == ["backup", "deployer", "logarchive"], sorted(services)
    for name, service in services.items():
        assert service.get("logging") == BOUNDS, f"{name} logs without a ceiling"
