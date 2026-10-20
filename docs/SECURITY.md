# Security model

## Assets and boundaries

Protect credentials, transaction history, analyst outcomes, tenant boundaries, model/policy integrity and availability. Trusted upstream systems must derive customer/device/payee identifiers and event amounts. A bearer credential authorizes a caller; it does not prove its transaction facts are truthful.

Controls include strict bounded JSON, body deadlines, role/tenant checks, expiring revocable hashed keys, Redis rate limits, native JSON model loading, model/manifest/metrics integrity, finite calibration, parameterized SQL, immutable decisions, auditable feedback corrections, exact outbox verification, TLS verification in production configuration, security headers, and hardened application containers.

API keys are high-entropy machine credentials. Production human access requires an identity-aware gateway/SSO/MFA and individual credentials. No passwords, PINs, OTPs or raw financial identifiers belong in requests. Bodies and bearer tokens must not be logged by the gateway.

## Configuration

`SENTINEL_ENV=production` requires verified PostgreSQL TLS, `rediss://` with certificate/hostname verification, explicit allowed hosts, independent model/manifest pins and shadow mode. Broker connections require SASL_SSL. The production Compose file exposes only the Caddy TLS edge; API/Redis/database ports must remain private.

Use `deploy/roles.sql` after migrations and separate login roles for API/worker. Do not run the API as the database owner. A database administrator can still change storage; this ledger is not cryptographically tamper-proof against privileged operators. Export audit records to appropriately controlled immutable storage if that threat is in scope.

Mount only the key configuration directory, never a directory containing plaintext token copies, into production API containers. Atomic key replacement is detected on subsequent authentication. Invalid key configuration fails closed. Credential compromise response: revoke key, review its access window/audit records, issue replacement, investigate and preserve evidence. Use distinct names during overlap rotation.

## Failure behavior

Redis failure -> 503; database failure -> 503 and rolled-back transaction; inference failure -> persisted REVIEW with null score; malformed model/policy -> startup failure; broker failure -> pending outbox; invalid event -> quarantine; stale feedback correction -> 409. Caller timeouts must reuse the same ID. Never treat an unavailable/REVIEW result as permission to send money.

## Remaining security work for a live operator

External penetration test; edge DDoS controls; secret-manager integration and IAM; SSO/MFA and user lifecycle; private network/firewall verification; backup encryption/PITR and restore drill; dependency/container OS scanning; region and retention governance; incident response and alert routing. Runtime credentials do not authorize arbitrary user-supplied CSVs to be promoted as production models.

The original six tests and the expanded regression tests are not a proof that every vulnerability is absent. See `AUDIT_AND_RESEARCH.md` and `TESTING.md` for tested scope and limitations. Do not paste credentials or sensitive transactions into public GitHub issues.
