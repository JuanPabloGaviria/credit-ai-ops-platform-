# credit-ai-ops-platform

Plataforma de operaciones de IA para riesgo crediticio, diseñada como si fuera a ser evaluada por un banco grande, un líder de tecnología y un reclutador no técnico al mismo tiempo.

**Idioma:** Español (principal) · [English version](/Users/jpgaviria/Development/jpgaviria/credit-ai-ops-platform/README.en.md)

## Qué Es Este Repositorio
Este repositorio muestra una plataforma completa para evaluar solicitudes de crédito con IA y controles de banca regulada.

No es solo un modelo.

No es solo una API.

No es solo una demo bonita.

El objetivo es demostrar que un sistema de IA para crédito puede:

- recibir una solicitud
- transformar datos en variables útiles
- calcular un score de riesgo
- tomar una decisión explicable
- registrar auditoría y trazabilidad
- operar con controles reales de seguridad, resiliencia y gobierno MLOps

## Por Qué Importa
En banca, una solución técnica no se evalúa solo por “si funciona”.

También se evalúa por preguntas como estas:

- ¿Se puede confiar en la decisión?
- ¿Se puede reconstruir lo que pasó meses después?
- ¿La plataforma resiste fallas sin corromper datos?
- ¿La seguridad está en código o solo en presentaciones?
- ¿El modelo que sirve producción es exactamente el que fue aprobado?
- ¿Un auditor, un reclutador y un arquitecto entienden lo mismo al leer el repositorio?

Este proyecto fue endurecido precisamente alrededor de esas preguntas.

## Lectura Rápida Para Cada Perfil
### Reclutamiento / HR
La lectura más simple es esta:

- existe un flujo completo de crédito, no solo un notebook
- existe una separación clara entre servicios, datos, auditoría y gobierno del modelo
- existe validación automática, no promesas
- existe evidencia reproducible, no screenshots manuales
- existe documentación pensada para negocio y para ingeniería

### Liderazgo No Técnico
La lectura de negocio es esta:

- una solicitud entra por un gateway
- el sistema calcula variables de riesgo
- el motor genera un score
- la política decide aprobar, revisar o rechazar
- cada paso queda rastreado
- el modelo promovido a producción tiene trazabilidad, firma y evidencia

### Revisión Técnica
La lectura técnica es esta:

- arquitectura multi-servicio con contratos explícitos
- persistencia append-only donde importa el historial
- idempotencia, outbox, DLQ, replay y circuit breaker
- autenticación JWT/JWKS y llamadas internas autenticadas
- despliegue Azure endurecido con identidad administrada y Key Vault
- artefactos de modelo firmados y verificados antes de servir
- pruebas reales de integración asíncrona y flujo HTTP end-to-end

## Qué Hace El Sistema, En Lenguaje Simple
Una persona solicita crédito.

La plataforma recibe esa solicitud y calcula datos útiles para medir riesgo: nivel de endeudamiento, historial, defaults previos y relación entre ingreso y monto solicitado.

Con esos datos, la plataforma genera un score.

Después, una política de negocio convierte ese score en una decisión: aprobar, revisar manualmente o rechazar.

Al mismo tiempo, cada evento relevante se guarda para auditoría.

En paralelo, el ciclo MLOps permite entrenar, evaluar, registrar y promover modelos nuevos de forma reproducible y controlada.

## Flujo De Negocio
```mermaid
flowchart LR
  A["Solicitud de crédito"] --> B["Recepción en api-gateway"]
  B --> C["Materialización de features"]
  C --> D["Scoring de riesgo"]
  D --> E["Decisión explicable"]
  E --> F["Resumen colaborativo"]
  B --> G["Auditoría y trazabilidad"]
  C --> G
  D --> G
  E --> G
```

## Flujo Técnico Del Sistema
```mermaid
flowchart LR
  Channel["Canales de entrada"] --> Gateway["api-gateway"]
  Gateway --> Feature["feature-service"]
  Gateway --> Scoring["scoring-service"]
  Gateway --> Decision["decision-service"]

  Application["application-service"] --> Bus["RabbitMQ"]
  Feature --> Bus
  Scoring --> Bus
  Decision --> Bus
  Assistant["collab-assistant"] --> Bus
  Bus --> Audit["observability-audit"]

  Application --> Postgres[(PostgreSQL)]
  Feature --> Postgres
  Scoring --> Postgres
  Decision --> Postgres
  Assistant --> Postgres
  Audit --> Postgres

  MLOps["mlops-service"] --> Postgres
  MLOps --> Artifacts["Artifacts + model cards + firmas"]
  Scoring --> Artifacts
```

