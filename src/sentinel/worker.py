"""Transactional outbox publisher and idempotent audit consumer (at-least-once)."""
import hashlib
import json
import logging
import signal
import time

from confluent_kafka import Consumer, KafkaError, Producer
from confluent_kafka.admin import AdminClient, NewTopic
from sqlalchemy import select, text, update

from sentinel.config import Settings
from sentinel.db import consumed, engine_for, insert_ignore, now_iso, outbox, quarantine, write_transaction

log = logging.getLogger("sentinel.worker")


def kafka_config(settings):
    if not settings.broker:
        raise ValueError("Broker required")
    if settings.broker_protocol not in {"PLAINTEXT", "SASL_PLAINTEXT", "SASL_SSL"}:
        raise ValueError("Unsupported Kafka transport")
    if settings.env == "production" and settings.broker_protocol != "SASL_SSL":
        raise ValueError("Production requires Kafka SASL_SSL")
    cfg = {"bootstrap.servers": settings.broker, "security.protocol": settings.broker_protocol, "socket.timeout.ms": 10000}
    if settings.broker_protocol.startswith("SASL_"):
        if not settings.broker_password:
            raise ValueError("SASL password required")
        cfg.update({"sasl.mechanism": "PLAIN", "sasl.username": settings.broker_username, "sasl.password": settings.broker_password})
    if settings.broker_ca_file:
        cfg["ssl.ca.location"] = settings.broker_ca_file
    return cfg


def publish_once(engine, producer, topic):
    with write_transaction(engine) as conn:
        if conn.dialect.name == "postgresql":
            if not conn.execute(text("SELECT pg_try_advisory_xact_lock(182392)")).scalar_one():
                return 0
        rows = conn.execute(select(outbox).where(outbox.c.published_at.is_(None)).order_by(outbox.c.created_at, outbox.c.id).limit(50)).mappings().all()
        errors = []
        for row in rows:
            def delivered(error, message):
                if error:
                    errors.append(error.code())
            producer.produce(topic, key=f"{row['tenant']}:{row['customer_id']}",
                             value=json.dumps(row["payload"], sort_keys=True).encode(), on_delivery=delivered)
        if producer.flush(8) or errors:
            raise RuntimeError("Broker delivery failed; outbox retained for retry")
        if rows:
            conn.execute(update(outbox).where(outbox.c.id.in_([r["id"] for r in rows])).values(published_at=now_iso()))
        return len(rows)


def consume_value(engine, raw: bytes, offset_id: str):
    """Only accept immutable messages matching the authoritative outbox; quarantine poison."""
    reason, event = None, None
    try:
        if not raw or len(raw) > 8192:
            raise ValueError("size")
        event = json.loads(raw)
        if not isinstance(event, dict) or event.get("schema_version") != 1 or not isinstance(event.get("event_id"), str):
            raise ValueError("schema")
        if len(event["event_id"]) != 36:
            raise ValueError("id")
    except (ValueError, TypeError, UnicodeDecodeError):
        reason = "invalid message"
    with write_transaction(engine) as conn:
        if not reason:
            original = conn.execute(select(outbox.c.payload).where(outbox.c.id == event["event_id"])).scalar_one_or_none()
            if original != event:
                reason = "unknown or altered event"
        if reason:
            insert_ignore(conn, quarantine, {"id": offset_id, "digest": hashlib.sha256(raw or b"").hexdigest(),
                                             "reason": reason, "created_at": now_iso()})
            return False
        insert_ignore(conn, consumed, {"id": event["event_id"], "tenant": event["tenant"], "created_at": now_iso()})
        return True


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    settings = Settings()
    cfg = kafka_config(settings)
    engine = engine_for(settings.database_url)
    admin = AdminClient(cfg)
    for result in admin.create_topics([NewTopic(settings.topic, num_partitions=3, replication_factor=1)], request_timeout=15).values():
        try:
            result.result()
        except Exception as exc:
            if not exc.args or not hasattr(exc.args[0], "code") or exc.args[0].code() != KafkaError.TOPIC_ALREADY_EXISTS:
                raise
    producer = Producer({**cfg, "enable.idempotence": True, "acks": "all", "delivery.timeout.ms": 7000, "request.timeout.ms": 4000})
    consumer = Consumer({**cfg, "group.id": "sentinel-audit-v1", "auto.offset.reset": "earliest",
                         "enable.auto.commit": False, "enable.auto.offset.store": False})
    consumer.subscribe([settings.topic])
    stopping = False
    def stop(*_):
        nonlocal stopping
        stopping = True
    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    backoff = 0.5
    try:
        while not stopping:
            try:
                publish_once(engine, producer, settings.topic)
                for _ in range(60):
                    message = consumer.poll(0.1)
                    if message is None:
                        break
                    if message.error():
                        raise RuntimeError("Kafka consumer error")
                    consume_value(engine, message.value(), f"{message.topic()}:{message.partition()}:{message.offset()}")
                    consumer.commit(message=message, asynchronous=False)
                backoff = 0.5
                time.sleep(0.25)
            except Exception as exc:
                log.warning("Event cycle failed (%s); retry in %.1fs", type(exc).__name__, backoff)
                time.sleep(backoff)
                backoff = min(backoff * 2, 15)
    finally:
        consumer.close()
        producer.flush(5)
        engine.dispose()


if __name__ == "__main__":
    main()
