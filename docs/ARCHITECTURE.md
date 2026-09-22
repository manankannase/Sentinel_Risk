# Architecture and trust boundaries

## Flow

1. Browser sends bounded JSON with a bearer workspace key.
2. FastAPI authenticates role and tenant, applies rate limits, validates a strict schema, and locks the customer ledger row.
3. Serving features use only prior events from the same tenant and customer.
4. A SHA-256-pinned native XGBoost artifact returns risk. Thresholds produce APPROVE, REVIEW, or simulated DECLINE recommendations.
5. Decision, feature snapshot, request digest, account cursor, and outbox event commit in one database transaction.
6. Worker publishes pending events with an idempotent producer. Consumer accepts only payloads matching the authoritative outbox and deduplicates event IDs.
7. Feedback is tenant-scoped and immutable. Dashboard aggregates only the authenticated tenant.

## Components

| Layer | Technology | Responsibility |
|---|---|---|
| UI | HTML, CSS, vanilla JavaScript | Accessible dashboard and manual scoring |
| API | FastAPI, Pydantic, Uvicorn | Validation, authentication, scoring, analytics |
| ML | XGBoost, scikit-learn, pandas, NumPy | Chronological experiment, baseline comparison, inference |
| Data | PostgreSQL/SQLite, SQLAlchemy | Ledger, feedback, customer ordering, transactional outbox |
| Cache | Redis | Distributed rate-limit counters |
| Streaming | Redpanda/Kafka protocol, confluent-kafka | At-least-once event delivery and idempotent consumption |
| Operations | Docker Compose, Prometheus, Grafana, GitHub Actions | Packaging, monitoring, automated verification |

## Main trust boundaries

- Internet-to-API: terminate TLS before public traffic; reject oversized, compressed, malformed, or unexpected input.
- API-to-storage: least-privilege credentials, private networks, parameterized SQL, bounded queries.
- Artifact-to-runtime: native JSON only, size limit, feature schema check, SHA-256 pin.
- API-to-broker: transactional outbox; SASL_SSL is mandatory in production.
- Tenant boundary: every business query includes the authenticated tenant. Tests verify cross-tenant reads and feedback fail.

This repository is a portfolio-grade reference, not a payment processor. Production use still requires infrastructure-specific IAM, TLS, secret management, backups, vulnerability management, privacy review, load testing, and independent security assessment.