## Cadena De Decisión Paso A Paso
### 1. Entrada
El endpoint principal síncrono es `POST /v1/gateway/credit-evaluate`.

Este endpoint no “finge” el resto del sistema dentro del mismo proceso. Llama a los servicios reales de features, scoring y decisión.

### 2. Features
`feature-service` transforma la solicitud en un vector de features y registra historial.

### 3. Scoring
`scoring-service` resuelve el modelo promovido en el registry, verifica digest y firma, y calcula el score desde el artefacto aprobado.

### 4. Decisión
`decision-service` aplica la política de crédito y produce una salida explicable.

### 5. Auditoría
`observability-audit` guarda eventos redactados, trazables por `trace_id`, `correlation_id` y `causation_id`.

### 6. Flujo Asíncrono
Existe además una cadena asíncrona completa para intake y procesamiento por eventos:

- `application -> feature -> scoring -> decision -> collab-assistant`

Ese flujo usa outbox relay, colas, replay y persistencia histórica.

## Qué Hace Que Este Repositorio Sea Fuerte
### 1. Honestidad Arquitectónica
La arquitectura documentada coincide con la arquitectura que realmente se ejecuta.

Eso parece obvio, pero no lo es. Muchas demos de IA “simulan” microservicios mientras resuelven todo en memoria. Aquí no.

### 2. Persistencia Con Historial
Las partes importantes no se reescriben silenciosamente. El historial de features, score, decisiones, promociones y auditoría se conserva para reconstrucción y revisión.

### 3. Resiliencia Operativa
Los bordes de integración tienen:

- timeouts
- retries acotados
- circuit breaker con recuperación
- bulkhead
- outbox claim/lease
- DLQ y replay
- idempotencia para escrituras

### 4. Seguridad En Código
La seguridad importante no vive solo en documentos:

- JWT/JWKS para autenticación
- secretos en Azure vía identidad administrada y Key Vault
- imágenes con digest pinning
- escaneo de secretos
- SBOM
- validaciones de postura
- verificación de contratos y políticas desde CI

### 5. Gobierno Real Del Modelo
El modelo en serving tiene:

- versión explícita
- stage explícito
- artefacto inmutable
- digest verificable
- firma verificable
- metadata de reproducibilidad
- model card
- aprobación de promoción con referencias de control

## Qué Se Endureció Durante El Desarrollo
Este repositorio no nació en estado final. El valor está también en el proceso de endurecimiento.

Las principales iteraciones fueron:

- eliminación de rutas falsas o “arquitectura teatro”
- corrección de autenticación incompleta entre servicios
- rediseño de idempotencia y outbox para evitar duplicados y estados colgados
- corrección de circuit breaker para recuperación real
- paso a persistencia append-only en dominios críticos
- paso de scoring local fijo a serving desde artefacto promovido
- introducción de firma y verificación de paquetes de modelo
- endurecimiento del despliegue Azure con identidad administrada y secretos referenciados
- propagación real de trazas y contexto observacional
- alineación estricta entre contratos, código y documentación
- limpieza de evidencia para evitar reportes “PASS” falsos o reutilizables

## Evidencia Que Ya Existe
### Validación Asíncrona Real
Archivo clave:

- [tests/integration/test_async_credit_chain.py](/Users/jpgaviria/Development/jpgaviria/credit-ai-ops-platform/tests/integration/test_async_credit_chain.py)

Qué demuestra:

- intake real
- relay-only publication
- consumo por colas
- persistencia histórica
- auditoría

### Validación HTTP End-To-End Real
Archivo clave:

- [tests/e2e/test_gateway_http_stack.py](/Users/jpgaviria/Development/jpgaviria/credit-ai-ops-platform/tests/e2e/test_gateway_http_stack.py)

Qué demuestra:

- procesos reales levantados por socket local
- gateway llamando servicios reales
- modelo promovido en serving
- replay idempotente
- escritura única en historial

### Evidencia MLOps
Artefactos clave:

- `build/recruiter-ml-evidence.json`
- `build/recruiter-mlops/artifacts/*.json`
- `build/recruiter-mlops/model_cards/*.json`

Qué demuestran:

- entrenamiento determinístico
- metadata de reproducibilidad
- artefacto firmado
- card del modelo

### Scorecard De Revisión
Artefactos clave:

- `build/reviewer-scorecard.md`
- `build/reviewer-scorecard.json`

Qué demuestran:

- validaciones ligadas al commit actual
- receipts con timestamp
- vínculo opcional a corrida CI
- mapa entre control y evidencia

## Demostración En Un Comando
```bash
make recruiter-demo
```

Salida esperada:

