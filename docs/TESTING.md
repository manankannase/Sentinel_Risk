# Verification evidence

The baseline had six tests, which passed before modification. This revision adds request-boundary, credential, currency, feature, fallback, concurrency, feedback-history, pagination, worker and model-integrity coverage.

## Run

```sh
uv sync --frozen
uv run ruff check .
uv run pytest -q --junitxml=runtime/tests.xml
uv run pip-audit --format json --output runtime/dependency-audit.json
uv run python scripts/build_demo.py
node tests/demo_parity.cjs
node tests/demo_flow.cjs
```

PostgreSQL and Redis tests are skipped unless explicit test URLs are set. They create/drop only randomly named `sentinel_test_*` databases; use a dedicated test server, never production credentials.

```sh
docker compose -f compose.test.yaml up -d --wait
SENTINEL_TEST_POSTGRES='postgresql+psycopg://sentinel_test:local-integration-only@127.0.0.1:55432/sentinel_test' \
SENTINEL_TEST_REDIS='redis://127.0.0.1:56379/0' \
uv run pytest -q tests/test_postgres.py
```

## Covered failure cases

- Duplicate concurrent requests commit exactly one decision/outbox; conflicting IDs cannot cross customer boundaries.
- Prior/future/same-time, 5m/1h/24h windows, IST midnight and currency isolation.
- INR input rejects floats, booleans, negative/oversized amounts, numeric/naive timestamps, unknown fields and invalid channels.
- Duplicate JSON keys, NaN/Infinity, overflow floats, deep malformed input and slow uploads fail safely.
- Tenant isolation, analyst/scorer permissions, revoked/expired credentials and broken credential configuration.
- Redis failure denies admission; shared Redis counters remain atomic across limiter instances.
- Model failure persists null-score REVIEW; cold starts, late events and history overflow cannot silently approve.
- Feedback corrections preserve history, require admin/expected previous value and reject stale changes.
- Stable cursor pages under concurrent inserts; malformed cursors return client errors.
- Broker failure retains unpublished rows; duplicate consumption is idempotent; tampered events are quarantined.
- Manifest/metrics/model integrity, train/serve calibration parity, immutable output directories and failed candidate promotion.
- Browser export: 400 comparisons against Python, maximum observed score difference below 0.00000015 in local verification. Adapter login/analytics/pagination/scoring/retry/feedback are tested in Node.

## Local observations on 20 October 2026

- Final local suite: 59 passed, one infrastructure error loading PostgreSQL’s `plpgsql.so`; see `docs/evidence/verification.json` for final counts.
- Actual PostgreSQL/Redis integration: three tests passed, including concurrent requests and distributed rate limiting; the added role-grants test hit a local Docker storage I/O error. Clean-runner CI verifies that additional path.
- Synthetic expanding backtests: all three folds passed; summarized results are in `docs/evidence/backtest.json`.
- Installed Python dependency audit: no known vulnerabilities reported at scan time. This does not cover all OS packages, future advisories or application logic.
- Docker build installed dependencies but the local Docker storage failed with an input/output error. Host free space was below 1 GB; no user volumes were pruned. A clean GitHub runner is the independent container-build/smoke verification path.
- Browser automation was disabled in application settings. No claim of visual/browser QA is made. Node tests verify adapter/inference behavior, not rendering or accessibility across browsers.
- No real banking data, bank/PSP integration, production load, regulatory certification or live backup restore was tested.

CI runs real PostgreSQL/Redis tests, dependency audit, demo parity, time backtests and a read-only container smoke test. Release evidence is retained as a workflow artifact. Do not mark a failed, pending or skipped integration check as passed.
