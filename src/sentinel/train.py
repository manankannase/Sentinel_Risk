"""Reproducible synthetic experiment or explicitly supplied normalized CSV."""
import argparse
import hashlib
import json
import math
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import NAMESPACE_DNS, uuid5

import numpy as np
import pandas as pd
import xgboost as xgb
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import (
    average_precision_score,
    brier_score_loss,
    confusion_matrix,
    precision_recall_curve,
)
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import StandardScaler

from sentinel.features import FEATURE_NAMES, build_features, utc
from sentinel.schemas import TransactionInput


def synthetic_rows(n=24000, seed=42):
    rng = np.random.default_rng(seed)
    start = datetime(2025, 1, 1, tzinfo=timezone.utc)
    history = defaultdict(list)
    for i in range(n):
        customer = f"customer_{int(rng.integers(0, 100)):03}"
        suspicious = rng.random() < 0.09
        t = start + timedelta(seconds=120 * i + int(rng.integers(0, 60)))
        tx = {
            "transaction_id": str(uuid5(NAMESPACE_DNS, f"sentinel-{seed}-{i}")),
            "customer_id": customer, "merchant_id": f"merchant_{int(rng.integers(0, 80)):03}",
            "device_id": f"device_{customer}" if not suspicious else f"device_alt_{int(rng.integers(0, 30))}",
            "amount_minor": max(1, min(100_000_000, int(rng.lognormal(8.2 if suspicious else 7.5, 1.0)))),
            "currency": "USD", "country": str(rng.choice(["US", "GB", "CA"])) if suspicious else "US",
            "channel": str(rng.choice(["ecommerce", "pos", "atm"], p=[0.65, 0.3, 0.05])),
            "occurred_at": t.isoformat(),
        }
        f = build_features(tx, history[customer])
        # Nonlinear synthetic mechanism. Interactions make the exercise useful for
        # tree models without pretending that synthetic behavior is real fraud.
        night = t.hour < 5
        logit = -6.2
        logit += 4.4 * (f["new_device"] and f["amount_ratio"] > 2.5)
        logit += 3.6 * (f["new_country"] and f["is_ecommerce"])
        logit += 2.8 * (f["is_atm"] and night)
        logit += 2.4 * (f["amount_ratio"] > 8)
        logit += 2.6 * (f["count_1h"] >= 2)
        logit += 0.35 * max(f["log_amount"] - 4, 0)
        tx["label"] = int(rng.random() < 1 / (1 + math.exp(-logit)))
        # Two-hour simulated case-confirmation delay leaves adequate validation data
        # in compact local smoke tests while still preventing same-event label leakage.
        tx["label_available_at"] = (t + timedelta(hours=2)).isoformat()
        history[customer] = [h for h in history[customer] if utc(h["occurred_at"]) >= t - timedelta(hours=24)]
        history[customer].append(tx)
        yield tx


def prepare(rows):
    rows = sorted(rows, key=lambda row: utc(row["occurred_at"]))
    history, ids = defaultdict(list), set()
    features, labels, times, available = [], [], [], []
    for row in rows:
        tx = TransactionInput.model_validate({k: row[k] for k in TransactionInput.model_fields}).model_dump(mode="json")
        if tx["transaction_id"] in ids:
            raise ValueError("Duplicate transaction ID in dataset")
        ids.add(tx["transaction_id"])
        t, ready = utc(tx["occurred_at"]), utc(row["label_available_at"])
        if ready < t or row["label"] not in (0, 1):
            raise ValueError("Invalid label or label-availability timestamp")
        prior = history[tx["customer_id"]]
        if prior and utc(prior[-1]["occurred_at"]) >= t:
            raise ValueError("Per-customer timestamps must be strictly increasing")
        prior = [h for h in prior if utc(h["occurred_at"]) >= t - timedelta(hours=24)]
        features.append(build_features(tx, prior))
        labels.append(row["label"])
        times.append(t)
        available.append(ready)
        history[tx["customer_id"]] = prior + [tx]
    return pd.DataFrame(features)[FEATURE_NAMES], np.array(labels, dtype=int), np.array(times), np.array(available)


def evaluate(y, p, threshold):
    tn, fp, fn, tp = confusion_matrix(y, p >= threshold, labels=[0, 1]).ravel()
    return {"average_precision": float(average_precision_score(y, p)),
            "brier_score": float(brier_score_loss(y, p)), "recall": float(tp / max(tp + fn, 1)),
            "precision": float(tp / max(tp + fp, 1)), "fpr": float(fp / max(fp + tn, 1)),
            "confusion_matrix": {"tn": int(tn), "fp": int(fp), "fn": int(fn), "tp": int(tp)},
            "cost_units": int(fp + 50 * fn), "threshold": float(threshold)}


