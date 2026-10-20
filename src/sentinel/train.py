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

from sentinel.features import FEATURE_NAMES, FEATURE_VERSION, build_features, utc
from sentinel.monitoring import reference_histogram
from sentinel.schemas import TransactionInput


def synthetic_rows(n=24000, seed=42, start=None, interval_seconds=120):
    rng = np.random.default_rng(seed)
    start = start or datetime(2025, 1, 1, tzinfo=timezone.utc)
    if interval_seconds <= 0:
        raise ValueError("Positive event interval required")
    history = defaultdict(list)
    for i in range(n):
        customer = f"customer_{int(rng.integers(0, 100)):03}"
        suspicious = rng.random() < 0.09
        t = start + timedelta(seconds=interval_seconds * i + int(rng.integers(0, max(1, int(interval_seconds / 2)))))
        tx = {
            "transaction_id": str(uuid5(NAMESPACE_DNS, f"sentinel-{seed}-{i}")),
            "customer_id": customer, "merchant_id": f"merchant_{int(rng.integers(0, 80)):03}",
            "device_id": f"device_{customer}" if not suspicious else f"device_alt_{int(rng.integers(0, 30))}",
            "amount_minor": max(1, min(100_000_000, int(rng.lognormal(12.2 if suspicious else 11.5, 1.0)))),
            "currency": "INR", "country": str(rng.choice(["IN", "GB", "US"])) if suspicious else "IN",
            "channel": str(rng.choice(["ecommerce", "pos", "atm", "upi", "imps", "neft", "rtgs"],
                                      p=[0.20, 0.15, 0.05, 0.40, 0.10, 0.07, 0.03])),
            "occurred_at": t.isoformat(),
        }
        f = build_features(tx, history[customer])
        # Nonlinear synthetic mechanism. Interactions make the exercise useful for
        # tree models without pretending that synthetic behavior is real fraud.
        night = (t + timedelta(hours=5, minutes=30)).hour < 5
        logit = -6.2
        logit += 4.4 * (f["new_device"] and f["amount_ratio"] > 2.5)
        logit += 3.6 * (f["new_country"] and f["is_ecommerce"])
        logit += 2.8 * (f["is_atm"] and night)
        logit += 2.4 * (f["amount_ratio"] > 8)
        logit += 2.6 * (f["count_1h"] >= 2)
        logit += 0.35 * max(f["log_amount"] - 8, 0)
        logit += 1.8 * (f["new_payee"] and f["is_upi"] and f["amount_ratio"] > 3)
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
        if ready < t or type(row["label"]) is not int or row["label"] not in (0, 1):
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
            "cost_units": int(fp + 50 * fn), "threshold": float(threshold),
            "alert_rate": float(np.mean(p >= threshold)),
            "precision_at_100": float(y[np.argsort(-p, kind="stable")[:100]].mean()),
            "reliability": [{"lower": lo / 10, "count": int(mask.sum()),
                             "mean_score": float(p[mask].mean()), "fraud_rate": float(y[mask].mean())}
                            for lo in range(10)
                            if (mask := ((p >= lo / 10) & (p < (lo + 1) / 10 if lo < 9 else p <= 1))).any()]}


def calibrate(p, slope, intercept):
    p = np.clip(np.asarray(p, dtype=float), 1e-7, 1 - 1e-7)
    logits = np.clip(slope * np.log(p / (1 - p)) + intercept, -40, 40)
    return 1 / (1 + np.exp(-logits))


