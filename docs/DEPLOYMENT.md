# Deployment and recovery runbook

## Public demo

The public demo is a static, synthetic INR application. Build with:

```sh
uv sync --frozen
uv run python scripts/build_demo.py
node tests/demo_parity.cjs
node tests/demo_flow.cjs
```

Publish only `dist/demo/`. Never upload `.env`, `.secrets`, `runtime`, CSV datasets or a private model. GitHub Pages is configured for Actions; the initial prebuilt `gh-pages` branch has its own deployment workflow. After merging this revision, `.github/workflows/pages.yml` rebuilds/publishes from `main`. Reverting a public demo release means republishing a verified prior static build, not reverting a production database.

## Local backend

Fresh checkout:

```sh
uv sync --frozen
uv run python scripts/bootstrap.py
uv run python -m sentinel.db migrate
uv run uvicorn sentinel.api:app --host 127.0.0.1 --port 8000 --no-server-header
```

Use `.secrets/admin_token` locally; never paste it into the public demo. In a second shell:

```sh
uv run python scripts/replay.py --count 80 --feedback
uv run python scripts/smoke.py --token-file .secrets/admin_token
```

Docker development: `docker compose up --build` (or installed `docker-compose`). It includes local PostgreSQL/Redis/Redpanda, private networks and persistent broker/database volumes. Local broker plaintext is deliberately restricted to the development network. This file is not the production deployment.

Existing v1 checkout: back up the database and preserve `.secrets`. Deploy new code and `artifacts/inr-v2`, run `python -m sentinel.db migrate`, then `python scripts/sync_model_digest.py` to update pins without rotating existing database passwords. **Do not rerun bootstrap or change USD records to INR.** New API submissions accept only INR. Old USD records remain visible with their currency and do not contribute to INR features/totals. Existing non-expiring local keys work in development; issue expiring replacements before production.

## Production shadow service

Required owner inputs: funded host/provider, DNS/domain, TLS contact, managed PostgreSQL and Redis endpoints/CA bundles, credentials, region/retention policy, backup/PITR policy, incident owner and representative-data validation plan. Streaming is optional; if enabled, provide a SASL_SSL Kafka-compatible broker and pre-created topic. This revision does not purchase services or claim a running cloud backend.

1. Build/test the release image in CI. Push it to your trusted registry and resolve its immutable digest. Set `SENTINEL_IMAGE=registry/path@sha256:...`; select a tested Caddy 2 image digest as `CADDY_IMAGE`.
2. Prepare `.secrets/credentials/` containing **only** `api_keys.json`. Mounting the directory permits atomic reload. Keep plaintext issued tokens elsewhere, mode 0600. Give UID/GID 10001 read/traverse access to the mounted config/CA directories, with no broad host access.
3. Copy `deploy/production.env.example` to `.secrets/production.env`; replace placeholders. Provide PEM files under `.secrets/ca/`. Use correctly URL-encoded database passwords. Pin the actual SHA-256 of both `artifacts/inr-v2/model.json` and `manifest.json`.
4. Create separate login roles for migration owner, API and worker. Create `.secrets/migration.env` with the migration-owner URL and the other required settings. Run `docker compose -f compose.production.yaml --profile maintenance run --rm migrate`. Apply `deploy/roles.sql` as owner, then grant `sentinel_api`/`sentinel_worker` to their corresponding service logins. Never give these runtime logins ownership, superuser, CREATE or BYPASSRLS privileges.
5. Set `DOMAIN` and `ACME_EMAIL` in the Compose environment. Add only your domain and loopback healthcheck name to `SENTINEL_ALLOWED_HOSTS`. Configure DNS, firewall ports 80/443 and private connections to stores. The fixed proxy IP `172.30.19.10` is the only trusted forwarded-header sender; change subnet and CLI trust together if this network conflicts.
6. Run `docker compose -f compose.production.yaml up -d api caddy`. Production configuration requires shadow mode and verified TLS. The application container has no host port. Caddy obtains/renews public HTTPS certificates. The readiness endpoint must pass; failed startup is not a usable deployment.
7. For streaming, configure broker in **both** API and `.secrets/worker.env`, provision the topic/ACLs/replication/retention first, then run with `--profile streaming`. A worker role needs SELECT/UPDATE outbox and INSERT/SELECT consumed/quarantine, not transaction write permission. Choose replication/minimum in-sync replicas to match failure tolerance; local single-node defaults are insufficient.
8. Run a smoke test against a dedicated test tenant, not customer history. Connect private Prometheus to `/metrics` using its own metrics key. Public proxy denies `/metrics` and OpenAPI. Configure alert receivers; loading alert rules alone does not notify anyone. One API process per container keeps metrics process-local and predictable.

