"""Database schema v1. Explicit initialization; never reset user data."""
import argparse
from contextlib import contextmanager
from datetime import datetime, timezone

from sqlalchemy import (
    JSON,
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
feedback = Table("feedback", metadata,
    Column("tenant", String(64), primary_key=True), Column("transaction_id", String(36), primary_key=True),
    Column("label", Integer, nullable=False), Column("created_at", String(40), nullable=False),
    Column("actor", String(64), nullable=False))
outbox = Table("outbox", metadata,
    Column("id", String(36), primary_key=True), Column("tenant", String(64), nullable=False),
    Column("customer_id", String(64), nullable=False), Column("payload", JSON, nullable=False),
    Column("created_at", String(40), nullable=False), Column("published_at", String(40)))
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
    with engine.begin() as conn:
        if engine.dialect.name == "postgresql":
            conn.execute(text("SELECT pg_advisory_xact_lock(182391)"))
        metadata.create_all(conn)


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
    print("Schema v1 ready (existing data preserved).")
