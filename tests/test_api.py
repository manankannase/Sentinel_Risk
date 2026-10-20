from datetime import datetime, timedelta, timezone
from uuid import uuid4


def transaction(customer="customer_one", occurred_at=None):
    return {
        "transaction_id": str(uuid4()), "customer_id": customer, "merchant_id": "merchant_one",
        "device_id": "device_one", "amount_minor": 4750, "currency": "INR", "country": "IN",
        "channel": "ecommerce", "occurred_at": (occurred_at or datetime.now(timezone.utc)).isoformat(),
    }


def test_score_is_idempotent_and_audited(client):
    client, headers = client
    payload = transaction()
    first = client.post("/api/v1/score", headers=headers, json=payload)
    second = client.post("/api/v1/score", headers=headers, json=payload)
    assert first.status_code == second.status_code == 200
    assert first.json() == second.json()
    overview = client.get("/api/v1/overview", headers=headers)
    assert overview.status_code == 200
    assert overview.json()["total"] == 1
    changed = dict(payload, amount_minor=9999)
    assert client.post("/api/v1/score", headers=headers, json=changed).status_code == 409


def test_rejects_future_out_of_order_and_unknown_fields(client):
    client, headers = client
    future = transaction(occurred_at=datetime.now(timezone.utc) + timedelta(minutes=3))
    assert client.post("/api/v1/score", headers=headers, json=future).status_code == 422
    late = transaction(occurred_at=datetime.now(timezone.utc) - timedelta(minutes=5))
    assert client.post("/api/v1/score", headers=headers, json=late).status_code == 200
    old = transaction(customer=late["customer_id"], occurred_at=datetime.now(timezone.utc) - timedelta(minutes=6))
    response = client.post("/api/v1/score", headers=headers, json=old)
    assert response.status_code == 200
    assert response.json()["decision"] == "REVIEW"
    assert any("Late" in reason for reason in response.json()["reasons"])
    bad = transaction()
    bad["unexpected"] = "x"
    assert client.post("/api/v1/score", headers=headers, json=bad).status_code == 422


def test_roles_feedback_and_secure_boundary(client):
    client, headers = client
    payload = transaction()
    result = client.post("/api/v1/score", headers=headers, json=payload)
    assert result.status_code == 200
    analyst = {"Authorization": "Bearer test-analyst-key"}
    assert client.post("/api/v1/score", headers=analyst, json=transaction()).status_code == 403
    endpoint = f"/api/v1/transactions/{payload['transaction_id']}/feedback"
    assert client.post(endpoint, headers=analyst, json={"label": 1}).status_code == 200
    assert client.post(endpoint, headers=analyst, json={"label": 0}).status_code == 409
    assert client.post("/api/v1/score", headers=headers, content=b"{}").status_code == 415
    response = client.get("/", headers=headers)
    assert response.headers["content-security-policy"].startswith("default-src 'none'")
    assert response.headers["x-content-type-options"] == "nosniff"


def test_tenant_isolation_and_body_limit(client):
    client, headers = client
    payload = transaction()
    assert client.post("/api/v1/score", headers=headers, json=payload).status_code == 200
    other = {"Authorization": "Bearer test-other-key"}
    assert client.get("/api/v1/transactions", headers=other).json()["items"] == []
    endpoint = f"/api/v1/transactions/{payload['transaction_id']}/feedback"
    assert client.post(endpoint, headers=other, json={"label": 1}).status_code == 404
    oversized = b'{"padding":"' + b"x" * 9000 + b'"}'
    assert client.post("/api/v1/score", content=oversized,
                       headers={**headers, "Content-Type": "application/json"}).status_code == 413
