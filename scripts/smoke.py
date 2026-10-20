"""Exercise an isolated running demo backend without printing credentials."""
import argparse
import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--url', default='http://127.0.0.1:8000')
    parser.add_argument('--token-file', type=Path, required=True)
    args = parser.parse_args()
    url = urllib.parse.urlsplit(args.url)
    if url.scheme != 'https' and not (url.scheme == 'http' and url.hostname in {'localhost','127.0.0.1','::1'}):
        parser.error('HTTPS required except explicit loopback addresses')
    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    opener = urllib.request.build_opener(NoRedirect())
    token = args.token_file.read_text().strip()
    def call(path, payload=None):
        req = urllib.request.Request(args.url.rstrip('/')+path,
            data=json.dumps(payload).encode() if payload is not None else None,
            headers={'Authorization': 'Bearer '+token, 'Content-Type': 'application/json'})
        with opener.open(req, timeout=15) as response:
            assert response.headers['X-Content-Type-Options'] == 'nosniff'
            return json.load(response)
    payload = {'transaction_id':str(uuid4()), 'customer_id':'smoke_'+uuid4().hex[:12],
        'merchant_id':'payee_test', 'device_id':'device_test', 'amount_minor':12345, 'currency':'INR',
        'country':'IN', 'channel':'upi', 'occurred_at':datetime.now(timezone.utc).isoformat()}
    started=time.perf_counter()
    first=call('/api/v1/score',payload)
    elapsed=time.perf_counter()-started
    assert first==call('/api/v1/score',payload)
    assert first['decision']=='REVIEW' and first['currency']=='INR'
    late={**payload, 'transaction_id':str(uuid4()), 'occurred_at':(datetime.now(timezone.utc)-timedelta(minutes=1)).isoformat()}
    assert call('/api/v1/score',late)['decision']=='REVIEW'
    feedback=f"/api/v1/transactions/{payload['transaction_id']}/feedback"
    call(feedback,{'label':1})
    call(feedback+'/corrections',{'expected_label':1,'label':0,'reason':'Isolated smoke test correction'})
    assert [r['label'] for r in call(feedback+'/history')['items']]==[1,0]
    assert call('/api/v1/overview')['currency']=='INR'
    assert call('/api/v1/model')['manifest']['currency']=='INR'
    print(json.dumps({'status':'passed', 'first_request_ms':round(elapsed*1000,2),
        'checks':['INR','idempotency','late-event review','feedback correction','overview','model','security headers']}))


if __name__=='__main__':
    main()
