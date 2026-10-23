"""A deploy does not wait for the services that only sleep.

The backup and the log archive run a shell loop as process 1 with no handler
for the stop signal, so docker waits the whole grace period and then kills
them, and compose starts no new container until then: the product's api is
down for as long as this is. The product's own services carry their memory,
pid and connection budgets in the product's checks.
"""

from __future__ import annotations

from pathlib import Path

import pytest
import yaml


@pytest.mark.parametrize("name", ["backup", "logarchive"])
def test_a_deploy_does_not_wait_for_the_services_that_only_sleep(library_root: Path, name: str) -> None:
    stack = yaml.safe_load((library_root / "deploy" / "compose.example.yml").read_text(encoding="utf-8"))
    grace = str(stack["services"][name]["stop_grace_period"])
    assert grace.endswith("s"), f"{name}: grace period not in seconds"
    assert int(grace[:-1]) <= 2, f"{name}: every release waits this long for a kill"
