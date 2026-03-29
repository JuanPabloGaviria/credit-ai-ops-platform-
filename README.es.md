# credit-ai-ops-platform

Plataforma de decision crediticia con IA para entornos regulados, construida alrededor de las partes dificiles de produccion: gobierno del modelo, trazabilidad de la decision, recuperacion ante fallas asincronas y controles de seguridad que existen en codigo en lugar de diapositivas.

**Idioma:** [English README](README.md) | Espanol (companero)

## Resumen

`credit-ai-ops-platform` trata la decision de credito como un problema de sistemas gobernados, no como una demo de serving de modelos.

La plataforma recibe una solicitud, materializa features de riesgo, resuelve el modelo promovido, produce un score, aplica una politica crediticia y registra toda la ruta como evidencia operativa reconstruible. El lifecycle MLOps no esta separado del producto: entrenamiento, evaluacion, registro, promocion, firma de artefactos, auditoria y pruebas reproducibles forman parte del mismo sistema.

## Tesis Del Sistema

La tesis central del repositorio es concreta:

> un sistema de decision crediticia puede moverse rapido sin perder verificabilidad operativa si el lifecycle del modelo, la politica de negocio y la recuperacion ante fallas se tratan como fronteras de primera clase.

## Lo Mas Importante Que Es Real

- existe un camino sincrono real por gateway para evaluar credito
- existe una cadena asincrona real para procesamiento durable, auditoria y replay
- el scoring resuelve un modelo promovido con identidad verificable
- la auditoria conserva trazabilidad por `trace_id`, `correlation_id` y `causation_id`
- la seguridad importante se valida con gates y artefactos, no con claims vacios

## Pruebas Mas Fuertes

| Afirmacion | Evidencia principal |
| --- | --- |
| La cadena asincrona existe de verdad | `tests/integration/test_async_credit_chain.py` |
| El gateway llama servicios reales | `tests/e2e/test_gateway_http_stack.py` |
| El lifecycle MLOps es reproducible | `build/recruiter-ml-evidence.json` y `docs/runbooks/mlops-lifecycle.md` |
| La postura de seguridad no es decorativa | `make bank-cybersec-gate` |
| La evidencia para review es reproducible | `make recruiter-demo` |

## Lectura Recomendada

- [README principal en English](README.md)
- [Brief ejecutivo](docs/executive/brief.md)
- [Matriz de alineacion al rol](docs/executive/role-alignment.md)
- [Runbook del flujo asincrono](docs/runbooks/async-flow.md)
- [Runbook del lifecycle MLOps](docs/runbooks/mlops-lifecycle.md)

## Comandos De Revision

```bash
make recruiter-demo
make release-ready
```

## Alcance Honesto

- no se afirma un despliegue bancario en produccion
- no se afirma aprobacion regulatoria
- no se afirma DR administrado en nube
- si se afirma que el camino principal de credito, el lifecycle del modelo y la superficie de evidencia son defendibles bajo revision tecnica seria
