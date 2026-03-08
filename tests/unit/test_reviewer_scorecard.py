from __future__ import annotations

import json
from pathlib import Path

import pytest
from scripts.ci.generate_reviewer_scorecard import (
    ScorecardArtifact,
    ValidationReceipt,
    generate_scorecard,
    main,
    render_markdown,
)


@pytest.mark.unit
def test_generate_scorecard_renders_expected_sections() -> None:
    payload = generate_scorecard(
        validations=[
            ValidationReceipt(
                label="cybersec",
                command="make bank-cybersec-gate",
                status="PASS",
                receipt_path="build/reviewer-validations/cybersec.json",
            ),
            ValidationReceipt(
                label="integration",
                command="pytest -m integration -q",
                status="PASS",
                receipt_path="build/reviewer-validations/integration.json",
            ),
        ],
        artifacts=[
            ScorecardArtifact(label="report", path="build/recruiter-demo-report.md"),
            ScorecardArtifact(label="mlops", path="build/recruiter-ml-evidence.json"),
        ],
        git_commit="deadbeef",
        git_branch="codex/test-branch",
        generated_at_utc="2026-03-07T22:00:00Z",
    )

    markdown = render_markdown(payload)

    assert "# Reviewer Scorecard" in markdown
    assert "`PASS`" in markdown
    assert "`make bank-cybersec-gate`" in markdown
    assert "`build/recruiter-ml-evidence.json`" in markdown
    assert "Security and Governance" in markdown
    assert "Observability and Audit" in markdown


@pytest.mark.unit
def test_main_writes_markdown_and_json_outputs(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    report = tmp_path / "recruiter-demo-report.md"
    evidence = tmp_path / "recruiter-ml-evidence.json"
    validation_dir = tmp_path / "reviewer-validations"
    validation_dir.mkdir()
    cybersec = validation_dir / "cybersec.json"
    cybersec.write_text(
        json.dumps(
            {
                "command": "make bank-cybersec-gate",
                "status": "PASS",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    report.write_text("# report\n", encoding="utf-8")
    evidence.write_text("{}\n", encoding="utf-8")

    markdown_output = tmp_path / "reviewer-scorecard.md"
    json_output = tmp_path / "reviewer-scorecard.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "sys.argv",
        [
            "generate_reviewer_scorecard.py",
            "--markdown-output",
            str(markdown_output),
            "--json-output",
            str(json_output),
            "--validation-receipt",
            f"cybersec={cybersec}",
            "--artifact",
            f"report={report}",
            "--artifact",
            f"mlops={evidence}",
            "--git-commit",
            "deadbeef",
            "--git-branch",
            "codex/test-branch",
            "--generated-at-utc",
            "2026-03-07T22:00:00Z",
        ],
    )

    assert main() == 0
    assert markdown_output.exists()
    assert json_output.exists()

    json_payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert json_payload["status"] == "PASS"
    assert json_payload["git_commit"] == "deadbeef"
    assert json_payload["git_branch"] == "codex/test-branch"
    assert json_payload["artifacts"][0]["label"] == "report"
    assert json_payload["validations"][0]["label"] == "cybersec"
