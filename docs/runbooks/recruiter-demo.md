# Recruiter Demo Runbook

## Objective
Provide one command that validates the platform with security controls, integration evidence,
and real networked execution evidence.

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
6. Runs the relay-only async credit chain integration test (`pytest tests/integration/test_async_credit_chain.py -q`)
7. Runs the real HTTP gateway end-to-end test (`pytest tests/e2e/test_gateway_http_stack.py -q`)
8. Writes a report to `build/recruiter-demo-report.md`
9. Generates reviewer scorecard artifacts (`build/reviewer-scorecard.md`, `build/reviewer-scorecard.json`)

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
