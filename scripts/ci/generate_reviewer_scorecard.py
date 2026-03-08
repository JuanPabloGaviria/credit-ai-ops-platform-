"""Generate a reviewer-grade scorecard bundle from validated repo artifacts."""

from __future__ import annotations

import argparse
import json
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

ROOT = Path(__file__).resolve().parents[2]


@dataclass(frozen=True)
class ScorecardArtifact:
    label: str
    path: str


@dataclass(frozen=True)
class ValidationReceipt:
    label: str
    command: str
    status: str
    receipt_path: str


@dataclass(frozen=True)
class ScorecardSection:
    title: str
    outcome: str
    evidence: list[str]


@dataclass(frozen=True)
class ScorecardPayload:
    status: str
    generated_at_utc: str
    git_commit: str
    git_branch: str
    validations: list[ValidationReceipt]
    artifacts: list[ScorecardArtifact]
    sections: list[ScorecardSection]


def generate_scorecard(
    *,
    validations: Sequence[ValidationReceipt],
    artifacts: Sequence[ScorecardArtifact],
    git_commit: str,
    git_branch: str,
    generated_at_utc: str,
) -> ScorecardPayload:
    overall_status = (
        "PASS" if validations and all(item.status == "PASS" for item in validations) else "FAIL"
    )
    return ScorecardPayload(
        status=overall_status,
        generated_at_utc=generated_at_utc,
        git_commit=git_commit,
        git_branch=git_branch,
        validations=list(validations),
        artifacts=list(artifacts),
        sections=[
            ScorecardSection(
                title="Security and Governance",
                outcome=(
                    "Branch policy, supply-chain controls, and runtime secret posture are "
                    "enforced through CI and Terraform gates."
                ),
                evidence=[
                    ".github/workflows/ci.yml",
                    ".github/workflows/build-sign.yml",
                    "scripts/ci/cybersec_gate.py",
                    "infra/terraform/container-apps/main.tf",
                ],
            ),
            ScorecardSection(
                title="Observability and Audit",
                outcome=(
                    "OpenTelemetry traces are exported through the managed Azure Container Apps "
                    "path, and audit lineage is queryable by trace and correlation identifiers."
                ),
                evidence=[
                    "docs/runbooks/observability.md",
                    "services/observability-audit/src/observability_audit/routes.py",
                    "infra/terraform/container-apps/main.tf",
                ],
            ),
            ScorecardSection(
                title="MLOps and Serving Integrity",
                outcome=(
                    "Promoted artifacts are immutable, signed, digest-verified, and served from "
                    "the registry-backed runtime path."
                ),
                evidence=[
                    "services/mlops/src/mlops_service/lifecycle.py",
                    "services/scoring/src/scoring_service/runtime.py",
                    "build/recruiter-ml-evidence.json",
                ],
            ),
            ScorecardSection(
                title="Async Runtime Reliability",
                outcome=(
                    "The credit decision chain is verified through relay-only integration tests "
                    "with append-only history and audit capture."
                ),
                evidence=[
                    "tests/integration/test_async_credit_chain.py",
                    "packages/shared-kernel/src/shared_kernel/outbox.py",
                    "packages/shared-kernel/src/shared_kernel/resilience.py",
                ],
            ),
        ],
    )


def render_markdown(payload: ScorecardPayload) -> str:
    lines = [
        "# Reviewer Scorecard",
        "",
        f"- Status: {payload.status}",
        f"- Generated (UTC): {payload.generated_at_utc}",
        f"- Git commit: `{payload.git_commit}`",
        f"- Git branch: `{payload.git_branch}`",
        "",
        "## Validations",
    ]
    lines.extend(
        (
            f"- {validation.label}: `{validation.status}` "
            f"`{validation.command}` ({validation.receipt_path})"
        )
        for validation in payload.validations
    )
    lines.extend(["", "## Artifacts"])
    lines.extend(f"- {artifact.label}: `{artifact.path}`" for artifact in payload.artifacts)
    for section in payload.sections:
        lines.extend(["", f"## {section.title}", section.outcome, "", "Evidence:"])
        lines.extend(f"- `{entry}`" for entry in section.evidence)
    return "\n".join(lines) + "\n"


