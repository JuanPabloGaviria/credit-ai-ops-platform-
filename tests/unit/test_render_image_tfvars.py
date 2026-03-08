from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.ci.render_image_tfvars import main, render_tfvars_payload


def _write_manifest(path: Path, lines: list[str]) -> None:
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def test_render_tfvars_payload_returns_sorted_digest_map(tmp_path: Path) -> None:
    manifest_path = tmp_path / "image-digests.txt"
    digest = "a" * 64
    _write_manifest(
        manifest_path,
        [
            f"scoring=ghcr.io/example/scoring@sha256:{digest}",
            f"application=ghcr.io/example/application@sha256:{digest}",
            f"api-gateway=ghcr.io/example/api-gateway@sha256:{digest}",
            f"decision=ghcr.io/example/decision@sha256:{digest}",
            f"feature=ghcr.io/example/feature@sha256:{digest}",
            f"collab-assistant=ghcr.io/example/collab-assistant@sha256:{digest}",
            f"mlops=ghcr.io/example/mlops@sha256:{digest}",
            f"observability-audit=ghcr.io/example/observability-audit@sha256:{digest}",
        ],
    )

    payload = render_tfvars_payload(manifest_path)

    assert list(payload["service_image_references"]) == sorted(payload["service_image_references"])
    assert payload["service_image_references"]["api-gateway"].endswith(digest)


def test_render_tfvars_payload_rejects_missing_service(tmp_path: Path) -> None:
    manifest_path = tmp_path / "image-digests.txt"
    digest = "b" * 64
    _write_manifest(
        manifest_path,
        [
            f"api-gateway=ghcr.io/example/api-gateway@sha256:{digest}",
        ],
    )

    with pytest.raises(ValueError, match="missing required services"):
        render_tfvars_payload(manifest_path)


def test_render_tfvars_main_writes_auto_tfvars_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manifest_path = tmp_path / "image-digests.txt"
    output_path = tmp_path / "container-apps.auto.tfvars.json"
    digest = "c" * 64
    _write_manifest(
        manifest_path,
        [
            f"api-gateway=ghcr.io/example/api-gateway@sha256:{digest}",
            f"application=ghcr.io/example/application@sha256:{digest}",
            f"feature=ghcr.io/example/feature@sha256:{digest}",
            f"scoring=ghcr.io/example/scoring@sha256:{digest}",
            f"decision=ghcr.io/example/decision@sha256:{digest}",
            f"collab-assistant=ghcr.io/example/collab-assistant@sha256:{digest}",
            f"mlops=ghcr.io/example/mlops@sha256:{digest}",
            f"observability-audit=ghcr.io/example/observability-audit@sha256:{digest}",
        ],
    )
    monkeypatch.setattr(
        "sys.argv",
        [
            "render_image_tfvars.py",
            "--manifest",
            str(manifest_path),
            "--output",
            str(output_path),
        ],
    )

    assert main() == 0
    payload = json.loads(output_path.read_text(encoding="utf-8"))
    assert payload["service_image_references"]["observability-audit"].endswith(digest)
