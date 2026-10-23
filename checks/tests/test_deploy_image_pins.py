"""The image pins agree with each other.

Postgres is pinned by hand in four places: the backup image (whose pg_dump
must match the server), the restore drill's image and its run.sh default,
and the example product.settings. Bumping one left the drill proving a restore
into an image production does not run. The deployer's base is pinned by
digest, because a floating tag could pick up a compose that treats the
profiled deployer as an orphan.
"""

from __future__ import annotations

import re
from pathlib import Path


def pins(text: str, image: str) -> set[str]:
    # A dotted version, so `postgres:5432` inside a connection URL is not a pin.
    return set(re.findall(rf"\b({image}:[0-9]+\.[0-9]+[0-9A-Za-z.\-]*[0-9A-Za-z])", text))


def test_every_postgres_pin_is_the_same_build(library_root: Path) -> None:
    deploy = library_root / "deploy"
    example = (deploy / "product.settings.example").read_text(encoding="utf-8")
    (assigned,) = re.findall(r"^POSTGRES_IMAGE=(.*)$", example, re.MULTILINE)
    sites = {
        "backup/Dockerfile": pins((deploy / "backup" / "Dockerfile").read_text(encoding="utf-8"), "postgres"),
        "restore-drill/Dockerfile": pins(
            (deploy / "restore-drill" / "Dockerfile").read_text(encoding="utf-8"), "postgres"
        ),
        "restore-drill/run.sh": pins(
            (deploy / "restore-drill" / "run.sh").read_text(encoding="utf-8"), "postgres"
        ),
        "product.settings.example POSTGRES_IMAGE": {assigned},
    }
    for name, found in sites.items():
        assert len(found) == 1, f"{name} pins postgres {sorted(found) or 'nowhere'}"
    assert len(set.union(*sites.values())) == 1, {k: sorted(v) for k, v in sites.items()}


def test_the_deployer_base_is_pinned_by_digest(library_root: Path) -> None:
    dockerfile = (library_root / "deploy" / "deployer" / "Dockerfile").read_text(encoding="utf-8")
    bases = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert len(bases) == 1 and re.fullmatch(r"FROM \S+@sha256:[0-9a-f]{64}", bases[0]), bases
