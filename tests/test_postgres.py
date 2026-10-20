"""Real service tests. Creates and drops only randomly named sentinel_test_* databases."""
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import func, select
from sqlalchemy.engine import make_url
from sqlalchemy.exc import ProgrammingError
from test_api import transaction

from sentinel.api import create_app
from sentinel.db import engine_for, initialize, outbox, transactions
from sentinel.security import RateLimiter


@pytest.fixture
def postgres_client(app):
    url = os.environ.get('SENTINEL_TEST_POSTGRES')
    if not url:
        pytest.skip('Set SENTINEL_TEST_POSTGRES to a dedicated test PostgreSQL instance')
    admin = engine_for(url)
    name = 'sentinel_test_' + uuid4().hex
    with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
        conn.exec_driver_sql(f'CREATE DATABASE "{name}"')
    database = make_url(url).set(database=name).render_as_string(hide_password=False)
    engine = engine_for(database)
    initialize(engine)
    engine.dispose()
    old_app, key = app
    # create_app closure settings aren't exposed until startup; reuse fixture paths through lifespan.
    with TestClient(old_app):
        settings = old_app.state.settings.model_copy(update={'database_url': database, 'broker': 'test-broker'})
    application = create_app(settings)
    try:
        with TestClient(application) as client:
            yield client, {'Authorization': f'Bearer {key}'}
    finally:
        with admin.connect().execution_options(isolation_level='AUTOCOMMIT') as conn:
            conn.exec_driver_sql(f'DROP DATABASE "{name}" WITH (FORCE)')
        admin.dispose()


def test_postgres_duplicate_atomicity_and_json_analytics(postgres_client):
    client, headers = postgres_client
    payload = transaction()
    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: client.post('/api/v1/score', headers=headers, json=payload), range(12)))
    assert all(r.status_code == 200 for r in results)
    assert all(r.json() == results[0].json() for r in results)
    with client.app.state.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(transactions)).scalar_one() == 1
        assert conn.execute(select(func.count()).select_from(outbox)).scalar_one() == 1
    assert client.get('/api/v1/overview', headers=headers).json()['volume_minor'] == payload['amount_minor']


def test_postgres_conflicting_ids_different_customers(postgres_client):
    client, headers = postgres_client
    payload = transaction()
    with ThreadPoolExecutor(max_workers=2) as pool:
        responses = list(pool.map(lambda c: client.post('/api/v1/score', headers=headers,
            json={**payload, 'customer_id': c}), ['customer_a','customer_b']))
    assert sorted(r.status_code for r in responses) == [200,409]


def test_postgres_runtime_grants_preserve_ledger(postgres_client):
    client, headers = postgres_client
    engine = client.app.state.engine
    with engine.begin() as conn:
        conn.exec_driver_sql(Path('deploy/roles.sql').read_text())
    # SET ROLE exercises the same permissions as a non-owner runtime login.
    with engine.begin() as conn:
        conn.exec_driver_sql('SET LOCAL ROLE sentinel_api')
        conn.exec_driver_sql('SELECT * FROM transactions LIMIT 1')
        with pytest.raises(ProgrammingError):
            conn.exec_driver_sql("DELETE FROM transactions")
    with engine.begin() as conn:
        conn.exec_driver_sql('SET LOCAL ROLE sentinel_worker')
        conn.exec_driver_sql('SELECT * FROM outbox LIMIT 1')
        with pytest.raises(ProgrammingError):
            conn.exec_driver_sql('SELECT * FROM transactions LIMIT 1')
    # Complete scoring/feedback must work under the API role too.
    from sqlalchemy import event
    @event.listens_for(engine, 'checkout')
    def use_api_role(connection, *_):
        with connection.cursor() as cursor:
            cursor.execute('SET ROLE sentinel_api')
    try:
        payload = transaction()
        assert client.post('/api/v1/score', headers=headers, json=payload).status_code == 200
        assert client.post(f"/api/v1/transactions/{payload['transaction_id']}/feedback",
                           headers=headers, json={'label': 1}).status_code == 200
    finally:
        event.remove(engine, 'checkout', use_api_role)


def test_distributed_redis_rate_limit():
    url = os.environ.get('SENTINEL_TEST_REDIS')
    if not url:
        pytest.skip('Set SENTINEL_TEST_REDIS to dedicated test Redis')
    import redis
    client = redis.Redis.from_url(url)
    key = 'test:' + uuid4().hex
    limiters = [RateLimiter(client), RateLimiter(client)]
    with ThreadPoolExecutor(max_workers=10) as pool:
        accepted = list(pool.map(lambda i: limiters[i % 2].allowed(key, 10), range(40)))
    assert sum(accepted) == 10
    client.close()
