"""Render Terraform tfvars JSON from a signed image digest manifest."""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

REQUIRED_SERVICES = (
    "api-gateway",
    "application",
    "feature",
    "scoring",
    "decision",
    "collab-assistant",
    "mlops",
    "observability-audit",
)
IMAGE_REFERENCE_PATTERN = re.compile(r"^[^\s=]+/[^\s=]+(?:/[^\s=]+)*@sha256:[0-9a-f]{64}$")


def _parse_manifest_line(raw_line: str, *, line_number: int) -> tuple[str, str] | None:
    line = raw_line.strip()
    if not line:
        return None
    if "=" not in line:
        raise ValueError(f"manifest line {line_number} must be '<service>=<image@sha256:digest>'")
    service_name, image_reference = line.split("=", maxsplit=1)
    service_name = service_name.strip()
    image_reference = image_reference.strip()
    if service_name not in REQUIRED_SERVICES:
        raise ValueError(f"manifest line {line_number} uses unknown service '{service_name}'")
    if not IMAGE_REFERENCE_PATTERN.fullmatch(image_reference):
        raise ValueError(
            f"manifest line {line_number} for service '{service_name}' is not digest-pinned"
        )
    return service_name, image_reference


def render_tfvars_payload(manifest_path: Path) -> dict[str, dict[str, str]]:
    references: dict[str, str] = {}
    for line_number, raw_line in enumerate(
        manifest_path.read_text(encoding="utf-8").splitlines(),
        start=1,
    ):
        parsed = _parse_manifest_line(raw_line, line_number=line_number)
        if parsed is None:
            continue
        service_name, image_reference = parsed
        if service_name in references:
            raise ValueError(f"manifest contains duplicate service entry '{service_name}'")
        references[service_name] = image_reference

    missing_services = sorted(set(REQUIRED_SERVICES).difference(references))
    if missing_services:
        raise ValueError(
            "manifest is missing required services: " + ", ".join(missing_services)
        )

    return {"service_image_references": dict(sorted(references.items()))}


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Render Terraform tfvars JSON from a build-sign image digest manifest."
    )
    parser.add_argument(
        "--manifest",
        required=True,
        type=Path,
        help="Path to the image-digests.txt manifest produced by build-sign workflow.",
    )
    parser.add_argument(
        "--output",
        required=True,
        type=Path,
        help="Destination Terraform .auto.tfvars.json path.",
    )
    args = parser.parse_args()

    payload = render_tfvars_payload(args.manifest)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"[render-image-tfvars] wrote {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
