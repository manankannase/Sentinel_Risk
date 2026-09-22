"""Replay synthetic traffic over HTTP; credentials read from a file, not command arguments."""
import argparse
import json
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

from sentinel.train import synthetic_rows

p = argparse.ArgumentParser()
p.add_argument("--url", default="http://127.0.0.1:8000")
p.add_argument("--token-file", type=Path, default=Path(".secrets/admin_token"))
p.add_argument("--count", type=int, default=80)
p.add_argument("--seed", type=int, default=7)
p.add_argument("--feedback", action="store_true")
args = p.parse_args()
if not 1 <= args.count <= 10000:
    p.error("count must be 1..10000")
token = args.token_file.read_text().strip()
start = datetime.now(timezone.utc) - timedelta(hours=12)
run = uuid4().hex[:8]
counts = {"APPROVE": 0, "REVIEW": 0, "DECLINE": 0}
def post(path, body):
    for _ in range(4):
        req = urllib.request.Request(args.url + path, data=json.dumps(body).encode(),
              headers={"Content-Type": "application/json", "Authorization": "Bearer " + token})
        try:
            with urllib.request.urlopen(req, timeout=20) as res:
                return json.load(res)
        except urllib.error.HTTPError as exc:
            if exc.code == 429:
                time.sleep(int(exc.headers.get("Retry-After", "60")))
            else:
                raise SystemExit(f"Replay rejected: HTTP {exc.code}: {exc.read().decode()}") from None
    raise SystemExit("Rate limit did not clear")
for i, tx in enumerate(synthetic_rows(args.count, args.seed)):
    label = tx.pop("label")
    tx.pop("label_available_at")
    tx["transaction_id"] = str(uuid4())
    tx["customer_id"] += "_" + run
    tx["occurred_at"] = (start + timedelta(seconds=i * 40000 / max(args.count, 1))).isoformat()
    result = post("/api/v1/score", tx)
    counts[result["decision"]] += 1
    if args.feedback and i % 5 == 0:
        post(f"/api/v1/transactions/{tx['transaction_id']}/feedback", {"label": label})
print(json.dumps({"replayed": args.count, "decisions": counts, "source": "synthetic"}, indent=2))
