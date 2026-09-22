"""One feature implementation for training and serving. No labels enter this module."""
import math
from datetime import datetime, timedelta, timezone

FEATURE_NAMES = [
    "log_amount", "amount_ratio", "count_1h", "count_24h", "log_sum_24h",
    "new_device", "new_country", "hour_sin", "hour_cos", "is_ecommerce", "is_atm",
]


def utc(value):
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if value.tzinfo is None:
        raise ValueError("Naive timestamp")
    return value.astimezone(timezone.utc)


def build_features(transaction: dict, history: list[dict]) -> dict[str, float]:
    t = utc(transaction["occurred_at"])
    # Exclude future and same-time events even when a caller supplies mixed history.
    past = [h for h in history if t - timedelta(hours=24) <= utc(h["occurred_at"]) < t
            and h["customer_id"] == transaction["customer_id"]]
    hour = [h for h in past if utc(h["occurred_at"]) >= t - timedelta(hours=1)]
    amount = transaction["amount_minor"] / 100
    total = sum(h["amount_minor"] for h in past) / 100
    mean = total / len(past) if past else 50.0
    angle = 2 * math.pi * (t.hour + t.minute / 60) / 24
    values = [
        math.log1p(amount), min(amount / max(mean, 1), 1000), float(len(hour)),
        float(len(past)), math.log1p(total),
        float(bool(past) and transaction["device_id"] not in {h["device_id"] for h in past}),
        float(bool(past) and transaction["country"] not in {h["country"] for h in past}),
        math.sin(angle), math.cos(angle), float(transaction["channel"] == "ecommerce"),
        float(transaction["channel"] == "atm"),
    ]
    return dict(zip(FEATURE_NAMES, values, strict=True))
