import asyncio
import hashlib
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone

import pytest
from fastapi import HTTPException
from sqlalchemy import func, select
from test_api import transaction

from sentinel.config import Settings
from sentinel.db import feedback_events, outbox, transactions
from sentinel.features import build_features
from sentinel.ml import RiskModel
from sentinel.security import KeyStore, RateLimiter, RequestBoundary, strict_json


@pytest.mark.parametrize('field,value', [('amount_minor', True), ('amount_minor', 1.5), ('amount_minor', 0),
    ('amount_minor', -1), ('amount_minor', 100000001), ('currency', 'USD'), ('occurred_at', 12345), ('occurred_at', '12345'),
    ('occurred_at', '2026-10-20T12:00:00'), ('customer_id', '../escape'), ('channel', 'unknown')])
def test_strict_input(client, field, value):
    client, headers = client
    assert client.post('/api/v1/score', headers=headers, json={**transaction(), field: value}).status_code == 422


@pytest.mark.parametrize('body', [b'{"x":1,"x":2}', b'{"x":NaN}', b'{"x":Infinity}', b'{"x":1e999}', b'{'*2000])
def test_ambiguous_json_rejected(client, body):
    client, headers = client
    response = client.post('/api/v1/score', content=body, headers={**headers, 'Content-Type': 'application/json'})
    assert response.status_code == 400
    assert response.headers['x-content-type-options'] == 'nosniff'


def test_body_deadline():
    async def run():
        messages = []
        async def app(*args):
            raise AssertionError('Timed out request reached application')
        async def receive():
            await asyncio.sleep(1)
        async def send(message):
            messages.append(message)
        boundary = RequestBoundary(app, body_timeout_seconds=0.01)
        await boundary({'type': 'http', 'method': 'POST', 'query_string': b'',
                        'headers': [(b'content-type', b'application/json')]}, receive, send)
        assert messages[0]['status'] == 408
    asyncio.run(run())


def test_unhandled_errors_get_security_headers(client, monkeypatch):
    client, headers = client
    def fail(*args, **kwargs):
        raise RuntimeError('secret database connection string')
    monkeypatch.setattr(client.app.state.engine, 'connect', fail)
    response = client.get('/api/v1/overview', headers=headers)
    assert response.status_code == 500
    assert 'secret' not in response.text
    assert response.headers['x-content-type-options'] == 'nosniff'


def test_duplicate_concurrency_creates_one_ledger_row(client):
    client, headers = client
    payload = transaction()
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: client.post('/api/v1/score', headers=headers, json=payload), range(8)))
    assert all(r.status_code == 200 for r in results)
    assert all(r.json() == results[0].json() for r in results)
    with client.app.state.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(transactions)).scalar_one() == 1


def test_same_id_different_customer_conflicts(client):
    client, headers = client
    payload = transaction()
    assert client.post('/api/v1/score', headers=headers, json=payload).status_code == 200
    payload['customer_id'] = 'different'
    assert client.post('/api/v1/score', headers=headers, json=payload).status_code == 409


def test_model_failure_is_durable_review(client, monkeypatch):
    client, headers = client
    def fail(_):
        raise ValueError('model unavailable')
    monkeypatch.setattr(client.app.state.model, 'predict', fail)
    payload = transaction()
    response = client.post('/api/v1/score', headers=headers, json=payload)
    assert response.status_code == 200
    assert response.json()['decision'] == 'REVIEW'
    assert response.json()['score'] is None
    assert client.post('/api/v1/score', headers=headers, json=payload).json() == response.json()


def test_cold_start_shadow_and_history_limit(client, monkeypatch):
    client, headers = client
    state = client.app.state
    monkeypatch.setattr(state.model, 'predict', lambda _: 0.0)
    start = datetime.now(timezone.utc) - timedelta(minutes=10)
    first = client.post('/api/v1/score', headers=headers, json=transaction(occurred_at=start)).json()
    assert first['decision'] == 'REVIEW' and first['features']['cold_start'] == 1
    state.settings.decision_mode = 'shadow'
    second = client.post('/api/v1/score', headers=headers, json=transaction(occurred_at=start+timedelta(minutes=1))).json()
    assert second['decision'] == 'REVIEW' and second['model_decision'] == 'APPROVE'
    state.settings.max_customer_history = 1
    third = client.post('/api/v1/score', headers=headers, json=transaction(occurred_at=start+timedelta(minutes=2))).json()
    assert third['decision'] == 'REVIEW' and third['score'] is None
    assert third['feature_status'] == 'truncated'


def test_late_history_excludes_future_and_preserves_cursor(client):
    client, headers = client
    start = datetime.now(timezone.utc) - timedelta(minutes=10)
    for delta in (0, 5, 2, 6):
        response = client.post('/api/v1/score', headers=headers, json=transaction(occurred_at=start+timedelta(minutes=delta)))
        assert response.status_code == 200
        counts = {0: 0, 5: 1, 2: 1, 6: 3}
        assert response.json()['features']['count_24h'] == counts[delta]
        if delta == 2:
            assert response.json()['decision'] == 'REVIEW'


