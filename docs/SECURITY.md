# Security policy and threat notes

Report suspected vulnerabilities privately to the repository owner. Do not include credentials or personal transaction data in an issue.

Implemented controls include strict schemas, body and query limits, content-type enforcement, security headers, role and tenant authorization, rate limits, generic server errors, parameterized SQL, customer ordering locks, request idempotency, immutable feedback, artifact digest verification, non-pickle model loading, transactional outbox delivery, poison-message quarantine, private service networks, read-only application containers, dropped application capabilities, and secret-free archives.

Known boundaries: local SQLite mode has process-local rate limiting and is for demonstration only; synthetic model metrics are not evidence of production accuracy; dashboard keys are static local credentials; local Redpanda uses plaintext only inside its private development network; TLS and cloud IAM are deployment responsibilities.
