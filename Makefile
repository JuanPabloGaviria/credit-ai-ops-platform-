SHELL := /bin/bash
PYTHON ?= python3

.PHONY: lint type-check pyright-check docs-audience-lint unit-tests coverage-gate pre-commit-gate security-scan secret-scan contract-lint adr-gate cybersec-posture bank-cybersec-gate container-policy sbom test all relay recruiter-demo release-ready lock-dependencies

lint:
	$(PYTHON) -m ruff check .

type-check:
	$(PYTHON) -m mypy .

pyright-check:
	$(PYTHON) -m pyright

unit-tests:
	@mkdir -p build
	$(PYTHON) -m pytest -m "unit or contract" --cov=packages --cov=services --cov-report=term --cov-report=json:build/coverage.unit.json -vv

docs-audience-lint:
	$(PYTHON) scripts/ci/docs_audience_lint.py

coverage-gate:
	python3 scripts/ci/coverage_gate.py --input build/coverage.unit.json

pre-commit-gate: lint type-check pyright-check docs-audience-lint unit-tests coverage-gate contract-lint adr-gate container-policy

security-scan:
	$(PYTHON) -m bandit -r packages services scripts -x tests
	# GHSA-5239-wwwm-4pmq is a dev-only Pygments advisory with no fixed release available yet.
	$(PYTHON) -m pip_audit -r requirements/lock/dev.lock --progress-spinner off --timeout 30 --ignore-vuln GHSA-5239-wwwm-4pmq

secret-scan:
	@if command -v gitleaks >/dev/null 2>&1; then \
		gitleaks detect --source . --redact --no-git; \
	else \
		docker run --rm -v "$(PWD):/repo" zricethezav/gitleaks:8.24.2 \
			detect --source /repo --redact --no-git; \
	fi

contract-lint:
	$(PYTHON) scripts/ci/contract_lint.py

adr-gate:
	$(PYTHON) scripts/ci/adr_gate.py

cybersec-posture:
	$(PYTHON) scripts/ci/cybersec_gate.py

container-policy:
	$(PYTHON) scripts/ci/container_hardening_gate.py

bank-cybersec-gate: security-scan secret-scan cybersec-posture container-policy

sbom:
	cyclonedx-py environment --output-format json --output-file build/sbom.json

test:
	pytest

all: lint type-check unit-tests coverage-gate security-scan secret-scan contract-lint container-policy

lock-dependencies:
	$(PYTHON) -m piptools compile pyproject.toml --generate-hashes --allow-unsafe --strip-extras --output-file requirements/lock/base.lock
	$(PYTHON) -m piptools compile pyproject.toml --extra dev --generate-hashes --allow-unsafe --strip-extras --output-file requirements/lock/dev.lock

migrate:
	@test -n "$$POSTGRES_DSN" || (echo "POSTGRES_DSN is required"; exit 1)
	python scripts/dev/apply_migrations.py --postgres-dsn "$$POSTGRES_DSN"

relay:
	@test -n "$$SERVICE" || (echo "SERVICE is required (application|feature|scoring|decision|mlops)"; exit 1)
	python scripts/dev/run_outbox_relay.py --service "$$SERVICE"

recruiter-demo:
	./scripts/dev/recruiter_demo.sh

release-ready: pre-commit-gate recruiter-demo