def train(output: Path, rows: list, seed=42, source="synthetic", mlflow_uri=""):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be new or empty; model versions are immutable")
    if len(rows) < 2000:
        raise ValueError("At least 2000 rows required")
    x, y, times, available = prepare(rows)
    c1, c2 = times[int(len(times) * 0.6)], times[int(len(times) * 0.8)]
    # Labels must have arrived by the cutoff at which training/threshold selection happen.
    masks = [(times < c1) & (available < c1), (times >= c1) & (times < c2) & (available < c2), times >= c2]
    for mask in masks:
        if mask.sum() < 100 or len(np.unique(y[mask])) != 2:
            raise ValueError("Every chronological split needs >=100 rows and both classes")
    tr, va, te = masks
    imbalance = float(np.sqrt((y[tr] == 0).sum() / max((y[tr] == 1).sum(), 1)))
    model = xgb.XGBClassifier(n_estimators=220, max_depth=4, learning_rate=0.04,
                             min_child_weight=4, subsample=0.9, colsample_bytree=0.9,
                             scale_pos_weight=imbalance, n_jobs=2, random_state=seed,
                             tree_method="hist", eval_metric="logloss")
    model.fit(x[tr], y[tr])
    validation_p = model.predict_proba(x[va])[:, 1]
    candidates = np.linspace(0.005, 0.8, 160)
    threshold = min(candidates, key=lambda t: evaluate(y[va], validation_p, t)["cost_units"])
    # High confidence threshold constrained to <=1% FPR on validation only.
    high = [t for t in np.linspace(max(threshold + 0.05, 0.5), 0.999, 100)
            if evaluate(y[va], validation_p, t)["fpr"] <= 0.01]
    decline = float(high[0]) if high else 1.0
    baseline = make_pipeline(StandardScaler(), LogisticRegression(max_iter=1000, random_state=seed))
    baseline.fit(x[tr], y[tr])
    baseline_va = baseline.predict_proba(x[va])[:, 1]
    baseline_threshold = min(candidates, key=lambda t: evaluate(y[va], baseline_va, t)["cost_units"])
    candidate_validation = evaluate(y[va], validation_p, threshold)
    baseline_validation = evaluate(y[va], baseline_va, baseline_threshold)
    if (candidate_validation["average_precision"] < baseline_validation["average_precision"]
            or candidate_validation["cost_units"] > baseline_validation["cost_units"]):
        raise ValueError("XGBoost candidate failed validation promotion criteria against baseline")
    p = model.predict_proba(x[te])[:, 1]
    precision, recall, _ = precision_recall_curve(y[te], p)
    idx = np.linspace(0, len(precision) - 1, min(80, len(precision)), dtype=int)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "model.json"
    model.save_model(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"model_version": f"xgb-{digest[:12]}", "model_sha256": digest, "feature_names": FEATURE_NAMES,
                "review_threshold": float(threshold), "decline_threshold": decline, "data_source": source,
                "trained_at": datetime.now(timezone.utc).isoformat(), "seed": seed,
                "currency": "USD", "history_window_hours": 24, "serialization": "xgboost-native-json"}
    metrics = {"data_source": source, "rows": len(rows), "fraud_rate": float(y.mean()),
               "split_counts": dict(zip(["train", "validation", "test"], [int(m.sum()) for m in masks])),
               "purged_delayed_labels": int(len(y) - sum(m.sum() for m in masks)),
               "cutoffs": [c1.isoformat(), c2.isoformat()], "validation": candidate_validation,
               "test": evaluate(y[te], p, threshold),
               "baseline": evaluate(y[te], baseline.predict_proba(x[te])[:, 1], baseline_threshold),
               "baseline_validation": baseline_validation,
               "promotion": {"selected": "xgboost", "rule": "validation AP >= baseline and cost <= baseline"},
               "precision_recall_curve": [{"precision": float(precision[i]), "recall": float(recall[i])} for i in idx],
               "cost_assumptions": {"false_positive": 1, "false_negative": 50, "units": "illustrative; not dollars"},
               "feature_importance": dict(zip(FEATURE_NAMES, map(float, model.feature_importances_))),
               "reference": {name: {"mean": float(x.loc[tr, name].mean()), "std": float(x.loc[tr, name].std())}
                             for name in FEATURE_NAMES},
               "limitations": ["Synthetic metrics do not establish real-world accuracy.",
                               "Review/decline are simulated recommendations; no payment is blocked.",
                               "Single chronological holdout; no population generalization claim.",
                               "Reason codes are rules, not model attribution."]}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    frame = x.copy()
    frame["event_timestamp"] = pd.to_datetime(times, utc=True)
    frame["entity_id"] = [r["customer_id"] for r in sorted(rows, key=lambda row: utc(row["occurred_at"]))]
    frame.to_csv(output / "features.csv", index=False)
    if mlflow_uri:
        import mlflow
        mlflow.set_tracking_uri(mlflow_uri)
        mlflow.set_experiment("sentinel-risk")
        with mlflow.start_run():
            mlflow.log_params({"seed": seed, "rows": len(rows), "data_source": source, "model": "xgboost"})
            mlflow.log_metrics({k: v for k, v in metrics["test"].items() if isinstance(v, (int, float))})
            mlflow.log_artifact(str(path))
            mlflow.log_artifact(str(output / "manifest.json"))
    return metrics


def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--output", type=Path, default=Path("artifacts"))
    p.add_argument("--rows", type=int, default=24000)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--csv", type=Path)
    p.add_argument("--mlflow-uri", default="")
    a = p.parse_args()
    if not 2000 <= a.rows <= 1_000_000:
        p.error("rows must be between 2000 and 1000000")
    rows = pd.read_csv(a.csv).to_dict("records") if a.csv else list(synthetic_rows(a.rows, a.seed))
    metrics = train(a.output, rows, a.seed, "user-supplied CSV" if a.csv else "synthetic", a.mlflow_uri)
    print(json.dumps({k: metrics[k] for k in ["data_source", "split_counts", "test", "baseline"]}, indent=2))


if __name__ == "__main__":
    main()
