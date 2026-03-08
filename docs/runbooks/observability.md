# Observability Runbook

## Production Path
Production Azure Container Apps deployments export traces through the managed OpenTelemetry agent:

- workloads enable tracing with `OTEL_ENABLED=true`
- the managed environment injects `OTEL_EXPORTER_OTLP_ENDPOINT` and `OTEL_EXPORTER_OTLP_PROTOCOL`
- the shared kernel supports both `http/protobuf` and `grpc` OTLP exporters
- traces land in the workspace-backed Application Insights resource
- application and platform logs remain in Log Analytics

## Required Controls
- keep `OTEL_ENABLED=true` for production deployments
- do not hardcode `OTEL_EXPORTER_OTLP_ENDPOINT` or `OTEL_EXPORTER_OTLP_PROTOCOL` in Terraform
- keep Application Insights and managed OpenTelemetry configuration attached to the Container Apps environment
- keep `OTEL_SAMPLER_RATIO` aligned with the approved sampling policy

## Validation
```bash
./.venv/bin/python -m pytest tests/unit/test_telemetry.py -vv
terraform -chdir=infra/terraform/container-apps validate
./.venv/bin/python scripts/ci/cybersec_gate.py
```