def test_legacy_usd_never_becomes_inr_history_or_volume(client):
    client, headers = client
    old = transaction(occurred_at=datetime.now(timezone.utc) - timedelta(minutes=10))
    old['currency'] = 'USD'
    with client.app.state.engine.begin() as conn:
        conn.execute(transactions.insert().values(tenant='test', transaction_id=old['transaction_id'],
            customer_id=old['customer_id'], occurred_at=old['occurred_at'], decision='APPROVE', request_hash='0'*64,
            event=old, response={**old, 'score': .1, 'model_version': 'legacy'}, features={}, created_at=old['occurred_at']))
    response = client.post('/api/v1/score', headers=headers, json=transaction()).json()
    assert response['features']['count_24h'] == 0
    overview = client.get('/api/v1/overview', headers=headers).json()
    assert overview['total'] == 1 and overview['volume_minor'] == 4750
    assert any(r['currency'] == 'USD' for r in client.get('/api/v1/transactions', headers=headers).json()['items'])


def test_corrections_preserve_history_and_require_admin(client):
    client, headers = client
    payload = transaction()
    client.post('/api/v1/score', headers=headers, json=payload)
    url = f"/api/v1/transactions/{payload['transaction_id']}/feedback"
    assert client.post(url, headers=headers, json={'label': 1}).status_code == 200
    correction = {'expected_label': 1, 'label': 0, 'reason': 'Customer verified transaction through support'}
    assert client.post(url+'/corrections', headers={'Authorization': 'Bearer test-analyst-key'}, json=correction).status_code == 403
    assert client.post(url+'/corrections', headers=headers, json=correction).status_code == 200
    assert client.post(url+'/corrections', headers=headers, json=correction).status_code == 409
    history = client.get(url+'/history', headers=headers).json()['items']
    assert [row['label'] for row in history] == [1, 0]
    assert history[1]['previous_label'] == 1
    assert client.get(url+'/history', headers={'Authorization': 'Bearer test-other-key'}).json()['items'] == []
    with client.app.state.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(feedback_events)).scalar_one() == 2


def test_no_broker_no_unbounded_pending_events(client):
    client, headers = client
    client.post('/api/v1/score', headers=headers, json=transaction())
    with client.app.state.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(outbox)).scalar_one() == 0


def test_credential_revocation_expiry_and_invalid_reload(tmp_path):
    path = tmp_path / 'keys.json'
    record = {'name': 'key1', 'tenant': 'tenant1', 'role': 'admin',
              'sha256': hashlib.sha256(b'token').hexdigest(),
              'expires_at': (datetime.now(timezone.utc)+timedelta(minutes=10)).isoformat()}
    path.write_text(json.dumps([record]))
    keys = KeyStore(path, require_expiry=True)
    assert keys.authenticate('token')
    replacement = tmp_path / 'replacement'
    replacement.write_text(json.dumps([{**record, 'revoked': True}]))
    replacement.replace(path)
    assert keys.authenticate('token') is None
    path.write_text(json.dumps([{**record, 'expires_at': '2020-01-01T00:00:00Z'}]))
    assert keys.authenticate('token') is None
    path.write_text('invalid')
    with pytest.raises(HTTPException) as error:
        keys.authenticate('token')
    assert error.value.status_code == 503


def test_redis_outage_fails_closed():
    class BrokenRedis:
        def eval(self, *args):
            raise ConnectionError('down')
    with pytest.raises(HTTPException) as error:
        RateLimiter(BrokenRedis()).allowed('key', 10)
    assert error.value.status_code == 503


def test_threshold_tampering_requires_manifest_pin(artifacts, tmp_path):
    import shutil
    original = (artifacts/'manifest.json').read_bytes()
    target = tmp_path/'model'
    shutil.copytree(artifacts, target)
    manifest = json.loads(original)
    manifest['review_threshold'] /= 2
    (target/'manifest.json').write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match='Manifest integrity'):
        RiskModel(target, manifest_sha256=hashlib.sha256(original).hexdigest())


def test_feature_windows_currency_and_ist():
    start = datetime(2026, 1, 1, 18, 30, tzinfo=timezone.utc)  # midnight IST
    current = transaction(occurred_at=start)
    history = [transaction(occurred_at=start-timedelta(minutes=5)),
               transaction(occurred_at=start-timedelta(hours=24)),
               {**transaction(occurred_at=start-timedelta(minutes=1)), 'currency': 'USD'}]
    features = build_features(current, history)
    assert features['count_24h'] == 2
    assert features['count_5m'] == 1
    assert features['hour_sin'] == pytest.approx(0)
    assert features['hour_cos'] == pytest.approx(1)


def test_production_configuration_rejects_unsafe_defaults():
    with pytest.raises(ValueError):
        Settings(env='production')
    settings = dict(env='production', database_url='postgresql+psycopg://u:p@db/risk?sslmode=verify-full',
                    redis_url='rediss://u:p@redis:6379', model_sha256='a'*64, manifest_sha256='b'*64,
                    decision_mode='shadow', allowed_hosts='risk.example.com')
    assert Settings(**settings).decision_mode == 'shadow'
    for key, value in [('database_url','postgresql+psycopg://u:p@db/risk'), ('redis_url','redis://host'),
                       ('decision_mode','simulation'), ('manifest_sha256',''), ('allowed_hosts','*')]:
        with pytest.raises(ValueError):
            Settings(**{**settings,key:value})


def test_strict_json_accepts_valid_nested_payload():
    assert strict_json(b'{"event":{"amount":1.25}}') == {'event': {'amount': 1.25}}
