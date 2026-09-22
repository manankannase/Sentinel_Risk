import json

import pytest

from sentinel.features import FEATURE_NAMES
from sentinel.ml import RiskModel


def test_model_digest_and_feature_order_are_enforced(artifacts, tmp_path):
    model = RiskModel(artifacts)
    values = dict.fromkeys(FEATURE_NAMES, 0.0)
    assert 0 <= model.predict(values) <= 1
    with pytest.raises(ValueError, match="order"):
        model.predict(dict(reversed(list(values.items()))))

    tampered = tmp_path / "tampered"
    tampered.mkdir()
    for name in ("model.json", "manifest.json", "metrics.json"):
        (tampered / name).write_bytes((artifacts / name).read_bytes())
    manifest = json.loads((tampered / "manifest.json").read_text())
    manifest["model_sha256"] = "0" * 64
    (tampered / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="integrity"):
        RiskModel(tampered)
