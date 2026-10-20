import base64
import binascii
import hashlib
import json
import logging
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import redis
from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from prometheus_client import CollectorRegistry, Counter, Histogram, generate_latest
from sqlalchemy import case, func, select, text, update
from sqlalchemy.exc import IntegrityError, SQLAlchemyError
from starlette.middleware.trustedhost import TrustedHostMiddleware

from sentinel.config import Settings
from sentinel.db import (
    accounts,
    consumed,
    engine_for,
    feedback,
    feedback_events,
    lock_customer,
    now_iso,
    outbox,
    transactions,
    verify_schema,
    write_transaction,
)
from sentinel.features import build_features, utc
from sentinel.ml import RiskModel
from sentinel.monitoring import population_stability
from sentinel.schemas import FeedbackCorrection, FeedbackInput, TransactionInput
from sentinel.security import KeyStore, RateLimiter, RequestBoundary, authorize

log = logging.getLogger("sentinel")
STATIC = Path(__file__).parent / "static"


def create_app(settings=None):
    settings = settings or Settings()
    @asynccontextmanager
    async def lifespan(app):
        app.state.settings = settings
        app.state.engine = engine_for(settings.database_url)
        with app.state.engine.connect() as conn:
            verify_schema(conn)
            conn.execute(select(transactions.c.transaction_id).limit(1))
        app.state.keys = KeyStore(settings.keys_file, require_expiry=settings.env == "production")
        app.state.model = RiskModel(settings.model_dir, settings.model_sha256, settings.manifest_sha256)
        app.state.redis = redis.Redis.from_url(settings.redis_url, socket_timeout=2, socket_connect_timeout=2) if settings.redis_url else None
        if app.state.redis:
            app.state.redis.ping()
        app.state.limiter = RateLimiter(app.state.redis)
        app.state.registry = CollectorRegistry()
        app.state.decisions = Counter("sentinel_decisions_total", "New scored decisions", ["decision"], registry=app.state.registry)
        app.state.latency = Histogram("sentinel_scoring_seconds", "Scoring including commit", registry=app.state.registry)
        app.state.fallbacks = Counter("sentinel_model_failures_total", "Model failures routed to review", registry=app.state.registry)
        yield
        if app.state.redis:
            app.state.redis.close()
        app.state.engine.dispose()
    app = FastAPI(title="Sentinel Risk", version="1.0.0", lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=[h.strip() for h in settings.allowed_hosts.split(",")])
    app.add_middleware(RequestBoundary, max_bytes=settings.max_body_bytes, production=settings.env == "production",
                       body_timeout_seconds=settings.body_timeout_seconds)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        return JSONResponse(status_code=422, content={"detail": "Invalid request fields", "fields": [".".join(map(str, e["loc"])) for e in exc.errors()]})

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request, exc):
        log.error("Database operation failed (%s)", type(exc).__name__)
        return JSONResponse(status_code=503, content={"detail": "Storage temporarily unavailable; retry with the same transaction ID"})

    @app.exception_handler(Exception)
    async def internal_error(request, exc):
        log.error("Request failed (%s)", type(exc).__name__)
        return JSONResponse(status_code=500, content={"detail": "Internal error"})

    @app.get("/healthz")
    def liveness():
        return {"status": "ok"}

    @app.get("/readyz")
    def readiness(request: Request):
        try:
            with request.app.state.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            if request.app.state.redis:
                request.app.state.redis.ping()
        except Exception:
            return JSONResponse({"status": "unavailable"}, status_code=503)
        return {"status": "ready"}

    @app.get("/api/v1/openapi.json")
    def schema(principal=Depends(authorize("admin"))):
        return app.openapi()

    @app.get("/api/v1/me")
    def me(principal=Depends(authorize("admin", "analyst", "scorer"))):
        return {"name": principal.name, "tenant": principal.tenant, "role": principal.role}

    @app.post("/api/v1/score")
    def score(tx: TransactionInput, request: Request, principal=Depends(authorize("admin", "scorer"))):
        state = request.app.state
        event = tx.model_dump(mode="json")
        event["occurred_at"] = tx.occurred_at.isoformat(timespec="microseconds")
        digest = hashlib.sha256(json.dumps(event, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
        with state.latency.time():
            try:
                with write_transaction(state.engine) as conn:
                    account = lock_customer(conn, principal.tenant, tx.customer_id)
                    existing = conn.execute(select(transactions).where(transactions.c.tenant == principal.tenant,
                        transactions.c.transaction_id == str(tx.transaction_id))).mappings().first()
                    if existing:
                        if existing["request_hash"] != digest:
                            raise HTTPException(409, "Transaction ID already used with a different payload")
                        return existing["response"]
                    now = datetime.now(timezone.utc)
                    if tx.occurred_at > now + timedelta(seconds=30) or tx.occurred_at < now - timedelta(hours=24):
                        raise HTTPException(422, "Event must be within the previous 24 hours and no more than 30 seconds ahead")
                    late = bool(account["last_event"] and utc(account["last_event"]) >= tx.occurred_at)
                    since = (tx.occurred_at - timedelta(hours=24)).isoformat(timespec="microseconds")
                    history = conn.execute(select(transactions.c.event).where(transactions.c.tenant == principal.tenant,
                        transactions.c.customer_id == tx.customer_id, transactions.c.occurred_at >= since,
                        transactions.c.occurred_at < event["occurred_at"],
                        transactions.c.event["currency"].as_string() == "INR")
                        .order_by(transactions.c.occurred_at).limit(settings.max_customer_history + 1)).scalars().all()
                    truncated = len(history) > settings.max_customer_history
                    features = build_features(event, history[:settings.max_customer_history])
                    manifest = state.model.manifest
                    flags = []
                    if late:
                        flags.append("Late or same-time event: history may be incomplete")
                    if not history:
                        flags.append("No prior INR history: manual review required")
                    if truncated:
                        flags.append("Customer history bound exceeded: model scoring skipped")
                    risk, model_decision = None, None
                    if not truncated:
                        try:
                            risk = state.model.predict(features)
                            model_decision = "DECLINE" if risk >= manifest["decline_threshold"] else "REVIEW" if risk >= manifest["review_threshold"] else "APPROVE"
                        except Exception:
                            # Persist an explicit fallback; never turn an inference failure into an approval.
                            state.fallbacks.inc()
                            log.error("Model prediction failed; decision routed to review")
                            flags.append("Model unavailable: manual review required")
                    if settings.decision_mode == "shadow":
                        flags.append("Shadow mode: model recommendation is advisory only")
                    decision = "REVIEW" if flags else model_decision
                    created = now_iso()
                    result = {k: event[k] for k in ["transaction_id", "customer_id", "merchant_id", "amount_minor", "currency", "occurred_at"]}
                    result.update(score=round(risk, 6) if risk is not None else None, decision=decision,
                        model_decision=model_decision, mode=settings.decision_mode, policy_version=state.model.policy_version,
                        model_version=manifest["model_version"], feature_status="truncated" if truncated else "complete",
                        reasons=flags + state.model.reasons(features), features=features, created_at=created, label=None)
                    conn.execute(transactions.insert().values(tenant=principal.tenant, transaction_id=str(tx.transaction_id),
                        customer_id=tx.customer_id, occurred_at=event["occurred_at"], decision=decision,
                        request_hash=digest, event=event, response=result, features=features, created_at=created))
                    if not late:
                        conn.execute(update(accounts).where(accounts.c.tenant == principal.tenant, accounts.c.customer_id == tx.customer_id)
                                     .values(last_event=event["occurred_at"]))
                    event_id = str(uuid4())
                    payload = {"event_id": event_id, "schema_version": 2, "tenant": principal.tenant,
                               "transaction_id": str(tx.transaction_id), "customer_id": tx.customer_id,
                               "score": result["score"], "decision": decision, "model_version": manifest["model_version"],
                               "created_at": created, "currency": "INR", "policy_version": state.model.policy_version,
                               "mode": settings.decision_mode}
                    if settings.broker:
                        conn.execute(outbox.insert().values(id=event_id, tenant=principal.tenant, customer_id=tx.customer_id,
                                                           payload=payload, created_at=created))
            except IntegrityError:
                # Concurrent duplicate with a different customer acquires a different account lock.
                with state.engine.connect() as conn:
                    previous = conn.execute(select(transactions).where(transactions.c.tenant == principal.tenant,
                        transactions.c.transaction_id == str(tx.transaction_id))).mappings().first()
                if previous and previous["request_hash"] == digest:
                    return previous["response"]
                raise HTTPException(409, "Conflicting transaction ID") from None
        state.decisions.labels(decision=decision).inc()
        return result

    @app.get("/api/v1/transactions")
    def list_transactions(request: Request, limit: int = Query(50, ge=1, le=200), offset: int = Query(0, ge=0, le=10000),
                          decision: Literal["APPROVE", "REVIEW", "DECLINE"] | None = None,
                          cursor: str | None = Query(None, max_length=256),
                          principal=Depends(authorize("admin", "analyst"))):
        stmt = select(transactions.c.response, feedback.c.label, transactions.c.created_at,
                      transactions.c.transaction_id).outerjoin(feedback,
            (transactions.c.tenant == feedback.c.tenant) & (transactions.c.transaction_id == feedback.c.transaction_id))
        stmt = stmt.where(transactions.c.tenant == principal.tenant)
        if decision:
            stmt = stmt.where(transactions.c.decision == decision)
        if cursor:
            try:
                point = json.loads(base64.b64decode(cursor, altchars=b"-_", validate=True))
                at, identifier = point["at"], str(UUID(point["id"]))
                if utc(at).isoformat(timespec="microseconds") != at or offset:
                    raise ValueError
            except (ValueError, TypeError, KeyError, AttributeError, binascii.Error):
                raise HTTPException(422, "Invalid pagination cursor") from None
            stmt = stmt.where((transactions.c.created_at < at) |
                ((transactions.c.created_at == at) & (transactions.c.transaction_id < identifier)))
        with request.app.state.engine.connect() as conn:
            rows = conn.execute(stmt.order_by(transactions.c.created_at.desc(), transactions.c.transaction_id.desc())
                                .limit(limit + 1).offset(offset)).all()
        more, rows = len(rows) > limit, rows[:limit]
        next_cursor = base64.urlsafe_b64encode(json.dumps(
            {"at": rows[-1][2], "id": rows[-1][3]}).encode()).decode() if more else None
        return {"items": [dict(r[0], label=r[1]) for r in rows], "offset": offset, "limit": limit, "next_cursor": next_cursor}

    @app.post("/api/v1/transactions/{transaction_id}/feedback")
    def record_feedback(transaction_id: UUID, body: FeedbackInput, request: Request,
                        principal=Depends(authorize("admin", "analyst"))):
        from sentinel.db import insert_ignore
        with write_transaction(request.app.state.engine) as conn:
            exists = conn.execute(select(transactions.c.customer_id).where(transactions.c.tenant == principal.tenant,
                                  transactions.c.transaction_id == str(transaction_id))).first()
            if not exists:
                raise HTTPException(404, "Transaction not found")
            lock_customer(conn, principal.tenant, exists[0])
            previous = conn.execute(select(feedback.c.label).where(feedback.c.tenant == principal.tenant,
                                   feedback.c.transaction_id == str(transaction_id))).scalar_one_or_none()
            insert_ignore(conn, feedback, {"tenant": principal.tenant, "transaction_id": str(transaction_id),
                                          "label": body.label, "actor": principal.name, "created_at": now_iso()})
            current = conn.execute(select(feedback.c.label).where(feedback.c.tenant == principal.tenant,
                                  feedback.c.transaction_id == str(transaction_id))).scalar_one()
            if current != body.label:
                raise HTTPException(409, "Feedback is immutable; conflicting label requires an audited correction process")
            if previous is None:
                conn.execute(feedback_events.insert().values(id=str(uuid4()), tenant=principal.tenant,
                    transaction_id=str(transaction_id), label=body.label, previous_label=None,
                    actor=principal.name, reason="Initial investigation", created_at=now_iso()))
        return {"transaction_id": str(transaction_id), "label": body.label}

    @app.post("/api/v1/transactions/{transaction_id}/feedback/corrections")
    def correct_feedback(transaction_id: UUID, body: FeedbackCorrection, request: Request,
                         principal=Depends(authorize("admin"))):
        with write_transaction(request.app.state.engine) as conn:
            customer = conn.execute(select(transactions.c.customer_id).where(
                transactions.c.tenant == principal.tenant,
                transactions.c.transaction_id == str(transaction_id))).scalar_one_or_none()
            if customer is None:
                raise HTTPException(404, "Transaction not found")
            lock_customer(conn, principal.tenant, customer)
            where = (feedback.c.tenant == principal.tenant, feedback.c.transaction_id == str(transaction_id))
            previous = conn.execute(select(feedback).where(*where)).mappings().first()
            if previous is None:
                raise HTTPException(404, "No feedback to correct")
            if previous["label"] != body.expected_label or body.label == body.expected_label:
                raise HTTPException(409, "Feedback changed or correction is a no-op")
            # Preserve v1 feedback in history before its first correction.
            old = conn.execute(select(feedback_events.c.id).where(feedback_events.c.tenant == principal.tenant,
                feedback_events.c.transaction_id == str(transaction_id)).limit(1)).first()
            if old is None:
                conn.execute(feedback_events.insert().values(id=str(uuid4()), tenant=principal.tenant,
                    transaction_id=str(transaction_id), label=previous["label"], previous_label=None,
                    actor=previous["actor"], reason="Preserved legacy feedback", created_at=previous["created_at"]))
            created = now_iso()
            conn.execute(feedback_events.insert().values(id=str(uuid4()), tenant=principal.tenant,
                transaction_id=str(transaction_id), label=body.label, previous_label=previous["label"],
                actor=principal.name, reason=body.reason, created_at=created))
            conn.execute(update(feedback).where(*where).values(label=body.label, actor=principal.name, created_at=created))
        return {"transaction_id": str(transaction_id), "label": body.label}

    @app.get("/api/v1/transactions/{transaction_id}/feedback/history")
    def feedback_history(transaction_id: UUID, request: Request, principal=Depends(authorize("admin", "analyst"))):
        with request.app.state.engine.connect() as conn:
            rows = conn.execute(select(feedback_events).where(feedback_events.c.tenant == principal.tenant,
                feedback_events.c.transaction_id == str(transaction_id))
                .order_by(feedback_events.c.created_at, feedback_events.c.id).limit(200)).mappings().all()
        return {"items": [dict(row) for row in rows]}

    @app.get("/api/v1/overview")
    def overview(request: Request, principal=Depends(authorize("admin", "analyst"))):
        scoped = (transactions.c.tenant == principal.tenant) & (transactions.c.event["currency"].as_string() == "INR")
        with request.app.state.engine.connect() as conn:
            row = conn.execute(select(func.count().label("total"),
                func.coalesce(func.sum(case((transactions.c.decision == "APPROVE", 1), else_=0)), 0).label("approved"),
                func.coalesce(func.sum(case((transactions.c.decision == "REVIEW", 1), else_=0)), 0).label("review"),
                func.coalesce(func.sum(case((transactions.c.decision == "DECLINE", 1), else_=0)), 0).label("declined"),
                func.coalesce(func.sum(transactions.c.event["amount_minor"].as_integer()), 0).label("volume_minor"),
                func.coalesce(func.avg(transactions.c.response["score"].as_float()), 0).label("average_risk"))
                .select_from(transactions).where(scoped)).mappings().one()
            # Hourly buckets make short demo replays visible without fabricated historical data.
            bucket = func.substr(transactions.c.occurred_at, 1, 13)
            series = conn.execute(select(bucket.label("hour"), func.count().label("total"),
                func.avg(transactions.c.response["score"].as_float()).label("risk"))
                .where(scoped, transactions.c.occurred_at >= (datetime.now(timezone.utc)-timedelta(hours=24)).isoformat(timespec="microseconds"))
                .group_by(bucket).order_by(bucket)).mappings().all()
        return {**dict(row), "currency": "INR", "series": [dict(s) for s in series]}

    @app.get("/api/v1/model")
    def model_details(request: Request, principal=Depends(authorize("admin", "analyst"))):
        model = request.app.state.model
        with request.app.state.engine.connect() as conn:
            rows = conn.execute(select(transactions.c.features).where(transactions.c.tenant == principal.tenant,
                                transactions.c.response["model_version"].as_string() == model.manifest["model_version"])
                                .order_by(transactions.c.created_at.desc()).limit(500)).scalars().all()
            label_count = conn.execute(select(func.count()).select_from(feedback).where(feedback.c.tenant == principal.tenant)).scalar_one()
        warnings = []
        if len(rows) >= 100:
            for name, ref in model.metrics["reference"].items():
                mean = sum(r[name] for r in rows) / len(rows)
                z = abs(mean-ref["mean"]) / max(ref["std"], 0.01)
                if z > 2:
                    warnings.append({"feature": name, "mean_shift_std": round(z, 2)})
        return {"manifest": model.manifest, "metrics": model.metrics,
                "monitoring": {"sample_size": len(rows), "labels_received": label_count,
                    "mean_shift_warnings": warnings,
                    "population_stability": population_stability(rows, model.metrics.get("histograms", {})),
                    "method": "Training-bin PSI and mean shift on recent model-specific samples; descriptive, not a drift guarantee"}}

    @app.get("/api/v1/health")
    def health(request: Request, principal=Depends(authorize("admin", "analyst"))):
        state = request.app.state
        with state.engine.connect() as conn:
            pending = conn.execute(select(func.count()).select_from(outbox).where(outbox.c.tenant == principal.tenant, outbox.c.published_at.is_(None))).scalar_one()
            done = conn.execute(select(func.count()).select_from(consumed).where(consumed.c.tenant == principal.tenant)).scalar_one()
            oldest = conn.execute(select(func.min(outbox.c.created_at)).where(
                outbox.c.tenant == principal.tenant, outbox.c.published_at.is_(None))).scalar_one()
        cache = "disabled"
        if state.redis:
            try:
                cache = "ok" if state.redis.ping() else "unavailable"
            except redis.RedisError:
                cache = "unavailable"
        return {"database": "ok", "redis": cache, "model": "ok", "outbox_pending": pending,
                "oldest_pending_seconds": max(0, round((datetime.now(timezone.utc) - utc(oldest)).total_seconds())) if oldest else 0,
                "events_consumed": done, "streaming": "configured" if settings.broker else "disabled (local mode)"}

    @app.get("/metrics")
    def metrics(request: Request, principal=Depends(authorize("metrics"))):
        from prometheus_client.core import GaugeMetricFamily

        # Each scrape queries current state; no stale per-process backlog gauge.
        registry = CollectorRegistry()
        class Backlog:
            def collect(self):
                with request.app.state.engine.connect() as conn:
                    pending, oldest = conn.execute(select(func.count(), func.min(outbox.c.created_at))
                        .where(outbox.c.published_at.is_(None))).one()
                count = GaugeMetricFamily("sentinel_outbox_pending", "Unpublished events across tenants")
                count.add_metric([], pending)
                age = GaugeMetricFamily("sentinel_outbox_oldest_seconds", "Age of oldest unpublished event")
                age.add_metric([], max(0, (datetime.now(timezone.utc) - utc(oldest)).total_seconds()) if oldest else 0)
                yield count
                yield age
        registry.register(Backlog())
        return Response(generate_latest(request.app.state.registry) + generate_latest(registry),
                        media_type="text/plain; version=0.0.4")

    @app.get("/")
    def index():
        return FileResponse(STATIC / "index.html")
    app.mount("/static", StaticFiles(directory=STATIC, check_dir=False), name="static")
    return app


app = create_app()
