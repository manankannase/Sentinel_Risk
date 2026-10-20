# Sentinel Risk — INR transaction risk

An auditable transaction-risk platform with a public browser demo and a private Python API. Amounts use **Indian rupees, stored as integer paise**. XGBoost scores transaction behavior; each decision retains its feature snapshot, model/policy version and investigation history.

**[Open the public synthetic demo](https://manankannase.github.io/Sentinel_Risk/)** · [Audit and research](docs/AUDIT_AND_RESEARCH.md) · [Deployment](docs/DEPLOYMENT.md) · [Model card](docs/MODEL_CARD.md) · [Verification](docs/TESTING.md)

The public demo computes scores in your browser using the exported synthetic model. No API key, account or live server is required. Data resets on reload; never enter private payment information. It is an educational simulation, not a payment processor.

## What this version does

- INR/paise contract, Indian currency formatting, IST time features and UPI/IMPS/NEFT/RTGS channels alongside card/ATM channels.
- Shared offline/live features, separate chronological calibration, validation-selected thresholds, baseline comparison and expanding time backtests.
- Tenant-scoped API, strict input limits/deadlines, expiring/revocable role credentials, model and manifest integrity checks.
- Safe retries, late-event/cold-start review, durable inference-failure review, preserved USD history and audited feedback corrections.
- PostgreSQL customer locking and optional transactional outbox; Redis rate limiting; worker retries, quarantine and deduplication.
- Cursor pagination, model-specific distribution diagnostics, metrics/alert rules, container packaging and real-service CI tests.

**Production mode is shadow-only.** The shipped model is synthetic and has not established accuracy on real Indian fraud. Production `decision` is REVIEW; `model_decision` is an observational candidate. Existing payment authorization must remain with the upstream system. This project never sends or blocks money. See the audit for remaining data, security, integration and operational requirements.

## Run locally

Python 3.12+, `uv`; Docker optional.

```sh
git clone https://github.com/manankannase/Sentinel_Risk.git
cd Sentinel_Risk
uv sync --frozen
uv run python scripts/bootstrap.py
uv run python -m sentinel.db migrate
uv run uvicorn sentinel.api:app --host 127.0.0.1 --port 8000 --no-server-header
```

Open `http://127.0.0.1:8000` **on that computer** and use the complete key from `.secrets/admin_token`. On macOS, `pbcopy < .secrets/admin_token` copies it without displaying it. The public demo never needs this key.

```sh
uv run python scripts/replay.py --count 80 --feedback
```

Fresh local containers: `docker compose up --build` (or `docker-compose`). Existing v1 installations must follow the migration instructions in [DEPLOYMENT.md](docs/DEPLOYMENT.md); do not rerun bootstrap or relabel USD rows.

## Transaction contract

```json
{
  "transaction_id": "02bb1647-8f17-4726-a508-13ec7181a598",
  "customer_id": "customer_opaque_01",
  "merchant_id": "payee_opaque_01",
  "device_id": "device_opaque_01",
  "amount_minor": 12345,
  "currency": "INR",
  "country": "IN",
  "channel": "upi",
  "occurred_at": "2026-10-20T10:00:00+05:30"
}
```

`12345` means ₹123.45. Replace example ID/time for each new transaction; keep them unchanged for retries. Live scoring accepts events in the preceding 24 hours and at most 30 seconds ahead. `merchant_id` denotes the merchant or payee's opaque identifier. No UPI PIN, OTP, card/account number or raw personal identifier belongs in this schema. Channel support is a model feature, not a connection to NPCI/banks or a claim to detect every scam.

## Verify and retrain

```sh
uv run ruff check .
uv run pytest -q
uv run pip-audit
uv run python scripts/build_demo.py
node tests/demo_parity.cjs
node tests/demo_flow.cjs
uv run python scripts/backtest.py --output runtime/backtest-new
```

Train into a **new** directory with `uv run python -m sentinel.train --output artifacts/my-candidate`. Use `--csv path.csv` only with normalized INR transactions plus `label` and `label_available_at`. No training automatically promotes a model or changes a running server. Production model/policy promotion requires explicit pins and independent validation.

The synthetic holdout has AP approximately 0.578 versus 0.504 for the logistic baseline, with approximately 79.5% recall at the review threshold. These are synthetic model-only results, not live payment performance or policy-level recall.

## Deploy and maintain

The public static demo avoids expiring API/database trials. A real backend needs funded infrastructure and an operator. [Production Compose](compose.production.yaml) expects managed verified-TLS PostgreSQL/Redis, domain/certificate ownership, role-separated credentials, immutable image/model pins and shadow mode. Optional streaming requires a pre-provisioned SASL_SSL broker.

[Three-year availability plan](docs/AVAILABILITY.md) covers ownership, renewals, recovery copies, maintenance and migration between hosts. No free URL or code change can guarantee three years of uninterrupted availability.

MIT licensed. See [architecture](docs/ARCHITECTURE.md) and [security boundaries](docs/SECURITY.md).
