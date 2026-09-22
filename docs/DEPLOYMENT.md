# Deployment guide

## Local deployment

Run python scripts/bootstrap.py once on a new checkout. It generates unique credentials in .secrets and writes .env. Both are ignored by Git and excluded from Docker builds.

Run docker compose up --build. Only the API maps to 127.0.0.1:8000. PostgreSQL, Redis, and Redpanda have no host ports. Application containers run as UID 10001 with a read-only filesystem, dropped Linux capabilities, and /tmp as writable temporary storage.

## Production baseline

Use managed PostgreSQL, Redis, and Kafka-compatible infrastructure. Set SENTINEL_ENV=production; a PostgreSQL connection URL; a Redis TLS URL; SENTINEL_BROKER_PROTOCOL=SASL_SSL; a CA file; an explicit host allowlist; and a pinned model digest from the approved manifest.

Terminate TLS at a managed load balancer or reverse proxy. Put credentials in a cloud secret manager and inject individual read-only secret files at runtime. Rotate credentials. Restrict /metrics to the monitoring network.

## Release checks

Run uv run pytest -q, uv run ruff check ., uv run pip-audit, docker compose config, and a container smoke test before every release. Review vulnerability scans, database migration, backup/restore, data governance, and threat model before public exposure.

The request ledger and outbox are written in one database transaction. A broker outage leaves accepted decisions pending in the outbox. Downstream consumers must deduplicate by event_id.
