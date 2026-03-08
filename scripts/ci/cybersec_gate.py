"""Cybersecurity posture gate for bank-grade baseline controls."""

from __future__ import annotations

import http.client
import json
import os
import re
import tomllib
from pathlib import Path
from typing import cast
from urllib.parse import quote

import yaml

ROOT = Path(__file__).resolve().parents[2]
PYPROJECT_PATH = ROOT / "pyproject.toml"
COMPOSE_PATH = ROOT / "docker-compose.yml"
TERRAFORM_MAIN_PATH = ROOT / "infra/terraform/container-apps/main.tf"
TERRAFORM_VARIABLES_PATH = ROOT / "infra/terraform/container-apps/variables.tf"
BRANCH_PROTECTION_PATH = ROOT / ".github/branch-protection-policy.json"
CI_WORKFLOW_PATH = ROOT / ".github/workflows/ci.yml"
BUILD_SIGN_WORKFLOW_PATH = ROOT / ".github/workflows/build-sign.yml"
WORKFLOW_PATHS = (CI_WORKFLOW_PATH, BUILD_SIGN_WORKFLOW_PATH)
REQUIRED_PYTHON_RANGE = ">=3.11,<3.12"
REQUIRED_STATUS_CHECKS = ("quality", "container-policy", "secret-scan", "sbom")
EXPECTED_CYBERSEC_STEP = "Cybersecurity posture gate"
REQUIRED_SIGNED_SERVICES = (
    "api-gateway",
    "application",
    "feature",
    "scoring",
    "decision",
    "collab-assistant",
    "mlops",
    "observability-audit",
)
GITHUB_API_BASE = "https://api.github.com"
ACTION_REFERENCE_PATTERN = re.compile(
    r"^\s*-\s+uses:\s+([A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+)@([^\s#]+)"
)
FULL_COMMIT_SHA_PATTERN = re.compile(r"^[0-9a-f]{40}$")


def _print_ok(message: str) -> None:
    print(f"[cybersec-gate] OK {message}")


def _print_fail(message: str) -> None:
    print(f"[cybersec-gate] FAIL {message}")


def _print_note(message: str) -> None:
    print(f"[cybersec-gate] NOTE {message}")


def _load_toml(path: Path) -> dict[str, object]:
    with path.open("rb") as handle:
        payload = tomllib.load(handle)
    return cast(dict[str, object], payload)


