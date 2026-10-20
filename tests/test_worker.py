import json
from uuid import uuid4

import pytest
from sqlalchemy import func, select

from sentinel.db import consumed, engine_for, initialize, now_iso, outbox, quarantine
from sentinel.worker import consume_value, publish_once


@pytest.fixture
def engine(tmp_path):
    engine = engine_for(f'sqlite:///{tmp_path}/worker.db')
    initialize(engine)
    yield engine
    engine.dispose()


def pending(engine):
    event = {'event_id': str(uuid4()), 'schema_version': 2, 'tenant': 'test', 'score': 0.2}
    with engine.begin() as conn:
        conn.execute(outbox.insert().values(id=event['event_id'], tenant='test', customer_id='c',
                                          payload=event, created_at=now_iso()))
    return event


def test_duplicate_delivery_and_tampered_events(engine):
    event = pending(engine)
    for offset in ['t:0:1', 't:0:2']:
        assert consume_value(engine, json.dumps(event).encode(), offset)
    assert not consume_value(engine, json.dumps({**event,'score': 0.9}).encode(), 't:0:3')
    assert not consume_value(engine, b'{'*2000, 't:0:4')
    with engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(consumed)).scalar_one() == 1
        assert conn.execute(select(func.count()).select_from(quarantine)).scalar_one() == 2


def test_failed_delivery_keeps_outbox_pending(engine):
    pending(engine)
    class Producer:
        def produce(self, *args, **kwargs):
            pass
        def flush(self, *args):
            return 1
    with pytest.raises(RuntimeError, match='retained'):
        publish_once(engine, Producer(), 'topic')
    with engine.connect() as conn:
        assert conn.execute(select(outbox.c.published_at)).scalar_one() is None


def test_successful_delivery_marks_published(engine):
    pending(engine)
    class Producer:
        def produce(self, *args, **kwargs):
            kwargs['on_delivery'](None, None)
        def flush(self, *args):
            return 0
    assert publish_once(engine, Producer(), 'topic') == 1
    assert publish_once(engine, Producer(), 'topic') == 0
