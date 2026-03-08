"""Enforce reviewer-facing documentation tone for markdown assets."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MARKDOWN_GLOBS = (
    "README.md",
    "docs/**/*.md",
    "infra/**/*.md",
    "services/**/*.md",
)


@dataclass(frozen=True, slots=True)
class DisallowedPattern:
    pattern: re.Pattern[str]
    reason: str


DISALLOWED_PATTERNS = (
    DisallowedPattern(
        pattern=re.compile(r"\bsuggested interview framing\b", re.IGNORECASE),
        reason="coaching language is not reviewer-facing",
    ),
    DisallowedPattern(
        pattern=re.compile(r"\b(you|your|we|our)\b", re.IGNORECASE),
        reason="direct conversational pronouns are not allowed in reviewer-facing docs",
    ),
)


def _iter_markdown_paths() -> list[Path]:
    discovered: set[Path] = set()
    for pattern in MARKDOWN_GLOBS:
        for path in ROOT.glob(pattern):
            if path.is_file():
                discovered.add(path)
    return sorted(discovered)


def main() -> int:
    failures: list[str] = []
    paths = _iter_markdown_paths()
    if not paths:
        print("[docs-audience-lint] FAIL no markdown files discovered")
        return 1

    for path in paths:
        text = path.read_text(encoding="utf-8")
        for disallowed in DISALLOWED_PATTERNS:
            for match in disallowed.pattern.finditer(text):
                line_number = text.count("\n", 0, match.start()) + 1
                rel_path = path.relative_to(ROOT)
                failures.append(
                    f"{rel_path}:{line_number}: disallowed phrase '{match.group(0)}' "
                    f"({disallowed.reason})"
                )

    if failures:
        for failure in failures:
            print(f"[docs-audience-lint] FAIL {failure}")
        return 1

    print(f"[docs-audience-lint] reviewer-facing tone validated for {len(paths)} markdown files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
