"""The image pins agree with each other.

Postgres is pinned by hand in five places: the backup image's default
(whose pg_dump must match the server), the compose example's build args,
the restore drill's image and its run.sh default, and the example
product.settings. Both Dockerfiles take the image as the POSTGRES_IMAGE
build arg, so a product on another major sets one exact tag; that default
and every value here must be an exact version, never a floating tag. Bumping one left the drill proving a restore
into an image production does not run. The deployer's base is pinned by
digest, because a floating tag could pick up a compose that treats the
profiled deployer as an orphan.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

# postgres:<major>.<minor>-alpine, optionally @sha256:<digest> for a stronger
# pin: the form every pin here takes.
EXACT = re.compile(r"postgres:[0-9]+\.[0-9]+-alpine(@sha256:[0-9a-f]{64})?")


def pins(text: str, image: str) -> set[str]:
    # A dotted version, so `postgres:5432` inside a connection URL is not a pin.
    # Whatever follows an @ is kept, so a digest (or a malformed one) is part
    # of the reference the sites must agree on.
    return set(re.findall(rf"\b({image}:[0-9]+\.[0-9]+[0-9A-Za-z.\-]*[0-9A-Za-z](?:@[0-9A-Za-z:]*)?)", text))


def test_every_postgres_pin_is_the_same_build(library_root: Path) -> None:
    deploy = library_root / "deploy"
    example = (deploy / "product.settings.example").read_text(encoding="utf-8")
    (assigned,) = re.findall(r"^POSTGRES_IMAGE=(.*)$", example, re.MULTILINE)
    sites = {
        "backup/Dockerfile": pins((deploy / "backup" / "Dockerfile").read_text(encoding="utf-8"), "postgres"),
        "restore-drill/Dockerfile": pins(
            (deploy / "restore-drill" / "Dockerfile").read_text(encoding="utf-8"), "postgres"
        ),
        "compose.example.yml": pins((deploy / "compose.example.yml").read_text(encoding="utf-8"), "postgres"),
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


def postgres_base(dockerfile: str) -> str:
    """The image a Dockerfile builds from: one exact-version POSTGRES_IMAGE arg, used as its only FROM."""
    args = re.findall(r"^ARG POSTGRES_IMAGE=(.*)$", dockerfile, re.MULTILINE)
    bases = [line for line in dockerfile.splitlines() if line.startswith("FROM ")]
    assert len(args) == 1, f"expected one ARG POSTGRES_IMAGE=, found {args}"
    assert bases == ["FROM ${POSTGRES_IMAGE}"], bases
    assert EXACT.fullmatch(args[0]), f"{args[0]} is not an exact postgres version"
    return args[0]


def test_both_postgres_dockerfiles_take_the_same_arg_and_default(library_root: Path) -> None:
    deploy = library_root / "deploy"
    defaults = {
        postgres_base((deploy / name / "Dockerfile").read_text(encoding="utf-8"))
        for name in ("backup", "restore-drill")
    }
    assert len(defaults) == 1, defaults


def build_arg(service: dict) -> str:
    """A compose service's POSTGRES_IMAGE build arg, which must be an exact version."""
    value = service["build"]["args"]["POSTGRES_IMAGE"]
    assert EXACT.fullmatch(value), value
    return value


def test_the_compose_build_args_are_exact_versions(library_root: Path) -> None:
    compose = yaml.safe_load((library_root / "deploy" / "compose.example.yml").read_text(encoding="utf-8"))
    for name in ("backup", "logarchive"):
        build_arg(compose["services"][name])


@pytest.mark.parametrize(
    "dockerfile",
    [
        "FROM postgres:16.15-alpine\n",
        "ARG POSTGRES_IMAGE=postgres:17-alpine\nFROM ${POSTGRES_IMAGE}\n",
        "ARG POSTGRES_IMAGE=postgres:alpine\nFROM ${POSTGRES_IMAGE}\n",
        "ARG POSTGRES_IMAGE=postgres:latest\nFROM ${POSTGRES_IMAGE}\n",
        "ARG POSTGRES_IMAGE=postgres:16.15-alpine\nFROM postgres:17-alpine\n",
        "ARG POSTGRES_IMAGE=postgres:16.15-alpine\nFROM ${POSTGRES_IMAGE}\nFROM postgres:alpine\n",
    ],
)
def test_a_floating_or_bypassed_postgres_base_is_refused(dockerfile: str) -> None:
    with pytest.raises(AssertionError):
        postgres_base(dockerfile)


@pytest.mark.parametrize("value", ["postgres:16.15-alpine", "postgres:17.11-alpine"])
def test_an_exact_tag_of_either_major_is_accepted(value: str) -> None:
    assert postgres_base(f"ARG POSTGRES_IMAGE={value}\nFROM ${{POSTGRES_IMAGE}}\n") == value


DIGEST = "0000000000000000000000000000000000000000000000000000000000000000"


@pytest.mark.parametrize(
    "value",
    [
        f"postgres:17.11-alpine@sha256:{DIGEST[:63]}",
        f"postgres:17.11-alpine@sha256:{DIGEST.upper()[:60]}ABCD",
        "postgres:17.11-alpine@sha256:",
        f"postgres@sha256:{DIGEST}",
        f"postgres:17-alpine@sha256:{DIGEST}",
    ],
)
def test_a_malformed_digest_pin_is_refused(value: str) -> None:
    with pytest.raises(AssertionError):
        postgres_base(f"ARG POSTGRES_IMAGE={value}\nFROM ${{POSTGRES_IMAGE}}\n")
    with pytest.raises(AssertionError):
        build_arg({"build": {"args": {"POSTGRES_IMAGE": value}}})


def test_a_digest_pin_is_accepted_and_compared_whole() -> None:
    pinned = f"postgres:17.11-alpine@sha256:{DIGEST}"
    assert postgres_base(f"ARG POSTGRES_IMAGE={pinned}\nFROM ${{POSTGRES_IMAGE}}\n") == pinned
    assert build_arg({"build": {"args": {"POSTGRES_IMAGE": pinned}}}) == pinned
    assert pins(f"FROM {pinned}\n", "postgres") == {pinned}
    # Same tag, another digest: two different builds, not one.
    other = f"postgres:17.11-alpine@sha256:{'1' * 64}"
    assert pins(f"a={pinned}\nb={other}\n", "postgres") == {pinned, other}
    assert pins(f"a={pinned}\nb=postgres:17.11-alpine\n", "postgres") == {pinned, "postgres:17.11-alpine"}
