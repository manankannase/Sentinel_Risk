# Architecture and trust boundaries

## Two deployment surfaces

The public Pages demo uses static assets and an in-memory browser adapter. Exported XGBoost trees and sigmoid calibration produce synthetic scores locally. It never connects to the private FastAPI service or requires production credentials.

The private service uses FastAPI, SQLAlchemy/PostgreSQL, Redis and optionally a Kafka-compatible broker. Production permits shadow mode only. The shipped model has not been validated for real financial action.

## Request flow

1. Edge TLS proxy limits reads and body size. Only its fixed private address is trusted for forwarded client IPs.
2. API bounds/strictly parses JSON, authenticates current unrevoked/unexpired credentials, checks role, and applies IP/key rate limits. Credential configuration reload failures and Redis failures fail closed.
3. One customer row is locked. Existing normalized transaction ID returns the original response or conflicts if contents differ.
4. A bounded query selects strictly earlier same-tenant/customer INR events. Late events, cold starts and incomplete history receive REVIEW. Inference failure produces a null score and durable REVIEW.
5. The ledger, account high-water mark and optional outbox event commit atomically. Responses contain model and policy versions, feature snapshot and rule-based signals.
6. A worker publishes pending events. Publication failure retains them. The consumer validates exact authoritative event contents and deduplicates IDs; it performs no financial action. Production topics/ACLs are provisioned separately.
7. Analyst labels are stored with append-only events. Admin corrections require expected previous label and reason. The scoring response itself remains immutable.

## Isolation and operations

Every business read/write includes tenant scope. INR analytics exclude preserved USD rows. Cursor pagination orders by server-created timestamp and transaction ID. Feature monitoring selects the active model version. Metrics have bounded labels; pending outbox count/age come from storage.

Migrations run separately with owner privileges. Runtime group-role grants deny API rewriting/deleting decision history. Model files/credentials/CA bundles are read-only mounts; credential directory mounts permit atomic key replacement. Container image and manifest digests are release inputs.

PostgreSQL customer locks serialize hot customers; the publisher uses one global advisory lock. These simplify ordering but bound scalability. All-time overview queries, 24-hour history scans, growing audit tables and database connection pools must be measured before increasing traffic. No unsupported throughput or HA claim is made.
