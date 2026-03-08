"""Smoke tests for contract lint checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType


def test_contract_lint_passes_current_repo_contracts() -> None:
    root = Path(__file__).resolve().parents[2]
    module_path = root / "scripts" / "ci" / "contract_lint.py"
    spec = importlib.util.spec_from_file_location("contract_lint", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    assert isinstance(module, ModuleType)
    spec.loader.exec_module(module)
    main = module.main
    assert callable(main)
    assert main() == 0