def _load_yaml(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        payload = yaml.safe_load(handle)
    if not isinstance(payload, dict):
        raise ValueError("YAML payload root must be a dictionary")
    return cast(dict[str, object], payload)


def _load_json(path: Path) -> dict[str, object]:
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError("JSON payload root must be a dictionary")
    return cast(dict[str, object], payload)


def _as_string_object_dict(value: object) -> dict[str, object] | None:
    if not isinstance(value, dict):
        return None
    normalized: dict[str, object] = {}
    raw_dict = cast(dict[object, object], value)
    for raw_key, raw_item in raw_dict.items():
        if not isinstance(raw_key, str):
            return None
        normalized[raw_key] = raw_item
    return normalized


def _as_string_list(value: object) -> list[str] | None:
    if not isinstance(value, list):
        return None
    items: list[str] = []
    raw_items = cast(list[object], value)
    for item in raw_items:
        if not isinstance(item, str):
            return None
        items.append(item)
    return items


def _check_python_runtime(pyproject: dict[str, object], failures: list[str]) -> None:
    project_obj = pyproject.get("project")
    if not isinstance(project_obj, dict):
        failures.append("pyproject.toml is missing [project] section")
        return
    project = cast(dict[str, object], project_obj)
    requires_python = project.get("requires-python")
    if requires_python != REQUIRED_PYTHON_RANGE:
        failures.append(
            f"requires-python must be '{REQUIRED_PYTHON_RANGE}', got {requires_python!r}"
        )
        return
    _print_ok(f"runtime policy pinned to {REQUIRED_PYTHON_RANGE}")


def _check_dependency_pinning(pyproject: dict[str, object], failures: list[str]) -> None:
    failures_before = len(failures)
    project_obj = pyproject.get("project")
    if not isinstance(project_obj, dict):
        failures.append("pyproject.toml is missing [project] section")
        return
    project = cast(dict[str, object], project_obj)

    def _validate_list(entries: object, label: str) -> None:
        if not isinstance(entries, list):
            failures.append(f"{label} must be a list")
            return
        for entry in cast(list[object], entries):
            if not isinstance(entry, str):
                failures.append(f"{label} contains non-string dependency entry")
                continue
            if "==" not in entry:
                failures.append(f"{label} dependency is not pinned: {entry}")

    _validate_list(project.get("dependencies"), "project.dependencies")

    optional_obj = project.get("optional-dependencies")
    if not isinstance(optional_obj, dict):
        failures.append("project.optional-dependencies must be a table")
        return
    optional = cast(dict[str, object], optional_obj)
    _validate_list(optional.get("dev"), "project.optional-dependencies.dev")
    if len(failures) == failures_before:
        _print_ok("dependencies are version-pinned")


def _check_docker_image_pinning(compose: dict[str, object], failures: list[str]) -> None:
    failures_before = len(failures)
    services_obj = compose.get("services")
    if not isinstance(services_obj, dict):
        failures.append("docker-compose.yml is missing services")
        return
    services = cast(dict[str, object], services_obj)
    for service_name, service_obj in services.items():
        if not isinstance(service_obj, dict):
            failures.append(f"docker service '{service_name}' must be a mapping")
            continue
        service = cast(dict[str, object], service_obj)
        image = service.get("image")
        if not isinstance(image, str):
            failures.append(f"docker service '{service_name}' is missing image string")
            continue
        if ":" not in image:
            failures.append(f"docker service '{service_name}' image must include explicit tag")
            continue
        image_tag = image.rsplit(":", maxsplit=1)[1]
        if image_tag in {"latest", ""}:
            failures.append(f"docker service '{service_name}' uses mutable/empty tag '{image_tag}'")
    if len(failures) == failures_before:
        _print_ok("docker compose images are explicitly tagged")


def _check_terraform_pinning(
    terraform_main: str,
    terraform_variables: str,
    failures: list[str],
) -> None:
    failures_before = len(failures)
    if not re.search(r'required_version\s*=\s*"[^\"]+"', terraform_main):
        failures.append("terraform required_version must be pinned in container-apps/main.tf")
    if not re.search(
        r"required_providers\s*\{[\s\S]*azurerm\s*=\s*\{[\s\S]*version\s*=\s*\"[^\"]+\"",
        terraform_main,
    ):
        failures.append("azurerm provider version pin is missing in container-apps/main.tf")
    if not re.search(
        r"required_providers\s*\{[\s\S]*azapi\s*=\s*\{[\s\S]*version\s*=\s*\"[^\"]+\"",
        terraform_main,
    ):
        failures.append("azapi provider version pin is missing in container-apps/main.tf")
    if 'variable "service_image_references"' not in terraform_variables:
        failures.append("container-apps variables must define service_image_references")
    for required_variable in (
        'variable "key_vault_id"',
        'variable "key_vault_secret_ids"',
        'variable "container_registry_use_managed_identity"',
        'variable "container_registry_resource_id"',
        'variable "model_signing_private_key_pem"',
        'variable "model_signing_public_key_pem"',
        'variable "otel_enabled"',
        'variable "otel_service_namespace"',
        'variable "otel_sampler_ratio"',
    ):
        if required_variable not in terraform_variables:
            failures.append(
                "container-apps variables are missing required secure deployment input "
                f"{required_variable}"
            )
    if "container_image_tag" in terraform_main or "container_image_tag" in terraform_variables:
        failures.append("container-apps terraform must not deploy mutable image tags")
    if "local.service_image_references" not in terraform_main:
        failures.append("container-apps terraform must deploy images from service_image_references")
    for required_check in (
        'check "core_runtime_secrets"',
        'check "key_vault_secret_configuration"',
        'check "service_image_references"',
        'check "registry_configuration"',
        'check "model_signing_configuration"',
        'check "production_private_environment"',
        'check "production_auth_required"',
        'check "production_pii_logging_disabled"',
        'check "production_key_vault_secret_boundary"',
        'check "production_gateway_ingress_allowlist"',
        'check "production_telemetry_required"',
    ):
        if required_check not in terraform_main:
            failures.append(
                "container-apps terraform is missing required control "
                f"{required_check}"
            )
    for required_pattern in (
        "azurerm_user_assigned_identity",
        "Key Vault Secrets User",
        "AcrPull",
        "Storage Blob Data Contributor",
        "key_vault_secret_id",
        "SystemAssigned, UserAssigned",
        "azurerm_application_insights",
        "azapi_update_resource",
        "appInsightsConfiguration",
        "openTelemetryConfiguration",
    ):
        if required_pattern not in terraform_main:
            failures.append(
                "container-apps terraform is missing required managed identity / "
                "key vault posture element "
                f"'{required_pattern}'"
            )
    for forbidden_pattern in (
        r"azurerm_container_app_environment_storage",
        r"storage_type\s*=\s*\"AzureFile\"",
        r"shared_access_key_enabled\s*=\s*true",
        r"access_key\s*=\s*azurerm_storage_account\.artifacts\.primary_access_key",
    ):
        if re.search(forbidden_pattern, terraform_main):
            failures.append(
                "container-apps terraform must not depend on Azure Files mounts or "
                f"shared access keys ('{forbidden_pattern}')"
            )
    for required_env in (
        "ARTIFACT_STORAGE_BACKEND",
        "ARTIFACT_BLOB_ACCOUNT_URL",
        "ARTIFACT_BLOB_CONTAINER_NAME",
        "ARTIFACT_BLOB_MANAGED_IDENTITY_CLIENT_ID",
        "APPLICATIONINSIGHTS_CONNECTION_STRING",
        "OTEL_ENABLED",
        "OTEL_SAMPLER_RATIO",
        "OTEL_SERVICE_NAMESPACE",
        "MODEL_SIGNING_PRIVATE_KEY_PEM",
        "MODEL_SIGNING_PUBLIC_KEY_PEM",
    ):
        if required_env not in terraform_main:
            failures.append(
                "container-apps terraform is missing required artifact / telemetry runtime "
                f"environment '{required_env}'"
            )
    for forbidden_pattern in (
        r"OTEL_EXPORTER_OTLP_ENDPOINT\s*=",
        r"OTEL_EXPORTER_OTLP_PROTOCOL\s*=",
    ):
        if re.search(forbidden_pattern, terraform_main):
            failures.append(
                "container-apps terraform must not override managed "
                "OpenTelemetry endpoint/protocol "
                f"environment injection ('{forbidden_pattern}')"
            )
    if len(failures) == failures_before:
        _print_ok(
            "terraform deployment inputs are pinned and production identity / "
            "secret / telemetry guardrails are enforced"
        )


def _check_branch_protection_policy(
    branch_policy: dict[str, object],
    failures: list[str],
) -> list[dict[str, object]]:
    failures_before = len(failures)
    branches_obj = branch_policy.get("branches")
    branches_list = _as_string_list(branches_obj)
    if branches_list is not None:
        failures.append("branch protection policy branches must be mappings, not strings")
        return []
    if not isinstance(branches_obj, list):
        failures.append("branch protection policy must define a 'branches' list")
        return []

    policies: list[dict[str, object]] = []
    found_main_policy = False
    for branch_obj in cast(list[object], branches_obj):
        branch_policy_entry = _as_string_object_dict(branch_obj)
        if branch_policy_entry is None:
            failures.append("branch protection policy entries must be mappings")
            continue
        policies.append(branch_policy_entry)

        branch_name = branch_policy_entry.get("name")
        if not isinstance(branch_name, str):
            failures.append("branch protection policy entry missing branch name")
            continue
        required_checks = _as_string_list(branch_policy_entry.get("required_status_checks"))
        if required_checks is None:
            failures.append(f"branch protection policy for '{branch_name}' must define checks list")
            continue
        if branch_name == "main":
            found_main_policy = True
            missing_checks = sorted(set(REQUIRED_STATUS_CHECKS).difference(required_checks))
            if missing_checks:
                failures.append(
                    "branch protection policy for 'main' is missing required checks: "
                    + ", ".join(missing_checks)
                )
            if branch_policy_entry.get("require_pull_request") is not True:
                failures.append("branch protection policy for 'main' must require pull requests")
            if branch_policy_entry.get("dismiss_stale_reviews") is not True:
                failures.append("branch protection policy for 'main' must dismiss stale reviews")
            if branch_policy_entry.get("require_conversation_resolution") is not True:
                failures.append(
                    "branch protection policy for 'main' must require conversation resolution"
                )
            if branch_policy_entry.get("allow_force_pushes") is not False:
                failures.append("branch protection policy for 'main' must disallow force pushes")
            if branch_policy_entry.get("allow_deletions") is not False:
                failures.append("branch protection policy for 'main' must disallow deletions")
            approving_reviews = branch_policy_entry.get("required_approving_review_count")
            if not isinstance(approving_reviews, int) or approving_reviews < 1:
                failures.append(
                    "branch protection policy for 'main' must require at least one approval"
                )

    if not found_main_policy:
        failures.append("branch protection policy must define controls for 'main'")
    if len(failures) == failures_before:
        _print_ok("branch protection policy includes required controls")
    return policies


def _extract_job_names(ci_workflow: dict[str, object]) -> set[str]:
    jobs_obj = _as_string_object_dict(ci_workflow.get("jobs"))
    if jobs_obj is None:
        return set()
    return set(jobs_obj)


def _extract_job_step_names(job: dict[str, object]) -> set[str]:
    steps_obj = job.get("steps")
    if not isinstance(steps_obj, list):
        return set()
    names: set[str] = set()
    for step_obj in cast(list[object], steps_obj):
        step = _as_string_object_dict(step_obj)
        if step is None:
            continue
        name = step.get("name")
        if isinstance(name, str):
            names.add(name)
    return names


def _check_ci_workflow(ci_workflow: dict[str, object], failures: list[str]) -> None:
    failures_before = len(failures)
    permissions_obj = _as_string_object_dict(ci_workflow.get("permissions"))
    if permissions_obj is None:
        failures.append("ci.yml must declare top-level permissions")
    elif permissions_obj.get("contents") != "read":
        failures.append("ci.yml top-level permissions must set contents=read")

    job_names = _extract_job_names(ci_workflow)
    missing_jobs = sorted(set(REQUIRED_STATUS_CHECKS).difference(job_names))
    if missing_jobs:
        failures.append(f"ci.yml is missing required jobs: {', '.join(missing_jobs)}")

    jobs_obj = _as_string_object_dict(ci_workflow.get("jobs"))
    if jobs_obj is None:
        failures.append("ci.yml must define jobs")
        return
    quality_obj = _as_string_object_dict(jobs_obj.get("quality"))
    if quality_obj is None:
        failures.append("ci.yml must define quality job")
        return

    quality_step_names = _extract_job_step_names(quality_obj)
    if EXPECTED_CYBERSEC_STEP not in quality_step_names:
        failures.append(
            f"quality job must include cybersecurity posture step named '{EXPECTED_CYBERSEC_STEP}'"
        )

    if len(failures) == failures_before:
        _print_ok("ci workflow exposes required protected-branch checks")


def _check_workflow_action_pins(failures: list[str]) -> None:
    failures_before = len(failures)
    for workflow_path in WORKFLOW_PATHS:
        try:
            workflow_text = workflow_path.read_text(encoding="utf-8")
        except OSError as exc:
            failures.append(
                "unable to read workflow "
                f"'{workflow_path.name}' for action pinning: {exc}"
            )
            continue

        for line_number, line in enumerate(workflow_text.splitlines(), start=1):
            match = ACTION_REFERENCE_PATTERN.match(line)
            if match is None:
                continue
            action, ref = match.groups()
            if not FULL_COMMIT_SHA_PATTERN.fullmatch(ref):
                failures.append(
                    f"{workflow_path.name}:{line_number} action '{action}' "
                    f"must be pinned to a full commit SHA, got '{ref}'"
                )
    if len(failures) == failures_before:
        _print_ok("workflow actions are pinned to full commit SHAs")


def _check_build_sign_workflow(
    build_sign_workflow: dict[str, object],
    build_sign_text: str,
    failures: list[str],
) -> None:
    failures_before = len(failures)
    jobs_obj = _as_string_object_dict(build_sign_workflow.get("jobs"))
    if jobs_obj is None:
        failures.append("build-sign workflow must define jobs")
        return
    jobs = jobs_obj

    build_sign_obj = _as_string_object_dict(jobs.get("build-sign"))
    if build_sign_obj is None:
        failures.append("build-sign workflow must define job 'build-sign'")
        return
    build_sign = build_sign_obj

    permissions_obj = _as_string_object_dict(build_sign.get("permissions"))
    if permissions_obj is None:
        failures.append("build-sign job must define explicit permissions")
    else:
        required_permissions = {
            "contents": "read",
            "packages": "write",
            "id-token": "write",
            "attestations": "write",
        }
        for permission_name, permission_value in required_permissions.items():
            if permissions_obj.get(permission_name) != permission_value:
                failures.append(
                    "build-sign job permission "
                    f"'{permission_name}' must be set to '{permission_value}'"
                )

    strategy_obj = _as_string_object_dict(build_sign.get("strategy"))
    if strategy_obj is None:
        failures.append("build-sign job must define strategy matrix")
        return
    matrix_obj = _as_string_object_dict(strategy_obj.get("matrix"))
    if matrix_obj is None:
        failures.append("build-sign job must define matrix")
        return
    include_obj = matrix_obj.get("include")
    if not isinstance(include_obj, list):
        failures.append("build-sign matrix must define include list")
        return

    discovered_services: set[str] = set()
    for include_entry_obj in cast(list[object], include_obj):
        include_entry = _as_string_object_dict(include_entry_obj)
        if include_entry is None:
            failures.append("build-sign matrix include entries must be mappings")
            continue
        service_name = include_entry.get("service")
        if not isinstance(service_name, str):
            failures.append("build-sign matrix include entry missing 'service' string")
            continue
        discovered_services.add(service_name)

    missing_services = sorted(set(REQUIRED_SIGNED_SERVICES).difference(discovered_services))
    if missing_services:
        failures.append(
            "build-sign matrix is missing signed service entries: " + ", ".join(missing_services)
        )

    consolidate_obj = _as_string_object_dict(jobs.get("consolidate-digests"))
    if consolidate_obj is None:
        failures.append("build-sign workflow must define job 'consolidate-digests'")
    else:
        needs_obj = consolidate_obj.get("needs")
        needs_values: set[str] = set()
        if isinstance(needs_obj, str):
            needs_values.add(needs_obj)
        elif isinstance(needs_obj, list):
            for needs_entry in cast(list[object], needs_obj):
                if isinstance(needs_entry, str):
                    needs_values.add(needs_entry)
        else:
            failures.append("consolidate-digests must depend on build-sign job")
        if "build-sign" not in needs_values:
            failures.append("consolidate-digests must list 'build-sign' in needs")

    required_build_patterns = (
        "provenance: mode=max",
        "sbom: true",
        "aquasecurity/trivy-action",
        "cosign verify",
        "actions/attest-build-provenance",
        "scripts/ci/render_image_tfvars.py",
        "container-apps.auto.tfvars.json",
    )
    for pattern in required_build_patterns:
        if pattern not in build_sign_text:
            failures.append(f"build-sign workflow must include '{pattern}'")

    if len(failures) == failures_before:
        _print_ok("build-sign workflow signs, scans, and attests all service images")


def _fetch_remote_branch_protection(
    repository: str,
    branch_name: str,
    token: str,
) -> dict[str, object]:
    connection = http.client.HTTPSConnection("api.github.com", timeout=10)
    path = f"/repos/{repository}/branches/{quote(branch_name, safe='')}/protection"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {token}",
        "User-Agent": "credit-ai-ops-platform/cybersec-gate",
        "X-GitHub-Api-Version": "2022-11-28",
    }
    connection.request("GET", path, headers=headers)
    response = connection.getresponse()
    try:
        if response.status >= 400:
            response_body = response.read().decode("utf-8", errors="replace")
            raise OSError(
                f"GitHub API returned {response.status} for branch '{branch_name}': {response_body}"
            )
        payload = json.loads(response.read().decode("utf-8"))
    finally:
        connection.close()
    if not isinstance(payload, dict):
        raise ValueError("GitHub branch protection response must be a dictionary")
    return cast(dict[str, object], payload)


