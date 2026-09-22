from datetime import datetime, timedelta, timezone

from sentinel.features import build_features


def event(transaction_id, at, customer="customer_one", device="device_one"):
    return {"transaction_id": transaction_id, "customer_id": customer, "merchant_id": "merchant_one",
            "device_id": device, "amount_minor": 1000, "currency": "USD", "country": "US",
            "channel": "ecommerce", "occurred_at": at.isoformat()}


def test_features_use_only_prior_same_customer_events():
    now = datetime(2026, 1, 2, tzinfo=timezone.utc)
    current = event("current", now, device="device_new")
    history = [
        event("prior", now - timedelta(minutes=10)),
        event("same-time", now),
        event("future", now + timedelta(minutes=1)),
        event("other-customer", now - timedelta(minutes=5), customer="other"),
        event("expired", now - timedelta(hours=25)),
    ]
    features = build_features(current, history)
    assert features["count_1h"] == 1
    assert features["count_24h"] == 1
    assert features["new_device"] == 1
