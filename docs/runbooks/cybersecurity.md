# Cybersecurity Runbook

## Objective
Enforce fail-fast security controls before merge or recruiter sharing.

## Bank-Grade Gate Command
```bash
make bank-cybersec-gate
```

This command runs:
- `make security-scan` (Bandit + pip-audit lockfile checks)
- `make secret-scan` (gitleaks)
- `make cybersec-posture` (policy checks for runtime pinning, dependency pinning, container image pinning, terraform pinning, managed identity and Key Vault posture, branch protection, CI enforcement, and supply-chain signing workflow coverage)

## Individual Commands
```bash
make security-scan
make secret-scan
make cybersec-posture
```

## Supply-Chain Signing Workflow
- Workflow: `.github/workflows/build-sign.yml`
- Builds and signs all service images with Cosign keyless OIDC.
- Produces per-service digest artifacts, a consolidated `image-digests` manifest, and `container-apps.auto.tfvars.json` for Terraform deployment pinning.
- Remote branch-protection validation uses `BRANCH_PROTECTION_TOKEN` when available; non-protected branches may fall back to `github.token` as best-effort read access.
- Protected branch validation is fail-closed. On `main`, CI must provide `BRANCH_PROTECTION_TOKEN` with permission to read branch protection or the cybersecurity gate fails.

## Azure Secret Boundary
- Production Terraform inputs must reference `key_vault_id` plus `key_vault_secret_ids`; raw DSN, broker, auth-secret, model-signing-key, and registry-password variables are for non-production bootstrap only.
- Container Apps use a shared user-assigned identity for Key Vault secret reads and optional ACR pulls.
- The production deployment path must provide both `MODEL_SIGNING_PRIVATE_KEY_PEM` for `mlops` and `MODEL_SIGNING_PUBLIC_KEY_PEM` for `scoring`.

## Expected Result
- Exit code `0`
- Output contains:
  - `[cybersec-gate] All cybersecurity posture checks passed`
  - `No known vulnerabilities found`
  - `no leaks found`

## Failure Handling
1. Fix the first failing control.
2. Re-run the failing command only.
3. Re-run `make bank-cybersec-gate`.
4. Do not continue to next slice until all checks are green.