def _remote_flag(payload: dict[str, object], key: str) -> bool | None:
    nested = _as_string_object_dict(payload.get(key))
    if nested is None:
        return None
    enabled = nested.get("enabled")
    return enabled if isinstance(enabled, bool) else None


def _check_remote_branch_protection(
    branch_policies: list[dict[str, object]],
    failures: list[str],
) -> None:
    explicit_token = os.getenv("BRANCH_PROTECTION_TOKEN")
    fallback_token = os.getenv("GITHUB_TOKEN")
    token = explicit_token or fallback_token
    using_fallback_github_token = explicit_token is None and fallback_token is not None
    repository = os.getenv("GITHUB_REPOSITORY")
    strict = os.getenv("ENFORCE_REMOTE_BRANCH_PROTECTION") == "1"

    if token is None or repository is None:
        if strict:
            failures.append(
                "remote branch protection validation is enforced but "
                "GITHUB_REPOSITORY/token missing"
            )
        else:
            _print_note(
                "skipping remote branch protection validation; no repository token configured"
            )
        return

    failures_before = len(failures)
    for policy in branch_policies:
        branch_name = policy.get("name")
        if not isinstance(branch_name, str):
            continue
        required_checks = _as_string_list(policy.get("required_status_checks"))
        if required_checks is None:
            continue

        try:
            remote_payload = _fetch_remote_branch_protection(repository, branch_name, token)
        except (OSError, ValueError) as exc:
            if (
                using_fallback_github_token
                and (
                    "GitHub API returned 401" in str(exc)
                    or "GitHub API returned 403" in str(exc)
                )
            ):
                if strict:
                    failures.append(
                        "remote branch protection validation is enforced but github.token "
                        "does not have branch-protection scope. Configure "
                        "BRANCH_PROTECTION_TOKEN to enforce this control."
                    )
                    continue
                _print_note(
                    "skipping remote branch protection validation; github.token "
                    "does not have branch-protection scope. Configure "
                    "BRANCH_PROTECTION_TOKEN to enforce this control."
                )
                return
            failures.append(
                f"unable to validate remote branch protection for '{branch_name}': {exc}"
            )
            continue

        remote_checks_obj = _as_string_object_dict(remote_payload.get("required_status_checks"))
        remote_contexts = (
            _as_string_list(remote_checks_obj.get("contexts"))
            if remote_checks_obj is not None
            else None
        )
        if remote_contexts is None:
            failures.append(f"remote branch '{branch_name}' is missing required status checks")
        else:
            missing_checks = sorted(set(required_checks).difference(remote_contexts))
            if missing_checks:
                failures.append(
                    f"remote branch '{branch_name}' is missing status checks: "
                    + ", ".join(missing_checks)
                )

        remote_reviews = _as_string_object_dict(remote_payload.get("required_pull_request_reviews"))
        if policy.get("require_pull_request") is True and remote_reviews is None:
            failures.append(f"remote branch '{branch_name}' must require pull request reviews")
        if remote_reviews is not None:
            approval_count = remote_reviews.get("required_approving_review_count")
            expected_approvals = policy.get("required_approving_review_count")
            if approval_count != expected_approvals:
                failures.append(
                    f"remote branch '{branch_name}' approval count must be {expected_approvals}, "
                    f"got {approval_count}"
                )
            if remote_reviews.get("dismiss_stale_reviews") != policy.get("dismiss_stale_reviews"):
                failures.append(
                    f"remote branch '{branch_name}' dismiss_stale_reviews does not match policy"
                )

        if _remote_flag(remote_payload, "allow_force_pushes") != policy.get("allow_force_pushes"):
            failures.append(
                f"remote branch '{branch_name}' allow_force_pushes does not match policy"
            )
        if _remote_flag(remote_payload, "allow_deletions") != policy.get("allow_deletions"):
            failures.append(f"remote branch '{branch_name}' allow_deletions does not match policy")
        if _remote_flag(
            remote_payload,
            "required_conversation_resolution",
        ) != policy.get("require_conversation_resolution"):
            failures.append(
                "remote branch "
                f"'{branch_name}' required_conversation_resolution does not match policy"
            )

    if len(failures) == failures_before:
        _print_ok("remote branch protection matches repository policy")


