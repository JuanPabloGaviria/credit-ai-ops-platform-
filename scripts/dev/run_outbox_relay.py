"""Compatibility wrapper for the runtime outbox relay entrypoint."""

from __future__ import annotations

import runpy
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def main() -> int:
    runpy.run_path(str(ROOT / "scripts" / "runtime" / "run_outbox_relay.py"), run_name="__main__")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
