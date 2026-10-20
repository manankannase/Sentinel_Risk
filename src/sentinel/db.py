"""Additive, versioned migrations. Existing USD records remain unchanged."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    create_engine,
    event,
    select,
    text,
)

metadata = MetaData()
SCHEMA_VERSION = 2
schema_versions = Table("schema_versions", metadata,
    Column("version", Integer, primary_key=True), Column("applied_at", String(40), nullable=False))
accounts = Table("accounts", metadata,
    Column("tenant", String(64), primary_key=True), Column("customer_id", String(64), primary_key=True),
    Column("last_event", String(40)))
transactions = Table("transactions", metadata,
    Column("tenant", String(64), primary_key=True), Column("transaction_id", String(36), primary_key=True),
    Column("customer_id", String(64), nullable=False), Column("occurred_at", String(40), nullable=False),
    Column("decision", String(10), nullable=False), Column("request_hash", String(64), nullable=False),
    Column("event", JSON, nullable=False), Column("response", JSON, nullable=False),
    Column("features", JSON, nullable=False), Column("created_at", String(40), nullable=False))
Index("ix_transactions_customer_time", transactions.c.tenant, transactions.c.customer_id, transactions.c.occurred_at)
Index("ix_transactions_decision_time", transactions.c.tenant, transactions.c.decision, transactions.c.created_at)
Index("ix_transactions_tenant_created", transactions.c.tenant, transactions.c.created_at, transactions.c.transaction_id)
feedback = Table("feedback", metadata,
    Column("tenant", String(64), primary_key=True), Column("transaction_id", String(36), primary_key=True),
    Column("label", Integer, nullable=False), Column("created_at", String(40), nullable=False),
    Column("actor", String(64), nullable=False))
feedback_events = Table("feedback_events", metadata,
    Column("id", String(36), primary_key=True), Column("tenant", String(64), nullable=False),
    Column("transaction_id", String(36), nullable=False), Column("label", Integer, nullable=False),
    Column("previous_label", Integer), Column("actor", String(64), nullable=False),
    Column("reason", String(500), nullable=False), Column("created_at", String(40), nullable=False),
    CheckConstraint("label IN (0,1)", name="ck_feedback_event_label"))
Index("ix_feedback_events_transaction", feedback_events.c.tenant, feedback_events.c.transaction_id,
      feedback_events.c.created_at)
outbox = Table("outbox", metadata,
    Column("id", String(36), primary_key=True), Column("tenant", String(64), nullable=False),
    Column("customer_id", String(64), nullable=False), Column("payload", JSON, nullable=False),
    Column("created_at", String(40), nullable=False), Column("published_at", String(40)))
Index("ix_outbox_pending", outbox.c.published_at, outbox.c.created_at, outbox.c.id)
consumed = Table("consumed_events", metadata,
    Column("id", String(36), primary_key=True), Column("tenant", String(64), nullable=False),
    Column("created_at", String(40), nullable=False))
quarantine = Table("quarantine", metadata,
    Column("id", String(100), primary_key=True), Column("digest", String(64), nullable=False),
    Column("reason", String(80), nullable=False), Column("created_at", String(40), nullable=False))


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def engine_for(url):
    kwargs = {"pool_pre_ping": True}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False, "timeout": 10}
    else:
        kwargs["connect_args"] = {"connect_timeout": 5, "options": "-c statement_timeout=8000 -c lock_timeout=5000"}
        kwargs["pool_size"], kwargs["max_overflow"] = 10, 10
    engine = create_engine(url, **kwargs)
    if engine.dialect.name == "sqlite":
        @event.listens_for(engine, "connect")
        def configure(connection, _):
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
    return engine


def insert_ignore(conn, table, values):
    if conn.dialect.name == "postgresql":
        from sqlalchemy.dialects.postgresql import insert
    else:
        from sqlalchemy.dialects.sqlite import insert
    return conn.execute(insert(table).values(**values).on_conflict_do_nothing())


@contextmanager
def write_transaction(engine):
    with engine.connect() as conn:
        try:
            if engine.dialect.name == "sqlite":
                conn.exec_driver_sql("BEGIN IMMEDIATE")
            else:
                conn.begin()
            yield conn
            conn.commit()
        except BaseException:
            conn.rollback()
            raise


def initialize(engine):
    with write_transaction(engine) as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(182391)"))
        metadata.create_all(conn)
        latest = conn.execute(select(schema_versions.c.version).order_by(schema_versions.c.version.desc()).limit(1)).scalar()
        if latest is not None and latest > SCHEMA_VERSION:
            raise RuntimeError("Refusing to migrate a newer schema with an older application")
        # create_all doesn't add new indexes to existing tables.
        for table in metadata.tables.values():
            for index in table.indexes:
                index.create(conn, checkfirst=True)
        insert_ignore(conn, schema_versions, {"version": SCHEMA_VERSION, "applied_at": now_iso()})


def verify_schema(conn):
    if conn.execute(select(schema_versions.c.version).order_by(schema_versions.c.version.desc()).limit(1)).scalar() != SCHEMA_VERSION:
        raise RuntimeError("Unsupported database schema; run the matching migration")


def lock_customer(conn, tenant, customer):
    insert_ignore(conn, accounts, {"tenant": tenant, "customer_id": customer})
    stmt = select(accounts).where(accounts.c.tenant == tenant, accounts.c.customer_id == customer)
    if conn.dialect.name == "postgresql":
        stmt = stmt.with_for_update()
    return conn.execute(stmt).mappings().one()


if __name__ == "__main__":
    from pathlib import Path

    from sentinel.config import Settings
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["migrate"])
    parser.parse_args()
    settings = Settings()
    if settings.database_url == "sqlite:///runtime/sentinel.db":
        Path("runtime").mkdir(exist_ok=True)
    initialize(engine_for(settings.database_url))
    print(f"Schema v{SCHEMA_VERSION} ready (existing data preserved).")
