"""ADR governance gate enforcing mandatory architecture decisions."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ADR_DIR = ROOT / "docs" / "adr"
ADR_README = ADR_DIR / "README.md"


@dataclass(frozen=True, slots=True)
class AdrRequirement:
    filename: str
    required_phrases: tuple[str, ...]


REQUIRED_ADRS = (
    AdrRequirement(
        filename="0001-service-boundaries.md",
        required_phrases=("service boundaries", "9-service architecture"),
    ),
    AdrRequirement(
        filename="0002-event-schema-versioning.md",
        required_phrases=("event schema", "openapi + asyncapi + json schema"),
    ),
    AdrRequirement(
        filename="0003-retry-idempotency-strategy.md",
        required_phrases=("retry", "idempotency", "circuit-breaker"),
    ),
    AdrRequirement(
        filename="0004-model-promotion-strategy.md",
        required_phrases=("model promotion", "deterministic", "model cards"),
    ),
    AdrRequirement(
        filename="0005-azure-deployment-decisions.md",
        required_phrases=("azure deployment", "container apps", "aks"),
    ),
)

ADR_TITLE_PATTERN = re.compile(r"^# ADR \d{4}: .+")
REQUIRED_SECTIONS = ("## Status", "## Decision", "## Rationale")


def _print_ok(message: str) -> None:
    print(f"[adr-gate] OK {message}")


def _print_fail(message: str) -> None:
    print(f"[adr-gate] FAIL {message}")


def _extract_section(markdown: str, section_header: str) -> str:
    marker = f"{section_header}\n"
    start = markdown.find(marker)
    if start < 0:
        return ""
    body_start = start + len(marker)
    section_body = markdown[body_start:]
    next_section = section_body.find("\n## ")
    if next_section >= 0:
        return section_body[:next_section].strip()
    return section_body.strip()


def _validate_adr_readme(failures: list[str]) -> None:
    try:
        readme_text = ADR_README.read_text(encoding="utf-8")
    except OSError as exc:
        failures.append(f"unable to read ADR policy file '{ADR_README}': {exc}")
        return
    if "No major design change is allowed without a corresponding ADR update." not in readme_text:
        failures.append("ADR policy must include mandatory design change rule")
        return
    _print_ok("ADR policy contains mandatory design change rule")


def _validate_required_adr(requirement: AdrRequirement, failures: list[str]) -> None:
    file_path = ADR_DIR / requirement.filename
    if not file_path.exists():
        failures.append(f"required ADR file is missing: {requirement.filename}")
        return
    try:
        text = file_path.read_text(encoding="utf-8")
    except OSError as exc:
        failures.append(f"unable to read ADR file '{requirement.filename}': {exc}")
        return

    lines = text.splitlines()
    if not lines:
        failures.append(f"{requirement.filename}: file is empty")
        return
    if ADR_TITLE_PATTERN.match(lines[0]) is None:
        failures.append(
            f"{requirement.filename}: first line must match '{ADR_TITLE_PATTERN.pattern}'"
        )

    for section in REQUIRED_SECTIONS:
        if section not in text:
            failures.append(f"{requirement.filename}: missing required section '{section}'")

    lowered = text.lower()
    status_body = _extract_section(text, "## Status").lower()
    if "accepted" not in status_body:
        failures.append(f"{requirement.filename}: status must be Accepted")

    for phrase in requirement.required_phrases:
        if phrase.lower() not in lowered:
            failures.append(f"{requirement.filename}: missing required phrase '{phrase}'")

    if not any(failure.startswith(requirement.filename) for failure in failures):
        _print_ok(f"{requirement.filename} satisfies governance requirements")


def main() -> int:
    failures: list[str] = []
    _validate_adr_readme(failures)
    for requirement in REQUIRED_ADRS:
        _validate_required_adr(requirement, failures)

    if failures:
        for failure in failures:
            _print_fail(failure)
        return 1

    print("[adr-gate] ADR governance checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