- `build/recruiter-demo-report.md`
- `build/recruiter-ml-evidence.json`
- `build/reviewer-scorecard.md`
- `build/reviewer-scorecard.json`

Ese comando hace lo siguiente:

1. levanta Postgres y RabbitMQ
2. espera readiness real
3. aplica migraciones
4. genera evidencia MLOps
5. ejecuta la compuerta de ciberseguridad
6. ejecuta la prueba de integración asíncrona real
7. ejecuta la prueba HTTP end-to-end real
8. genera el reporte final y el scorecard

Compuerta final antes de compartir:

```bash
make release-ready
```

## Contratos y APIs
### Endpoints v1 Implementados
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

### Contratos Canónicos
- REST: `schemas/openapi/*.yaml`
- Eventos: `schemas/asyncapi/credit-events-v1.yaml`
- Schemas base: `schemas/jsonschema/*.json`

### Autenticación
- todos los endpoints `/v1/*` requieren `Authorization: Bearer <token>`
- `health`, `ready` y `metrics` quedan abiertos para operación local y probes

## Seguridad, Plataforma y Supply Chain
### En Pre-Merge
Las ramas protegidas exigen:

- `quality`
- `integration-e2e`
- `supply-chain-verify`
- `container-policy`
- `secret-scan`
- `sbom`

### En Post-Merge Sobre `main`
La plataforma ejecuta:

- build de imágenes
- push por digest
- escaneo de vulnerabilidades
- firma con Cosign
- verificación de firma
- attestations de provenance
- manifest consolidado para Terraform

### En Infraestructura Azure
La ruta de despliegue contempla:

- Azure Container Apps
- identidades administradas
- secretos vía Key Vault
- referencias por digest
- telemetría gestionada
- restricciones de producción en Terraform

## Observabilidad y Auditoría
La observabilidad no se limita a logs.

Incluye:

- logs estructurados
- métricas por ruta normalizada
- contexto de trazas propagado
- trazabilidad por `trace_id`, `correlation_id` y `causation_id`
- auditoría con redacción de PII

## MLOps En Lenguaje Simple
El proceso MLOps se puede entender así:

1. se entrena un modelo
2. se evalúa
3. se decide si pasa la política
4. se registra con metadata
5. se firma el artefacto
6. se promueve a un stage
7. el servicio de scoring solo sirve lo que fue promovido y verificado

## SLOs y Honestidad Operativa
Objetivos v1:

- p95 gateway `<= 300ms`
- p95 procesamiento asíncrono `<= 2s`
- tasa 5xx `< 1%`

Estos números son objetivos, no slogans permanentes.

La evidencia vigente debe regenerarse antes de citar resultados a terceros. Por eso el repositorio privilegia corridas reproducibles sobre screenshots o cifras viejas pegadas en markdown.

## Setup Local
```bash
brew install python@3.11
./scripts/dev/bootstrap.sh
source .venv/bin/activate
python --version
```

Versión permitida para v1: `>=3.11,<3.12`.

Infra local:

```bash
docker compose up -d postgres rabbitmq
export POSTGRES_DSN=postgresql://credit:credit@localhost:5432/credit_ai_ops
make migrate
```

## Compuertas De Calidad
Comandos locales principales:

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

## Cómo Leer El Repositorio En Orden
Para una lectura ejecutiva:

1. `README.md`
2. `docs/executive/brief.md`
3. `docs/runbooks/recruiter-demo.md`
4. `build/recruiter-demo-report.md`
5. `build/reviewer-scorecard.md`

Para una lectura técnica:

1. `schemas/openapi/*.yaml`
2. `tests/integration/test_async_credit_chain.py`
3. `tests/e2e/test_gateway_http_stack.py`
4. `services/mlops/src/mlops_service/lifecycle.py`
5. `services/scoring/src/scoring_service/runtime.py`
6. `infra/terraform/container-apps/main.tf`
7. `.github/workflows/ci.yml`
8. `.github/workflows/build-sign.yml`

## Guías Clave
- Brief ejecutivo: `docs/executive/brief.md`
- Matriz de alineación al rol: `docs/executive/role-alignment.md`
- Runbook de demo: `docs/runbooks/recruiter-demo.md`
- Flujo asíncrono: `docs/runbooks/async-flow.md`
- Lifecycle MLOps: `docs/runbooks/mlops-lifecycle.md`
- Ciberseguridad: `docs/runbooks/cybersecurity.md`
- Observabilidad: `docs/runbooks/observability.md`

## Cierre
La idea central de este repositorio es simple:

IA útil para banca no consiste solo en un modelo bueno.

Consiste en un sistema completo, explicable, seguro, auditable, operable y defendible frente a una revisión dura.
