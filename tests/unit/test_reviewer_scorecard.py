from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import cast

import pytest
from scripts.ci import generate_reviewer_scorecard as scorecard_module


def _receipt_payload(
    *,
    command: str = "make bank-cybersec-gate",
    status: str = "PASS",
    generated_at_utc: str = "2026-03-07T22:00:00Z",
    git_commit: str = "d" * 40,
    git_branch: str = "codex/test-branch",
    ci_run_id: str | None = None,
) -> dict[str, str]:
    payload = {
        "command": command,
        "status": status,
        "generated_at_utc": generated_at_utc,
        "git_commit": git_commit,
        "git_branch": git_branch,
    }
    if ci_run_id is not None:
        payload["ci_run_id"] = ci_run_id
    return payload


def _load_receipt_parser() -> Callable[[str], scorecard_module.ValidationReceipt]:
    parser = scorecard_module.__dict__["_existing_validation_receipt"]
    return cast(Callable[[str], scorecard_module.ValidationReceipt], parser)


@pytest.mark.unit
def test_generate_scorecard_renders_expected_sections() -> None:
    payload = scorecard_module.generate_scorecard(
        validations=[
            scorecard_module.ValidationReceipt(
                label="cybersec",
                command="make bank-cybersec-gate",
                status="PASS",
                receipt_path="build/reviewer-validations/cybersec.json",
                generated_at_utc="2026-03-07T22:00:00Z",
                git_commit="d" * 40,
                git_branch="codex/test-branch",
                ci_run_id=None,
            ),
            scorecard_module.ValidationReceipt(
                label="gateway_http_e2e",
                command="pytest tests/e2e/test_gateway_http_stack.py -q",
                status="PASS",
                receipt_path="build/reviewer-validations/gateway_http_e2e.json",
                generated_at_utc="2026-03-07T22:05:00Z",
                git_commit="d" * 40,
                git_branch="codex/test-branch",
                ci_run_id=None,
            ),
        ],
        artifacts=[
            scorecard_module.ScorecardArtifact(
                label="report",
                path="build/recruiter-demo-report.md",
            ),
            scorecard_module.ScorecardArtifact(
                label="mlops",
                path="build/recruiter-ml-evidence.json",
            ),
        ],
        git_commit="d" * 40,
        git_branch="codex/test-branch",
        generated_at_utc="2026-03-07T22:10:00Z",
    )

    markdown = scorecard_module.render_markdown(payload)

    assert "# Reviewer Scorecard" in markdown
    assert "`PASS`" in markdown
    assert "`make bank-cybersec-gate`" in markdown
    assert "`build/recruiter-ml-evidence.json`" in markdown
    assert "Security and Governance" in markdown
    assert "Observability and Audit" in markdown
    assert "tests/e2e/test_gateway_http_stack.py" in markdown


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
        json.dumps(_receipt_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    report.write_text("# report\n", encoding="utf-8")
    evidence.write_text("{}\n", encoding="utf-8")

    markdown_output = tmp_path / "reviewer-scorecard.md"
    json_output = tmp_path / "reviewer-scorecard.json"
    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("GITHUB_RUN_ID", raising=False)
    monkeypatch.setattr(scorecard_module, "_git_commit", lambda: "d" * 40)
    monkeypatch.setattr(scorecard_module, "_git_branch", lambda: "codex/test-branch")
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
            "d" * 40,
            "--git-branch",
            "codex/test-branch",
            "--generated-at-utc",
            "2026-03-07T22:10:00Z",
        ],
    )

    assert scorecard_module.main() == 0
    assert markdown_output.exists()
    assert json_output.exists()

    json_payload = json.loads(json_output.read_text(encoding="utf-8"))
    assert json_payload["status"] == "PASS"
    assert json_payload["git_commit"] == "d" * 40
    assert json_payload["git_branch"] == "codex/test-branch"
    assert json_payload["artifacts"][0]["label"] == "report"
    assert json_payload["validations"][0]["label"] == "cybersec"


@pytest.mark.unit
def test_existing_validation_receipt_rejects_mismatched_commit(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "cybersec.json"
    receipt.write_text(
        json.dumps(_receipt_payload(git_commit="a" * 40), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(scorecard_module, "_git_commit", lambda: "b" * 40)

    with pytest.raises(ValueError, match="git_commit does not match current HEAD"):
        _ = _load_receipt_parser()(f"cybersec={receipt}")


@pytest.mark.unit
def test_existing_validation_receipt_rejects_missing_ci_run_id_in_actions(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    receipt = tmp_path / "cybersec.json"
    receipt.write_text(
        json.dumps(_receipt_payload(), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(scorecard_module, "_git_commit", lambda: "d" * 40)
    monkeypatch.setenv("GITHUB_RUN_ID", "12345")

    with pytest.raises(
        ValueError,
        match="must include ci_run_id when generated under GitHub Actions",
    ):
        _ = _load_receipt_parser()(f"cybersec={receipt}")
