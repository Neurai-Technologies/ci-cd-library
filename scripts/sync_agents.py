"""Copy the shared-core block from CLAUDE.md into AGENTS.md and GEMINI.md.

CLAUDE.md is the source of truth. Each agent file stays self-contained: the
block is copied, never linked, so no agent has to read another agent's file.
Run `--check` to verify without writing (scripts/check_docs.py calls this).
"""

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SOURCE = "CLAUDE.md"
TARGETS = ("AGENTS.md", "GEMINI.md")
BLOCK = re.compile(
    r"<!-- shared-core:begin -->\n.*?<!-- shared-core:end -->\n", re.DOTALL
)


def block_of(name: str) -> str:
    """Return the shared-core block of one agent file, or exit if it is missing."""
    match = BLOCK.search((ROOT / name).read_text(encoding="utf-8"))
    if match is None:
        print(f"{name}: shared-core markers missing")
        raise SystemExit(1)
    return match.group(0)


def main(check: bool) -> int:
    """Sync or verify the shared block; return a process status."""
    source = block_of(SOURCE)
    status = 0
    for name in TARGETS:
        path = ROOT / name
        text = path.read_text(encoding="utf-8")
        if block_of(name) == source:
            continue
        if check:
            print(f"{name}: shared-core block differs from {SOURCE}")
            status = 1
            continue
        path.write_text(BLOCK.sub(lambda _: source, text, count=1), encoding="utf-8")
        print(f"{name}: shared-core block updated from {SOURCE}")
    return status


if __name__ == "__main__":
    raise SystemExit(main(check="--check" in sys.argv[1:]))
