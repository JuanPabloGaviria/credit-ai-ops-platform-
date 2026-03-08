# Brief Ejecutivo

**Idioma:** Español (principal) · [English version](brief.en.md)

## Objetivo
Validar capacidad de entrega end-to-end de soluciones de IA en entorno bancario regulado: desde definición de caso de uso hasta operación productiva con controles de riesgo, observabilidad y gobierno ML.

## Tesis De Valor
La plataforma prueba tres capacidades críticas para banca:

1. Construcción de producto de IA con impacto operacional real.
2. Controles técnicos que reducen riesgo operacional y riesgo de modelo.
3. Comunicación verificable entre objetivos de negocio y evidencia técnica.

## Resultado De Negocio Esperado
- reducción de fricción en evaluación crediticia
- mejora de tiempo de respuesta en decisiones
- mayor trazabilidad para auditoría y cumplimiento
- menor costo de incidentes por recuperación explícita (DLQ/replay)

## Evidencia Técnica De Alto Nivel
- **Cadena asíncrona completa:** `application -> feature -> scoring -> decision -> assistant -> audit`
- **Persistencia SQL y contratos tipados:** repositorios y schemas versionados
- **MLOps reproducible:** train/evaluate/register/promote + model card
- **Resiliencia:** timeout, retry, circuit-breaker, bulkhead, idempotencia
- **Seguridad y supply chain:** SAST, dependency audit, secret scan, SBOM, firma de imágenes

## Métricas y SLO
- p95 gateway `<= 300ms`
- p95 asíncrono `<= 2s`
- tasa 5xx `< 1%` (ventana 15 minutos)

Estas metas son objetivos operativos. No se publican snapshots estáticos de latencia en
markdown porque dejan de ser confiables cuando cambia el entorno, la red o la topología
ejecutada. La evidencia vigente debe regenerarse antes de compartir métricas externas.

## Checklist De Revisión
1. Ejecutar `make recruiter-demo` y revisar `build/recruiter-demo-report.md`.
2. Verificar evidencia MLOps en `build/recruiter-ml-evidence.json`.
3. Revisar `build/reviewer-scorecard.md` para el mapa de controles y evidencia.
4. Ejecutar `make bank-cybersec-gate` para postura de seguridad.
5. Ejecutar `pytest -m integration -vv` para confiabilidad operativa real.
6. Confirmar trazabilidad en `GET /v1/audit/traces/{trace_id}`.

## Decisiones Arquitectónicas Clave
- microservicios por dominio en monorepo con compuertas únicas de calidad
- contratos explícitos REST/eventos antes de evolución funcional
- integración asíncrona con outbox/inbox para consistencia operacional
- fallback determinístico para assistant interno en ausencia de LLM
