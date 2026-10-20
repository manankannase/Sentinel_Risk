# Sentinel Risk: engineering audit and research

Audit date: 20 October 2026. Baseline: `6d430be`. Scope: source, model artifacts, API, browser client, database, credentials, worker, packaging, CI, deployment and operational readiness. No customer payment data was used. This is a code audit and engineering validation, not a penetration-test certification or regulatory opinion.

## Decision

The original repository was a compact synthetic fraud demonstration. It had useful foundations—tenant-scoped queries, parameterized SQL, a shared feature implementation, native XGBoost loading, customer locks and an outbox—but did not justify live payment decisions or a durable hosted-service claim.

This revision provides an INR simulation and a guarded **shadow scoring service**. It must not be connected to an automatic payment authorization/decline path. Production configuration deliberately requires shadow mode. `model_decision` preserves the candidate recommendation; `decision` remains `REVIEW` in shadow mode. This is an integration safeguard, not evidence that every payment should literally enter a human queue. The caller must treat shadow results as observational and keep its existing authorization policy.

## Findings and treatment

| Priority | Component / original failure | Implemented treatment | Remaining boundary |
|---|---|---|---|
| Critical for live use | Model trained and tested on one synthetic mechanism; apparent accuracy did not establish Indian fraud detection | Explicit synthetic provenance, dataset digest, independent calibration split, frozen validation thresholds, channel metrics, expanding chronological backtests, enforced production shadow mode | Representative consented/tokenized data and prospectively measured outcomes remain required |
| High | USD-only contract; replacing the symbol would mix incompatible amounts and model distributions | INR in integer paise; new versioned artifact; separate feature schema; currency isolation in history and totals; original currency displayed for legacy rows | Existing USD events are preserved, not converted; v1 clients must migrate |
| High | A model-file hash did not protect threshold, calibration or currency metadata | Independent manifest pin; manifest binds model and metrics hashes; startup validates feature contract and calibration | Trusted release approval, secret manager and immutable artifact distribution remain operational duties |
| High | Authentication keys loaded once, with no expiry/revocation workflow | Expiring keys, atomic reload on file replacement, revoked flag, fail-closed invalid configuration, issue/revoke CLI | Human SSO/MFA and identity lifecycle belong at the gateway; do not share an admin key among analysts |
| High | Bounded request bytes but no deadline; ambiguous duplicate JSON keys and NaN accepted by default parser | Total body-read deadline, size/content-type/encoding checks, strict JSON parser, secure headers on unexpected errors | Edge DDoS and connection controls still required; application limits are not volumetric protection |
| High | Out-of-order and simultaneous events rejected as 409, encouraging unsafe caller workarounds | Persist late/same-time events as REVIEW; exclude future/same-time history; never move account cursor backward; preserve original decisions | Events older than 24 hours remain rejected; historical replay/import needs a distinct controlled ingestion process |
| High | Inference failure produced an error without a durable decision | Null score plus explicit REVIEW fallback and failure counter, saved transactionally | Database failure still returns 503; caller must retry the same ID and must not interpret failure as approval |
| High | No-history customer had zero novelty flags and could receive ordinary approval | Cold-start feature and mandatory simulation REVIEW for absent INR history | First-seen handling and review capacity require business approval |
| High | History overflow failed without durable result | Bounded query; truncated feature status; skip scoring and persist REVIEW | Repeated huge histories remain expensive; move to tested incremental aggregates before high throughput |
| High | Production configuration did not enforce database/Redis certificate verification | PostgreSQL `verify-full`, Redis TLS/certificate/hostname checks, pinned manifest, explicit hosts, shadow mode | Cloud IAM, private networking, certificates and firewall rules must be provisioned |
| High | API and worker normally used a database owner/superuser | Separate runtime group-role grants; API cannot rewrite decisions or delete audit rows; worker cannot rewrite transactions | Operator must create login roles, assign grants and run migrations separately |
| Medium | “migrate” only called `create_all`, with no version or existing-table index upgrade | Schema version record, additive feedback-history table/indexes, startup version check, downgrade refusal | Changes to existing columns still require explicit future migrations; no destructive automatic conversion |
| Medium | Feedback had no correction path | Admin-only compare-and-set corrections with required reason; append-only history; original v1 label copied before correction | No full investigator assignment/SLA/appeal workflow; history endpoint returns at most 200 entries |
| Medium | Worker type comparison could equate JSON booleans with numeric values | Strict JSON and canonical type-sensitive payload comparison, quarantine and dedup tests, v1/v2 compatibility | This is an audit consumer, not a payment or notification processor |
| Medium | Worker attempted topic creation in production | Production expects pre-provisioned topic and broker ACLs; topic auto-creation restricted to local/test mode | Production replication/retention/minimum in-sync replicas must be configured by broker operator |
| Medium | Local mode accumulated events that no broker could deliver | Explicit ledger-only mode creates no outgoing events when broker disabled | Enabling streaming later does not retroactively publish ledger-only history |
| Medium | Redpanda state had no persistent volume | Persistent local broker volume and restart policies | Single-node Compose remains a development stack, not HA |
| Medium | Offset pagination stopped at 10,000 and shifted with new inserts | Stable keyset cursor and matching tenant/time index; UI cursor navigation | All-time overview aggregates remain potentially costly; materialize/cap analytics when load warrants it |
| Medium | Drift watch compared only means and could mix model versions | Version-specific snapshots, fixed training-bin PSI plus mean diagnostics | PSI is descriptive; thresholds are illustrative and there is no guarantee of detecting concept drift |
| Medium | Model scores were presented as probabilities without calibration evidence | Sigmoid calibration on a separate chronological segment; reliability bins/Brier score; precise UI wording | Calibration on synthetic data does not transfer to real customers |
| Medium | No capacity/value/segment evidence | Alert rate, precision at top 100, fraud-value recall, per-channel results and repeated time folds | No business-derived review budget, currency loss model or confidence intervals from real traffic |
| Medium | Retry script followed redirects with credentials and changed time after generating labels | Reject redirects, require HTTPS outside loopback, generate labels on the actual replay timeline | Replay credentials must remain local; synthetic feedback must never contaminate a real dataset |
| Medium | UI requests could wait indefinitely or update a page after navigation | Client deadline, aborts and navigation generation checks; retries retain transaction ID | Full cross-browser/accessibility and browser-based end-to-end QA still required |
| Medium | Six tests, no real PostgreSQL/Redis coverage or worker fault tests | Expanded regression suite; real service concurrency tests; worker delivery-failure/dedup tests; container smoke and CI evidence | Tests are evidence for covered cases, not an exhaustive security proof |
| Medium | README had localhost addresses but no public deployment | Public browser-only synthetic model build and Pages workflow; standalone downloadable assets | Hosting availability depends on repository/account/provider continuity |

