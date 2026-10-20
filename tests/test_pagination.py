import base64
import json

import pytest
from test_api import transaction


def test_cursor_stable_when_new_transactions_arrive(client):
    client, headers = client
    for i in range(5):
        assert client.post('/api/v1/score', headers=headers, json=transaction(customer=f'customer_{i}')).status_code == 200
    first=client.get('/api/v1/transactions?limit=2',headers=headers).json()
    cursor=first['next_cursor']
    client.post('/api/v1/score',headers=headers,json=transaction(customer='new_customer'))
    second=client.get('/api/v1/transactions',headers=headers,params={'limit':2,'cursor':cursor}).json()
    third=client.get('/api/v1/transactions',headers=headers,params={'limit':2,'cursor':second['next_cursor']}).json()
    ids=[r['transaction_id'] for page in (first,second,third) for r in page['items']]
    assert len(ids)==len(set(ids))==5
    assert third['next_cursor'] is None


@pytest.mark.parametrize('value',[[],{}, {'at':1,'id':'x'},{'at':None,'id':'00000000-0000-0000-0000-000000000000'}])
def test_invalid_cursor_is_client_error(client,value):
    client,headers=client
    cursor=base64.urlsafe_b64encode(json.dumps(value).encode()).decode()
    assert client.get('/api/v1/transactions',headers=headers,params={'cursor':cursor}).status_code==422
