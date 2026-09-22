import hashlib
import json
from pathlib import Path

import numpy as np
import xgboost as xgb

from sentinel.features import FEATURE_NAMES


class RiskModel:
    def __init__(self, artifact_dir: Path, expected_sha256: str = ""):
        path = artifact_dir / "model.json"
        if path.stat().st_size > 16_000_000:
            raise ValueError("Model artifact too large")
        if (artifact_dir / "manifest.json").stat().st_size > 65536:
            raise ValueError("Manifest too large")
        self.manifest = json.loads((artifact_dir / "manifest.json").read_text())
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        if digest != self.manifest["model_sha256"] or (expected_sha256 and digest != expected_sha256):
            raise ValueError("Model integrity check failed")
        if self.manifest["feature_names"] != FEATURE_NAMES:
            raise ValueError("Model feature schema incompatible")
        if not 0 <= self.manifest["review_threshold"] < self.manifest["decline_threshold"] <= 1:
            raise ValueError("Invalid decision thresholds")
        self.model = xgb.Booster(params={"nthread": 1})
        self.model.load_model(path)
        if self.model.num_features() != len(FEATURE_NAMES):
            raise ValueError("Invalid feature count")
        self.metrics = json.loads((artifact_dir / "metrics.json").read_text())

    def predict(self, features: dict) -> float:
        if list(features) != FEATURE_NAMES:
            raise ValueError("Features must match model order")
        matrix = np.asarray([[features[name] for name in FEATURE_NAMES]], dtype=np.float32)
        if not np.isfinite(matrix).all():
            raise ValueError("Non-finite features")
        score = float(self.model.inplace_predict(matrix)[0])
        if not 0 <= score <= 1:
            raise ValueError("Invalid model score")
        return score

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
