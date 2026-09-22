# Sentinel Risk

Auditable real-time transaction-risk demo joining data science, ML serving, and systems design.

It creates a synthetic chronological fraud dataset; trains a native-JSON XGBoost model; serves tenant-scoped decisions through FastAPI; records an audit trail; supports manual feedback; and uses a transactional outbox for reliable event publishing.

This is a student portfolio project. It scores synthetic USD transactions and never sends, blocks, or authorizes payments. Model metrics are synthetic-study results, not claims about real fraud performance.

## Start locally

Requirements: macOS/Linux, Python 3.12, Docker Desktop if using containers.

    cd sentinel-risk
    uv sync --frozen
    uv run python scripts/bootstrap.py
    uv run python -m sentinel.db migrate
    uv run uvicorn sentinel.api:app --host 127.0.0.1 --port 8000 --no-server-header

Open http://127.0.0.1:8000. The workspace key is the complete single line in `.secrets/admin_token`; paste it into the login screen. It is a generated local API credential—not your name, email, college, roll number, or any paid service key. On macOS, copy it without displaying it:

    pbcopy < .secrets/admin_token

It is generated uniquely on your machine and never included in the ZIP. Keep it private.

In another terminal, create activity:

    cd sentinel-risk
    uv run python scripts/replay.py --count 80 --feedback

Run verification:

    uv run pytest -q
    uv run ruff check .

## Start with containers

The container stack uses PostgreSQL, Redis, and Redpanda. The API binds only to 127.0.0.1.

    cd sentinel-risk
    uv run python scripts/bootstrap.py
    docker compose up --build

If your installation provides the standalone command, use `docker-compose up --build` instead.

Open http://127.0.0.1:8000 and use .secrets/admin_token.

The default Docker setup runs an isolated local Redpanda broker using plaintext traffic on its private Docker network. For production configure a managed Kafka-compatible broker with SASL_SSL; production settings reject other broker protocols.

Optional monitoring:

    docker compose -f compose.yaml -f compose.observability.yaml --profile observability up -d

Prometheus runs at http://127.0.0.1:9090 and Grafana at http://127.0.0.1:3000. Grafana username is `admin`; its generated password is `GF_ADMIN_PASSWORD` in `.env`.

## Design

    Browser -> FastAPI API -> PostgreSQL audit ledger
                                -> transactional outbox -> Redpanda -> idempotent consumer
                          -> Redis rate limits

    Offline training -> chronological delayed-label-safe features -> XGBoost JSON artifact
                                                        -> digest-verified live scoring

The API locks one customer before reading history and writing a transaction. Each scoring request sees strictly ordered history for 1-hour and 24-hour velocity features. Repeating an identical transaction ID returns the original decision; reusing it with different contents returns HTTP 409.

## Roles

admin_token: score, investigate, dashboard. analyst_token: investigate and immutable feedback. scorer_token: score only. metrics_token: metrics endpoint only.

## Security

Bounded JSON bodies, strict request schemas, bearer-token roles, tenant isolation, rate limits, explicit content type checks, CSP/security headers, generic error responses, model SHA-256 verification, native JSON model loading, SQL parameter binding, non-root read-only containers, private database/cache networks, and outbox idempotency are implemented.

No implementation can prove absence of every vulnerability. Before public deployment complete a threat model, secret-manager integration, TLS termination, dependency scanning, penetration testing, data-governance review, and load testing. See docs/DEPLOYMENT.md.

Detailed verification: [docs/TESTING.md](docs/TESTING.md). Architecture and trust boundaries: [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