def write_scorecard(
    *,
    markdown_output: Path,
    json_output: Path,
    payload: ScorecardPayload,
) -> None:
    markdown_output.parent.mkdir(parents=True, exist_ok=True)
    json_output.parent.mkdir(parents=True, exist_ok=True)
    markdown_output.write_text(render_markdown(payload), encoding="utf-8")
    json_output.write_text(
        json.dumps(asdict(payload), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _git_commit() -> str:
    head_ref = (ROOT / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    if head_ref.startswith("ref: "):
        ref_path = ROOT / ".git" / head_ref.removeprefix("ref: ")
        return ref_path.read_text(encoding="utf-8").strip()
    return head_ref


def _git_branch() -> str:
    head_ref = (ROOT / ".git" / "HEAD").read_text(encoding="utf-8").strip()
    if head_ref.startswith("ref: "):
        return head_ref.removeprefix("ref: ").split("/", maxsplit=2)[-1]
    return "detached-head"


def _existing_artifact(value: str) -> ScorecardArtifact:
    label, separator, raw_path = value.partition("=")
    if separator == "":
        raise ValueError("artifact arguments must use label=path format")
    path = Path(raw_path)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        raise ValueError(f"artifact path does not exist: {path}")
    try:
        display_path = str(path.relative_to(ROOT))
    except ValueError:
        display_path = str(path)
    return ScorecardArtifact(label=label, path=display_path)


def _existing_validation_receipt(value: str) -> ValidationReceipt:
    label, separator, raw_path = value.partition("=")
    if separator == "":
        raise ValueError("validation-receipt arguments must use label=path format")
    path = Path(raw_path)
    if not path.is_absolute():
        path = ROOT / path
    if not path.exists():
        raise ValueError(f"validation receipt path does not exist: {path}")
    with path.open("r", encoding="utf-8") as handle:
        payload = json.load(handle)
    if not isinstance(payload, dict):
        raise ValueError(f"validation receipt must be a JSON object: {path}")
    payload_dict = cast(dict[str, object], payload)
    command = payload_dict.get("command")
    status = payload_dict.get("status")
    if not isinstance(command, str) or command.strip() == "":
        raise ValueError(f"validation receipt command must be a non-empty string: {path}")
    if status not in {"PASS", "FAIL"}:
        raise ValueError(f"validation receipt status must be PASS or FAIL: {path}")
    normalized_status = cast(str, status)
    try:
        display_path = str(path.relative_to(ROOT))
    except ValueError:
        display_path = str(path)
    return ValidationReceipt(
        label=label,
        command=command,
        status=normalized_status,
        receipt_path=display_path,
    )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Generate reviewer-grade scorecard artifacts for hardened repo evidence",
    )
    parser.add_argument(
        "--markdown-output",
        default="build/reviewer-scorecard.md",
        help="Markdown scorecard output path",
    )
    parser.add_argument(
        "--json-output",
        default="build/reviewer-scorecard.json",
        help="JSON scorecard output path",
    )
    parser.add_argument(
        "--validation-receipt",
        action="append",
        default=[],
        help="Validation receipt entry using label=path",
    )
    parser.add_argument(
        "--artifact",
        action="append",
        default=[],
        help="Artifact entry using label=path",
    )
    parser.add_argument("--git-commit", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--git-branch", default=None, help=argparse.SUPPRESS)
    parser.add_argument("--generated-at-utc", default=None, help=argparse.SUPPRESS)
    return parser.parse_args()


def main() -> int:
    args = _parse_args()
    validations = [_existing_validation_receipt(entry) for entry in args.validation_receipt]
    if not validations:
        raise ValueError("at least one validation receipt is required")
    artifacts = [_existing_artifact(entry) for entry in args.artifact]
    payload = generate_scorecard(
        validations=validations,
        artifacts=artifacts,
        git_commit=args.git_commit or _git_commit(),
        git_branch=args.git_branch or _git_branch(),
        generated_at_utc=(
            args.generated_at_utc or datetime.now(tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
        ),
    )
    write_scorecard(
        markdown_output=Path(args.markdown_output),
        json_output=Path(args.json_output),
        payload=payload,
    )
    print(f"[reviewer-scorecard] markdown={args.markdown_output}")
    print(f"[reviewer-scorecard] json={args.json_output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
