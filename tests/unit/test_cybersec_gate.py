"""Smoke tests for cybersecurity posture checks."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

import pytest


def _load_module() -> ModuleType:
    root = Path(__file__).resolve().parents[2]
    module_path = root / "scripts" / "ci" / "cybersec_gate.py"
    spec = importlib.util.spec_from_file_location("cybersec_gate", module_path)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    assert isinstance(module, ModuleType)
    spec.loader.exec_module(module)
    return module


def test_cybersec_gate_passes_current_repo_posture(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRANCH_PROTECTION_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_TOKEN", raising=False)
    monkeypatch.delenv("GITHUB_REPOSITORY", raising=False)

    module = _load_module()
    main = module.main
    assert callable(main)
    assert main() == 0


def test_cybersec_gate_strict_mode_fails_when_github_token_lacks_branch_scope(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRANCH_PROTECTION_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    monkeypatch.setenv("ENFORCE_REMOTE_BRANCH_PROTECTION", "1")

    module = _load_module()

    def _raise_forbidden(repository: str, branch_name: str, token: str) -> dict[str, object]:
        raise OSError(f"GitHub API returned 403 for branch '{branch_name}': forbidden")

    monkeypatch.setattr(module, "_fetch_remote_branch_protection", _raise_forbidden)
    main = module.main
    assert callable(main)
    assert main() == 1


def test_cybersec_gate_non_strict_mode_allows_best_effort_github_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("BRANCH_PROTECTION_TOKEN", raising=False)
    monkeypatch.setenv("GITHUB_TOKEN", "github-token")
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    monkeypatch.setenv("ENFORCE_REMOTE_BRANCH_PROTECTION", "0")

    module = _load_module()

    def _raise_forbidden(repository: str, branch_name: str, token: str) -> dict[str, object]:
        raise OSError(f"GitHub API returned 403 for branch '{branch_name}': forbidden")

    monkeypatch.setattr(module, "_fetch_remote_branch_protection", _raise_forbidden)
    main = module.main
    assert callable(main)
    assert main() == 0


def test_cybersec_gate_non_strict_mode_reports_remote_drift_without_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    expected_secret = "-".join(("branch", "protection", "drift", "credential"))
    monkeypatch.setenv("BRANCH_PROTECTION_TOKEN", expected_secret)
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    monkeypatch.setenv("ENFORCE_REMOTE_BRANCH_PROTECTION", "0")

    module = _load_module()

    def _drifted_payload(repository: str, branch_name: str, token: str) -> dict[str, object]:
        assert repository == "example/repo"
        assert branch_name == "main"
        assert token == expected_secret
        return {
            "required_status_checks": {"contexts": ["quality"]},
            "required_pull_request_reviews": {
                "required_approving_review_count": 0,
                "dismiss_stale_reviews": True,
            },
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
            "required_conversation_resolution": {"enabled": True},
        }

    monkeypatch.setattr(module, "_fetch_remote_branch_protection", _drifted_payload)
    main = module.main
    assert callable(main)
    assert main() == 0
    output = capsys.readouterr().out
    assert "best-effort validation only outside protected-branch enforcement" in output
    assert "remote branch protection matches repository policy" not in output


def test_cybersec_gate_uses_branch_protection_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_secret = "-".join(("branch", "protection", "alias", "credential"))
    monkeypatch.setenv("BRANCH_PROTECTION_TOKEN", expected_secret)
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    monkeypatch.setenv("ENFORCE_REMOTE_BRANCH_PROTECTION", "1")

    module = _load_module()

    def _valid_payload(repository: str, branch_name: str, token: str) -> dict[str, object]:
        assert repository == "example/repo"
        assert branch_name == "main"
        assert token == expected_secret
        return {
            "required_status_checks": {
                "contexts": [
                    "quality",
                    "integration-e2e",
                    "supply-chain-verify",
                    "container-policy",
                    "secret-scan",
                    "sbom",
                ]
            },
            "required_pull_request_reviews": {
                "required_approving_review_count": 1,
                "dismiss_stale_reviews": True,
            },
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
            "required_conversation_resolution": {"enabled": True},
        }

    monkeypatch.setattr(module, "_fetch_remote_branch_protection", _valid_payload)
    main = module.main
    assert callable(main)
    assert main() == 0


def test_cybersec_gate_strict_mode_fails_on_remote_policy_drift(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    expected_secret = "-".join(("branch", "protection", "strict", "credential"))
    monkeypatch.setenv("BRANCH_PROTECTION_TOKEN", expected_secret)
    monkeypatch.setenv("GITHUB_REPOSITORY", "example/repo")
    monkeypatch.setenv("ENFORCE_REMOTE_BRANCH_PROTECTION", "1")

    module = _load_module()

    def _drifted_payload(repository: str, branch_name: str, token: str) -> dict[str, object]:
        assert repository == "example/repo"
        assert branch_name == "main"
        assert token == expected_secret
        return {
            "required_status_checks": {"contexts": ["quality"]},
            "required_pull_request_reviews": {
                "required_approving_review_count": 0,
                "dismiss_stale_reviews": True,
            },
            "allow_force_pushes": {"enabled": False},
            "allow_deletions": {"enabled": False},
            "required_conversation_resolution": {"enabled": True},
        }

    monkeypatch.setattr(module, "_fetch_remote_branch_protection", _drifted_payload)
    main = module.main
    assert callable(main)
    assert main() == 1


def test_check_terraform_pinning_requires_model_signing_controls() -> None:
    module = _load_module()
    check_terraform_pinning = module._check_terraform_pinning
    root = Path(__file__).resolve().parents[2]

    terraform_main = (
        (root / "infra" / "terraform" / "container-apps" / "main.tf")
        .read_text(encoding="utf-8")
        .replace(
            'check "model_signing_configuration"',
            'check "tampered_signing_configuration"',
        )
        .replace("MODEL_SIGNING_PRIVATE_KEY_PEM", "TAMPERED_SIGNING_PRIVATE_KEY_ENV")
        .replace("MODEL_SIGNING_PUBLIC_KEY_PEM", "TAMPERED_SIGNING_PUBLIC_KEY_ENV")
    )
    terraform_variables = (
        (root / "infra" / "terraform" / "container-apps" / "variables.tf")
        .read_text(encoding="utf-8")
        .replace(
            'variable "model_signing_private_key_pem"',
            'variable "tampered_signing_private_key_pem"',
        )
        .replace(
            'variable "model_signing_public_key_pem"',
            'variable "tampered_signing_public_key_pem"',
        )
    )

    failures: list[str] = []
    check_terraform_pinning(terraform_main, terraform_variables, failures)

    assert any(
        'variable "model_signing_private_key_pem"' in failure for failure in failures
    )
    assert any(
        'variable "model_signing_public_key_pem"' in failure for failure in failures
    )
    assert any('check "model_signing_configuration"' in failure for failure in failures)
    assert any("MODEL_SIGNING_PRIVATE_KEY_PEM" in failure for failure in failures)
    assert any("MODEL_SIGNING_PUBLIC_KEY_PEM" in failure for failure in failures)


def test_check_build_sign_workflow_requires_push_main_trigger() -> None:
    module = _load_module()
    check_build_sign_workflow = module._check_build_sign_workflow
    root = Path(__file__).resolve().parents[2]

    build_sign_text = (
        (root / ".github" / "workflows" / "build-sign.yml")
        .read_text(encoding="utf-8")
        .replace('  push:\n    branches: ["main"]\n', "")
    )
    build_sign_workflow = module._load_yaml(root / ".github" / "workflows" / "build-sign.yml")
    build_sign_workflow[True] = {
        "workflow_dispatch": None,
    }

    failures: list[str] = []
    check_build_sign_workflow(build_sign_workflow, build_sign_text, failures)

    assert "build-sign workflow must trigger on pushes to main" in failures
