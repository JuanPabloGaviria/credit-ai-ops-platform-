# Supply Chain Baseline

## Required Controls
- Pinned dependency lockfiles in `requirements/lock/`
- SBOM generation in CI (`build/sbom.json` artifact)
- Container image signing with Cosign keyless OIDC flow
- Digest pinning for deployment references (avoid mutable tags)
- Signed-image digest manifest rendered into `container-apps.auto.tfvars.json` for Terraform deployments

## Branch Protection
`main` must enforce required checks and review policy documented in `.github/branch-protection-policy.json`.