Production application roles and financial rail integration remain separate. All production `decision` values are REVIEW; `model_decision` is observational. The upstream payment system must continue its existing policy and ignore these shadow decisions for actual authorization.

## Credentials and rotation

```sh
uv run python -m sentinel.credentials --keys-file .secrets/credentials/api_keys.json issue \
  --name scorer-2026-10 --tenant your_tenant --role scorer --days 90 \
  --token-file .secrets/scorer-2026-10.token
```

Distribute the token through the secret manager. Validate new credentials, switch the caller, then revoke the old name:

```sh
uv run python -m sentinel.credentials --keys-file .secrets/credentials/api_keys.json revoke \
  --name previous-scorer --tenant your_tenant
```

Use unique names during overlap. Tool never prints tokens or overwrites existing token files. API reloads changed configuration on authentication and denies revoked/expired keys. In development Compose, a single-file bind mount may retain its inode after atomic replacement; recreate that container or use the production directory-mount layout.

## Backup and restore

Use managed encrypted backups and point-in-time recovery with retention and RPO/RTO agreed by the operator. A local Docker volume is not a backup. Separate backup credentials from API credentials.

For a portable logical backup, install PostgreSQL 18 client tools; configure a private libpq service named `sentinel-backup` with verified TLS and protected credentials, then:

```sh
uv run python scripts/backup.py --service sentinel-backup --output backups/sentinel-2026-10-20.dump
```

Store the dump and checksum encrypted off-host. Test restore quarterly into a **new isolated database** with no production network access. Use `pg_restore --no-owner --no-privileges --exit-on-error` against that explicitly chosen empty target, then run migrations and role grants, verify tenant/ledger/feedback/outbox counts, load the pinned model and exercise retries. Do not use `--clean` against a live database. Retain original backup until restore evidence has been reviewed. This repository cannot verify provider backup configuration or a live restore without those resources.

## Rollback and incidents

- Preserve the previous image, model/manifest pins and configuration. New schema is additive; original USD rows are untouched. Never downgrade by deleting columns/tables. Verify the chosen app supports the current schema; otherwise restore to a separate database and switch traffic after verification.
- Database/Redis unavailable: callers receive 503, retain their transaction ID, use bounded backoff, and follow the upstream incident policy. Do not convert API failure into payment approval.
- Inference failures: REVIEW with null score; inspect release/artifact compatibility and rollback approved model/image if needed. No silent random or zero score.
- Broker outage: monitor count and oldest pending age, repair broker/ACL/certificate issues, then allow retries. Duplicates are expected and deduplicated. Never mark events published manually to clear an alert.
- Disk pressure: stop new load, expand storage/restore capacity, preserve pending/audit evidence. Do not indiscriminately prune customer volumes or delete outbox rows.

## Release gate

Require green unit/integration tests, browser-model parity, demo adapter tests, dependency audit, container build/smoke, config validation, representative shadow metrics and restore evidence appropriate to the release. CI results are attached as artifacts. No hardware throughput/SLA claim is made until measured on the intended infrastructure. Browser automation was disabled in the development environment; a maintainer must complete visual/browser QA before a broader launch.
