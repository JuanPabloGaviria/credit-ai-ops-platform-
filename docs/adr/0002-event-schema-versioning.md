# ADR 0002: Event Schema and Versioning Policy

## Status
Accepted

## Decision
Use OpenAPI + AsyncAPI + JSON Schema with event naming `<domain>.<entity>.<action>.v1`.

## Rationale
Machine-validated contracts reduce integration drift and enforce explicit compatibility boundaries.
