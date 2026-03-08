"""Test path bootstrap for monorepo package imports."""

from __future__ import annotations

import os
import sys
from pathlib import Path

from tests.defaults import DEFAULT_POSTGRES_DSN, DEFAULT_RABBITMQ_URL

ROOT = Path(__file__).resolve().parents[1]
PATHS = [
    ROOT / "packages" / "shared-kernel" / "src",
    ROOT / "packages" / "contracts" / "src",
    ROOT / "packages" / "observability" / "src",
    ROOT / "packages" / "security" / "src",
    ROOT / "services" / "api-gateway" / "src",
    ROOT / "services" / "application" / "src",
    ROOT / "services" / "feature" / "src",
    ROOT / "services" / "scoring" / "src",
    ROOT / "services" / "decision" / "src",
    ROOT / "services" / "collab-assistant" / "src",
    ROOT / "services" / "mlops" / "src",
    ROOT / "services" / "observability-audit" / "src",
]

for path in PATHS:
    sys.path.insert(0, str(path))

os.environ.setdefault("POSTGRES_DSN", DEFAULT_POSTGRES_DSN)
os.environ.setdefault("RABBITMQ_URL", DEFAULT_RABBITMQ_URL)
os.environ.setdefault("SKIP_STARTUP_DEPENDENCY_CHECKS", "true")
