# Matriz De Alineación Del Rol (Amrop - Especialista en IA)

Volver a los documentos principales:

- [README principal](../../README.md)
- [README en Espanol](../../README.es.md)
- [Version en English](role-alignment.en.md)

**Idioma:** Español (principal) · [English version](role-alignment.en.md)

Esta matriz mapea requisitos del rol contra evidencia verificable en código, pruebas y runbooks.

| Requisito del rol | Evidencia verificable |
|---|---|
| Diseño y despliegue end-to-end de IA/ML | Flujo asíncrono completo documentado en `docs/runbooks/async-flow.md` y validado en `tests/integration/test_async_credit_chain.py` |
| Dominio avanzado de Python y SQL | Servicios tipados en `services/*/src/*`; migraciones SQL versionadas en `services/*/migrations/*.sql`; validación estricta con `mypy`, `pyright`, `pydantic` |
| Pipelines robustos y mantenibles | Persistencia por dominio con outbox/inbox; replay controlado de DLQ en `scripts/dev/replay_dlq.py`; compuertas de calidad en `Makefile` |
| MLOps y ciclo de vida de modelos | Endpoints `train/evaluate/register/promote` en `services/mlops`; runbook en `docs/runbooks/mlops-lifecycle.md`; pruebas en `tests/unit/test_mlops_lifecycle.py` |
| Despliegue de modelos como APIs (FastAPI) | Servicios FastAPI en `services/*/src/*/main.py`; contratos versionados en `schemas/openapi` |
| Cloud readiness (Azure) | Baseline IaC para Container Apps en `infra/terraform/container-apps`; decisiones documentadas en ADR y ruta AKS next-stage |
| Comunicación con liderazgo no técnico | Narrativa ejecutiva en `docs/executive/brief.md`; demo reproducible de negocio+técnica en `make recruiter-demo` |
| Innovación con control de riesgo | Evolución de compuertas `pre-commit-gate`, `cybersec-posture`, `bank-cybersec-gate`; ADRs de arquitectura en `docs/adr/*` |

## Comandos De Alta Señal
```bash
make recruiter-demo
make pre-commit-gate
make bank-cybersec-gate
```

## Resultado Esperado De Revisión
- confirmación de impacto funcional end-to-end
- confirmación de confiabilidad operativa bajo falla
- confirmación de seguridad y trazabilidad para entorno regulado
