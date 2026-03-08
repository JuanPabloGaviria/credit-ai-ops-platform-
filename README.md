# credit-ai-ops-platform

Plataforma de operaciones de IA para riesgo crediticio, diseñada para banca regulada con foco en confiabilidad, trazabilidad y despliegue productivo.

**Idioma:** Español (principal) · [English version](README.en.md)

## Resumen Ejecutivo
`credit-ai-ops-platform` demuestra un ciclo completo de IA aplicada a crédito:

- ingestión de solicitud
- materialización de features
- scoring determinístico
- decisión explicable
- auditoría trazable
- gobierno MLOps reproducible

El diseño prioriza resultados de negocio y control de riesgo operacional:

- decisiones observables extremo a extremo
- fallas asíncronas recuperables con DLQ + replay
- seguridad verificable por compuertas automáticas
- evidencia reproducible para revisión técnica y ejecutiva

## Demostración En Un Comando
```bash
make recruiter-demo
```

Salida esperada:

- reporte ejecutivo técnico en `build/recruiter-demo-report.md`
- evidencia MLOps en `build/recruiter-ml-evidence.json`
- scorecard revisor en `build/reviewer-scorecard.md`
- validación de ciberseguridad, integración y baseline asíncrono de pipeline

Comando de verificación final antes de compartir:

```bash
make release-ready
```

## Arquitectura Del Sistema
```mermaid
flowchart LR
  Channel[Canales de solicitud] --> Gateway[api-gateway]
  Gateway --> Application[application-service]
  Application --> Feature[feature-service]
  Feature --> Scoring[scoring-service]
  Scoring --> Decision[decision-service]
  Decision --> Assistant[collab-assistant-service]

  Application --> Bus[RabbitMQ]
  Feature --> Bus
  Scoring --> Bus
  Decision --> Bus
  Assistant --> Bus
  Bus --> Audit[observability-audit-service]

  Application --> Postgres[(PostgreSQL)]
  Feature --> Postgres
  Scoring --> Postgres
  Decision --> Postgres
  Assistant --> Postgres
  Audit --> Postgres

  MLOps[mlops-service] --> Postgres
  MLOps --> Artifacts[Model artifacts y model cards]
```

## Flujo Asíncrono De Crédito
```mermaid
sequenceDiagram
  participant App as application
  participant Feat as feature
  participant Score as scoring
  participant Dec as decision
  participant Assist as collab-assistant
  participant Audit as observability-audit

  App->>App: Persistencia solicitud + outbox
  App->>Feat: credit.application.submitted.v1
  Feat->>Feat: Persistencia features + outbox
  Feat->>Score: credit.feature.materialized.v1
  Score->>Score: Persistencia score + outbox
  Score->>Dec: credit.scoring.generated.v1
  Dec->>Dec: Persistencia decision + outbox
  Dec->>Assist: credit.decision.made.v1
  App-->>Audit: Eventos auditables
  Feat-->>Audit: Eventos auditables
  Score-->>Audit: Eventos auditables
  Dec-->>Audit: Eventos auditables
  Assist-->>Audit: Eventos auditables
```

## Qué Evalúa Un Perfil Ejecutivo
| Pregunta de negocio | Evidencia verificable |
|---|---|
| ¿Existe impacto operativo real? | Cadena crédito completa en `tests/integration/test_async_credit_chain.py` |
| ¿El riesgo operacional está controlado? | Errores tipados, trace IDs y runbooks en `docs/runbooks/*` |
| ¿La solución es auditable? | Eventos redactados y consultas de trazas en `services/observability-audit` |
| ¿La plataforma resiste fallas? | Timeouts, retries, circuit-breaker, bulkhead, DLQ/replay |
| ¿El ciclo ML es gobernable? | `train/evaluate/register/promote` + model card + reproducibilidad |