def train(output: Path, rows: list, seed=42, source="synthetic", mlflow_uri=""):
    if output.exists() and any(output.iterdir()):
        raise ValueError("Output directory must be new or empty; model versions are immutable")
    if len(rows) < 2000:
        raise ValueError("At least 2000 rows required")
    x, y, times, available = prepare(rows)
    c0, c1, c2 = [times[int(len(times) * f)] for f in (0.5, 0.6, 0.8)]
    # Labels must have arrived by the cutoff at which training/threshold selection happen.
    masks = [(times < c0) & (available < c0), (times >= c0) & (times < c1) & (available < c1),
             (times >= c1) & (times < c2) & (available < c2), times >= c2]
    for mask in masks:
        if mask.sum() < 100 or len(np.unique(y[mask])) != 2:
            raise ValueError("Every chronological split needs >=100 rows and both classes")
    tr, ca, va, te = masks
    imbalance = float(np.sqrt((y[tr] == 0).sum() / max((y[tr] == 1).sum(), 1)))
    model = xgb.XGBClassifier(n_estimators=220, max_depth=4, learning_rate=0.04,
                             min_child_weight=4, subsample=0.9, colsample_bytree=0.9,
                             scale_pos_weight=imbalance, n_jobs=2, random_state=seed,
                             tree_method="hist", eval_metric="logloss")
    model.fit(x[tr], y[tr])
    calibration_p = np.clip(model.predict_proba(x[ca])[:, 1].astype(float), 1e-7, 1 - 1e-7)
    calibrator = LogisticRegression(C=1e6, random_state=seed).fit(
        np.log(calibration_p / (1 - calibration_p)).reshape(-1, 1), y[ca])
    slope, intercept = float(calibrator.coef_[0, 0]), float(calibrator.intercept_[0])
    if slope <= 0:
        raise ValueError("Calibration reverses ranking; candidate rejected")
    validation_p = calibrate(model.predict_proba(x[va])[:, 1], slope, intercept)
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
    p = calibrate(model.predict_proba(x[te])[:, 1], slope, intercept)
    precision, recall, _ = precision_recall_curve(y[te], p)
    idx = np.linspace(0, len(precision) - 1, min(80, len(precision)), dtype=int)
    output.mkdir(parents=True, exist_ok=True)
    path = output / "model.json"
    model.save_model(path)
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest = {"model_version": f"xgb-{digest[:12]}", "model_sha256": digest, "feature_names": FEATURE_NAMES,
                "review_threshold": float(threshold), "decline_threshold": decline, "data_source": source,
                "trained_at": datetime.now(timezone.utc).isoformat(), "seed": seed,
                "currency": "INR", "history_window_hours": 24, "serialization": "xgboost-native-json",
                "feature_version": FEATURE_VERSION, "feature_timezone": "Asia/Kolkata",
                "intended_use": "simulation-or-shadow-only", "calibrated": True,
                "calibration": {"method": "sigmoid", "slope": slope, "intercept": intercept},
                "dataset_sha256": hashlib.sha256(json.dumps(rows, sort_keys=True, default=str).encode()).hexdigest()}
    metrics = {"data_source": source, "rows": len(rows), "fraud_rate": float(y.mean()),
               "split_counts": dict(zip(["train", "calibration", "validation", "test"], [int(m.sum()) for m in masks])),
               "purged_delayed_labels": int(len(y) - sum(m.sum() for m in masks)),
               "cutoffs": [c0.isoformat(), c1.isoformat(), c2.isoformat()], "validation": candidate_validation,
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
    # Freeze bins on training only; later distributions never redefine the baseline.
    metrics["histograms"] = {}
    for name in FEATURE_NAMES:
        metrics["histograms"][name] = reference_histogram(x.loc[tr, name])
    ordered = sorted(rows, key=lambda row: utc(row["occurred_at"]))
    amounts = np.array([row["amount_minor"] for row in ordered])[te]
    metrics["test"]["fraud_value_recall"] = float(
        amounts[(y[te] == 1) & (p >= threshold)].sum() / max(amounts[y[te] == 1].sum(), 1))
    channels = np.array([row["channel"] for row in ordered])[te]
    metrics["test_by_channel"] = {
        channel: {"rows": int((mask := channels == channel).sum()),
                  "fraud_count": int(y[te][mask].sum()),
                  "metrics": evaluate(y[te][mask], p[mask], threshold) if len(np.unique(y[te][mask])) == 2 else None}
        for channel in sorted(set(channels))}
    (output / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n")
    manifest["metrics_sha256"] = hashlib.sha256((output / "metrics.json").read_bytes()).hexdigest()
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
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
    p.add_argument("--output", type=Path, default=Path("artifacts/inr-v2"))
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
