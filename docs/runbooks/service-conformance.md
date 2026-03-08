# Service Conformance Checklist

A service step cannot be marked complete unless all checks pass:

1. Structured JSON logging
2. Trace ID propagation
3. Typed error envelope responses
4. `/health`, `/ready`, `/metrics` endpoints
5. Contract validation and tests green
6. Security and secret scans green
7. Risk-weighted coverage gates green (`make coverage-gate`)
8. Cybersecurity posture gate green (`make cybersec-posture`)