## Mapa De Servicios
- Tier A completo: `api-gateway`, `application`, `feature`, `scoring`, `decision`
- Tier B scoped-real: `collab-assistant`, `mlops`, `observability-audit`
- Identidad: integración OIDC con Keycloak (`services/identity`)

## Endpoints v1 Implementados
- `POST /v1/gateway/credit-evaluate`
- `POST /v1/applications/intake`
- `POST /v1/features/materialize`
- `POST /v1/scores/predict`
- `POST /v1/decisions/evaluate`
- `POST /v1/assistant/summarize`
- `GET /v1/assistant/summaries/{application_id}`
- `POST /v1/mlops/train`
- `POST /v1/mlops/evaluate`
- `POST /v1/mlops/register`
- `POST /v1/mlops/promote`
- `GET /v1/mlops/runs/{run_id}`
- `POST /v1/audit/events`
- `GET /v1/audit/events`
- `GET /v1/audit/events/{event_id}`
- `GET /v1/audit/traces/{trace_id}`

Contratos canónicos:

- REST: `schemas/openapi/*.yaml`
- Eventos: `schemas/asyncapi/credit-events-v1.yaml`
- Schemas base: `schemas/jsonschema/*.json`

Autenticación:

- todos los endpoints `/v1/*` requieren `Authorization: Bearer <token>`
- `health`, `ready` y `metrics` permanecen sin autenticación para operación local y probes

## Controles Técnicos Críticos
### Confiabilidad
- timeouts obligatorios en integraciones externas
- retries acotados con backoff + jitter
- circuit-breaker y bulkhead en bordes de integración
- idempotencia para escrituras externas
- outbox/inbox + DLQ/replay para resiliencia asíncrona

### Observabilidad
- logs estructurados JSON
- trace/correlation IDs propagados
- endpoints de health/readiness/metrics
- taxonomía de errores tipados fail-loud

### Seguridad y Supply Chain
- dependencias pinneadas en `requirements/lock/*.lock`
- SBOM en CI
- firma de imágenes con Cosign + digest pinning
- posture gate bancario: `make bank-cybersec-gate`

## SLO Baselines v1
- p95 API gateway `<= 300ms`
- p95 procesamiento asíncrono `<= 2s`
- tasa 5xx `< 1%` en ventana de 15 minutos

Las metas anteriores son objetivos operativos. Este repositorio no congela cifras de latencia
en markdown porque dejan de ser confiables fuera del entorno exacto donde se ejecutaron.
La evidencia vigente debe generarse con `make recruiter-demo` y corridas de integración/red
actuales antes de citar números a terceros.

## Setup Local (Python 3.11 Fijo)
```bash
brew install python@3.11
./scripts/dev/bootstrap.sh
source .venv/bin/activate
python --version
```

Versión permitida para v1: `>=3.11,<3.12`.

## Infraestructura Local
```bash
docker compose up -d postgres rabbitmq
source .venv/bin/activate
export POSTGRES_DSN=postgresql://credit:credit@localhost:5432/credit_ai_ops
make migrate
```

## Compuertas De Calidad
- `make lint`
- `make type-check`
- `make pyright-check`
- `make docs-audience-lint`
- `make unit-tests`
- `make coverage-gate`
- `make security-scan`
- `make secret-scan`
- `make contract-lint`
- `make adr-gate`
- `make cybersec-posture`

Compuerta rápida antes de commit:

```bash
make pre-commit-gate
```

## Cobertura (Policy)
- núcleo dominio/aplicación: `>=90%`
- adaptadores/bordes integración: `>=70%`
- piso total repositorio: `>=80%`

## Guías Clave
- Brief ejecutivo: `docs/executive/brief.md`
- Matriz de alineación de rol: `docs/executive/role-alignment.md`
- Runbook demo: `docs/runbooks/recruiter-demo.md`
- Flujo asíncrono: `docs/runbooks/async-flow.md`
- MLOps lifecycle: `docs/runbooks/mlops-lifecycle.md`
- Ciberseguridad: `docs/runbooks/cybersecurity.md`
