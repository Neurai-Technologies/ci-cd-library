"""Every library service drops every capability, and the exception is named.

The backup and log-archive containers need no capability at all. Anything
added back is a widening this test refuses.
"""

from __future__ import annotations

from pathlib import Path

import yaml

# The deployer holds the docker socket and is recreated by hand from the
# operator's machine rather than by itself, so its hardening is a separate
# decision, not a service this rule reaches. It still refuses escalation.
EXEMPT = {"deployer"}


def services(library_root: Path) -> dict:
    stack = yaml.safe_load((library_root / "deploy" / "compose.example.yml").read_text(encoding="utf-8"))
    return stack["services"]


def test_every_service_drops_every_capability(library_root: Path) -> None:
    for name, service in services(library_root).items():
        assert service.get("security_opt") == ["no-new-privileges:true"], f"{name} allows escalation"
        if name in EXEMPT:
            continue
        assert service.get("cap_drop") == ["ALL"], f"{name} keeps its capabilities"
        assert service.get("read_only") is True, f"{name} has a writable root filesystem"


def test_no_service_adds_anything_back(library_root: Path) -> None:
    for name, service in services(library_root).items():
        assert not service.get("cap_add"), f"{name} adds back {service.get('cap_add')}"
