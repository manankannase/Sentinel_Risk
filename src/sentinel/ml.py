import hashlib
import json
from pathlib import Path

import numpy as np
import xgboost as xgb

from sentinel.features import FEATURE_NAMES, FEATURE_VERSION


class RiskModel:
    def __init__(self, artifact_dir: Path, expected_sha256: str = "", manifest_sha256: str = ""):
        path = artifact_dir / "model.json"
        if path.stat().st_size > 16_000_000:
            raise ValueError("Model artifact too large")
        if (artifact_dir / "manifest.json").stat().st_size > 65536:
            raise ValueError("Manifest too large")
        manifest_bytes = (artifact_dir / "manifest.json").read_bytes()
        self.policy_version = hashlib.sha256(manifest_bytes).hexdigest()
        if manifest_sha256 and self.policy_version != manifest_sha256:
            raise ValueError("Manifest integrity check failed")
        self.manifest = json.loads(manifest_bytes)
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != self.manifest["model_sha256"] or (expected_sha256 and digest != expected_sha256):
            raise ValueError("Model integrity check failed")
        if self.manifest["feature_names"] != FEATURE_NAMES:
            raise ValueError("Model feature schema incompatible")
        if (self.manifest.get("currency") != "INR"
                or self.manifest.get("feature_version") != FEATURE_VERSION
                or self.manifest.get("feature_timezone") != "Asia/Kolkata"
                or self.manifest.get("history_window_hours") != 24):
            raise ValueError("Model currency or feature contract incompatible")
        if not 0 <= self.manifest["review_threshold"] < self.manifest["decline_threshold"] <= 1:
            raise ValueError("Invalid decision thresholds")
        calibration = self.manifest.get("calibration", {})
        if (calibration.get("method") != "sigmoid"
                or not isinstance(calibration.get("slope"), (int, float))
                or not isinstance(calibration.get("intercept"), (int, float))
                or not np.isfinite([calibration["slope"], calibration["intercept"]]).all()
                or calibration["slope"] <= 0):
            raise ValueError("Invalid calibration")
        self.model = xgb.Booster(params={"nthread": 1})
        self.model.load_model(path)
        if self.model.num_features() != len(FEATURE_NAMES):
            raise ValueError("Invalid feature count")
        metrics_path = artifact_dir / "metrics.json"
        if metrics_path.stat().st_size > 1_000_000:
            raise ValueError("Metrics artifact too large")
        metrics_bytes = metrics_path.read_bytes()
        if hashlib.sha256(metrics_bytes).hexdigest() != self.manifest.get("metrics_sha256"):
            raise ValueError("Metrics integrity check failed")
        self.metrics = json.loads(metrics_bytes)
        # Fail startup, not the first customer request, for incompatible artifacts.
        self.predict(dict.fromkeys(FEATURE_NAMES, 0.0))

    def predict(self, features: dict) -> float:
        if list(features) != FEATURE_NAMES:
            raise ValueError("Features must match model order")
        matrix = np.asarray([[features[name] for name in FEATURE_NAMES]], dtype=np.float32)
        if not np.isfinite(matrix).all():
            raise ValueError("Non-finite features")
        score = float(self.model.inplace_predict(matrix)[0])
        if not 0 <= score <= 1:
            raise ValueError("Invalid model score")
        calibration = self.manifest["calibration"]
        p = np.clip(score, 1e-7, 1 - 1e-7)
        logit = np.clip(calibration["slope"] * np.log(p / (1 - p)) + calibration["intercept"], -40, 40)
        return float(1 / (1 + np.exp(-logit)))

    @staticmethod
    def reasons(features: dict) -> list[str]:
        # These are observable risk signals, not causal/SHAP explanations of the model.
        flags = []
        if features["amount_ratio"] > 4:
            flags.append("Amount exceeds recent customer average")
        if features["count_1h"] >= 3:
            flags.append("Elevated transaction frequency")
        if features["new_device"]:
            flags.append("Device unseen in previous 24 hours")
        if features["new_country"]:
            flags.append("Country unseen in previous 24 hours")
        return flags or ["No rule-based risk signals; decision uses model score"]