def main() -> int:
    failures: list[str] = []

    try:
        pyproject = _load_toml(PYPROJECT_PATH)
        compose = _load_yaml(COMPOSE_PATH)
        terraform_main = TERRAFORM_MAIN_PATH.read_text(encoding="utf-8")
        terraform_variables = TERRAFORM_VARIABLES_PATH.read_text(encoding="utf-8")
        branch_policy = _load_json(BRANCH_PROTECTION_PATH)
        ci_workflow = _load_yaml(CI_WORKFLOW_PATH)
        build_sign_workflow = _load_yaml(BUILD_SIGN_WORKFLOW_PATH)
        build_sign_text = BUILD_SIGN_WORKFLOW_PATH.read_text(encoding="utf-8")
    except (
        OSError,
        ValueError,
        json.JSONDecodeError,
        tomllib.TOMLDecodeError,
        yaml.YAMLError,
    ) as exc:
        _print_fail(f"failed to load security posture inputs: {exc}")
        return 1

    _check_python_runtime(pyproject, failures)
    _check_dependency_pinning(pyproject, failures)
    _check_docker_image_pinning(compose, failures)
    _check_terraform_pinning(terraform_main, terraform_variables, failures)
    branch_policies = _check_branch_protection_policy(branch_policy, failures)
    _check_remote_branch_protection(branch_policies, failures)
    _check_ci_workflow(ci_workflow, failures)
    _check_build_sign_workflow(build_sign_workflow, build_sign_text, failures)
    _check_workflow_action_pins(failures)

    if failures:
        for failure in failures:
            _print_fail(failure)
        return 1

    print("[cybersec-gate] All cybersecurity posture checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
