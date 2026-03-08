# Recruiter Demo Runbook

## Objective
Provide one command that validates the platform with security controls, integration evidence,
and async pipeline baseline checks.

## Command
```bash
make recruiter-demo
```

Full pre-share command:
```bash
make release-ready
```

## What It Does
1. Starts local dependencies (`postgres`, `rabbitmq`) with Docker Compose
2. Waits for dependency readiness
3. Applies all service migrations
4. Generates deterministic MLOps evidence artifacts (`build/recruiter-ml-evidence.json`)
5. Runs bank-grade cybersecurity gate (`make bank-cybersec-gate`)
6. Runs integration tests plus async pipeline baseline checks (`pytest -m "integration or perf"`)
7. Writes a report to `build/recruiter-demo-report.md`
8. Generates reviewer scorecard artifacts (`build/reviewer-scorecard.md`, `build/reviewer-scorecard.json`)

## Expected Output
- Console line: `[recruiter-demo] recruiter demo passed`
- Report file: `build/recruiter-demo-report.md`
- MLOps evidence file: `build/recruiter-ml-evidence.json`
- Reviewer scorecard: `build/reviewer-scorecard.md`
- Reviewer scorecard JSON: `build/reviewer-scorecard.json`

## Failure Behavior
- Fail-fast and fail-descriptive
- Report file is still generated on failure with:
  - failing step
  - UTC timestamp
  - recovery command