## Research and comparison

### Fraud Detection Handbook

[Repository](https://github.com/Fraud-Detection-Handbook/fraud-detection-handbook) and [validation chapter](https://fraud-detection-handbook.github.io/fraud-detection-handbook/Chapter_5_ModelValidationAndSelection/ValidationStrategies.html).

Useful practices: chronological/prequential evaluation, label-availability delays, and investigator-capacity metrics. Adopted expanding time folds and explicit delayed-label exclusion. Kept a final holdout for each fold; validation chooses thresholds. Our calibration split is separate from both classifier training and threshold selection. The handbook is reproducible research, not a payment gateway implementation or proof of applicability to UPI.

### Lucas and Jurgovsky, credit-card fraud survey

[Paper](https://arxiv.org/abs/2010.06479).

The survey emphasizes severe imbalance, evolving distributions and sequential transaction behavior. This supports transaction aggregates and chronological tests over a random split or headline accuracy. It does not establish that a card-fraud model transfers to Indian account-to-account payments. We retain a simple tree model and expose segment/value metrics rather than adding deep learning without supporting data.

### River and Alibi Detect

[River ADWIN](https://riverml.xyz/latest/api/drift/ADWIN/) and [Alibi Detect algorithms](https://docs.seldon.ai/alibi-detect/algorithms).

These provide established approaches for streaming change detection and distribution tests. Adoption decision: keep the current small service dependency footprint; implement descriptive fixed-bin PSI with explicit limits. Add a statistical detector only after choosing reference windows, seasonality handling, false-alert targets and ownership. Covariate shift is not equivalent to model-performance degradation. Do not automatically retrain or promote from unlabeled drift alarms.

### IBM AMLSim

[Repository](https://github.com/IBM/AMLSim).

AMLSim generates banking transaction graphs with known laundering patterns. It is relevant to mule networks, fan-in/fan-out and connected-entity research. Its synthetic AML labels are a different target from transaction fraud. We did not bolt on a graph database or claim AML coverage: this service lacks recipient-network data and validated graph labels. A later graph component should be benchmarked on those signals independently.

### Leito2/realtime-fraud-detection

[Repository](https://github.com/Leito2/realtime-fraud-detection).

Its README describes Kafka/Flink/Redis/XGBoost, shadow models, open-loop load tests and failure experiments, but explicitly labels the project **M0 bootstrap** and leaves throughput claims unmeasured. This is a useful design reference, not verified production evidence. Adopted the emphasis on measured integration/fault testing. Did not copy a large distributed architecture without a measured bottleneck.

### Probability calibration

[scikit-learn documentation](https://scikit-learn.org/stable/modules/calibration.html).

Calibration data must be disjoint from classifier training. Here the timeline is 50% classifier training, 10% sigmoid calibration, 20% threshold validation and 20% final testing, with labels purged at training/calibration/validation cutoffs. Calibration coefficients are native JSON numbers bound by the manifest hash; no pickle is introduced. Test reliability bins expose sparse/high-score calibration weaknesses instead of hiding them behind one average.

### Security and Indian payment context

[OWASP API4](https://api-security.owasp.org/editions/2023/en/0xa4-unrestricted-resource-consumption/) motivates resource limits across request size, execution time and operational capacity. [NPCI fraud awareness](https://www.npci.org.in/fraud-awareness) illustrates social-engineering/QR/PIN scams that transaction amount alone cannot identify. This application must never collect UPI PINs, OTPs, PANs or raw account credentials. Use opaque customer/device/payee identifiers from a trusted integration.

[RBI digital payment security controls](https://systemhealth.rbi.org.in/Scripts/BS_ViewMasDirections.aspx_id%3D12032%283%29.html) and [non-bank PSO cyber-resilience directions](https://systemhealth.rbi.org.in/Scripts/BS_ViewMasDirections.aspx_id%3D12715%281%29.html) are relevant starting points for a regulated deployment's governance, monitoring and security review. Applicability depends on the operator and product; no RBI/NPCI certification or legal compliance is claimed by this repository.

## Real-world scenarios and current behavior

| Scenario | Current behavior | Further integration needed |
|---|---|---|
| Legitimate new customer | REVIEW with cold-start flag | Approved onboarding policy, friction measurement |
| Known customer, unseen device, large UPI transfer | Features and candidate score; shadow observation in production | Verified device binding, account age, beneficiary trust, identity assurance |
| Account takeover / remote-access scam | May show unusual behavior but has no direct device compromise evidence | Device/session telemetry and trusted case labels |
| Authorized push-payment scam | Can appear normal and escape the model | Intent/recipient intelligence, customer confirmation and scam signals |
| Mule account / fraud ring | Per-customer 24-hour features are insufficient | Receiver network, cross-account/entity linkage, temporal graph validation |
| Duplicate webhook or client retry | Same normalized payload and ID returns original decision | Caller must retain ID across timeouts |
| Same ID with modified amount/customer | 409 conflict | Investigate upstream idempotency misuse |
| Delayed/simultaneous event | Durable REVIEW; no future-history leakage | Separate historical reconciliation path for events older than 24 hours |
| Database outage | 503; no partial decision/outbox commit | Caller retry policy, backups, HA, recovery drills |
| Redis outage | Fail closed with 503 | Rate-limit store HA and explicit incident policy |
| Model inference error | Null score and durable REVIEW; counter increments | Incident alert and model rollback |
| Broker outage | Decision commits; outbox retains unpublished event | Monitor oldest age/storage; retry and dedup downstream |
| Analyst changes a label | Admin correction preserves original/history | Case evidence retention and approval workflow |
| Rupee display or rounding | Integer paise; Indian formatting; no binary-float amount input | Integration must provide paise; API cap is not an official rail limit |
| Legacy USD row | Remains USD; excluded from INR aggregate/features | Archive/retain under approved data policy; no automatic FX conversion |

## Scaling plan tied to measurements

1. Establish target peak rate, p95/p99 latency, maximum customer activity, retention, RPO/RTO and acceptable loss/friction. Current illustrative alert latency is 500 ms, not a measured SLA.
2. Keep one API process per container so Prometheus counters remain correct; scale replicas only after PostgreSQL pool and Redis capacity budgets are set. Each process can open up to 20 DB connections.
3. Benchmark high-activity customers, concurrent duplicate IDs, overlapping customer requests, slow database and broker outages. Customer locking intentionally serializes a customer's updates. Test hot customers, not only uniformly random IDs.
4. Replace repeated history scans with verified incremental aggregates only after this path is a measured bottleneck. Match offline/online event-time semantics, late-event handling, TTLs and rebuild procedures before switching.
5. Materialize overview statistics, use partition/retention policies and archive large audit histories after recovery and governance requirements are defined. Current all-time aggregates and one global publisher lock limit scale.
6. Introduce multi-publisher outbox claiming only with explicit per-customer ordering/fencing tests. Current single publisher is deliberately simple and bounded; delivery remains at-least-once.
7. Deploy an independently versioned challenger in shadow mode. Evaluate label maturity/selection bias and multiple time windows. Promote only through an approved release; never automatically from analyst feedback or a drift alarm.

## Gaps that code changes cannot honestly close here

- No real Indian fraud dataset or bank/PSP adapter was supplied. Feature collection is still caller-trusted.
- No production domain, cloud account, funded database or uptime commitment was supplied. The public static demo does not provide backend persistence.
- No independent penetration test, real-load capacity test, legal/privacy assessment, region selection or disaster-recovery acceptance was performed.
- No validated model for social engineering, mule networks, sanctions, AML or identity fraud is included.
- Model/rule attribution, statistically justified alerts, investigator staffing, SLA assignment and production outcome feedback ingestion remain separate product work.

See [verification evidence](TESTING.md), [deployment/runbook](DEPLOYMENT.md), [model card](MODEL_CARD.md) and [three-year availability plan](AVAILABILITY.md).
