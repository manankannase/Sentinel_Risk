import hashlib
import json
import shutil
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from sentinel.api import create_app
from sentinel.config import Settings
from sentinel.db import engine_for, initialize


@pytest.fixture(scope="session")
def artifacts(tmp_path_factory):
    path = tmp_path_factory.mktemp("model") / "artifacts"
    shutil.copytree(Path(__file__).resolve().parents[1] / "artifacts/inr-v2", path)
    return path


@pytest.fixture()
def app(tmp_path, artifacts):
    key = "test-local-admin-key-that-is-long-enough"
    keys = tmp_path / "keys.json"
    keys.write_text(json.dumps([
        {"name": "admin", "tenant": "test", "role": "admin", "sha256": hashlib.sha256(key.encode()).hexdigest()},
        {"name": "analyst", "tenant": "test", "role": "analyst", "sha256": hashlib.sha256(b"test-analyst-key").hexdigest()},
        {"name": "scorer", "tenant": "test", "role": "scorer", "sha256": hashlib.sha256(b"test-scorer-key").hexdigest()},
        {"name": "metrics", "tenant": "ops", "role": "metrics", "sha256": hashlib.sha256(b"test-metrics-key").hexdigest()},
        {"name": "other-admin", "tenant": "other", "role": "admin", "sha256": hashlib.sha256(b"test-other-key").hexdigest()},
    ]))
    database = f"sqlite:///{tmp_path / 'test.db'}"
    initialize(engine_for(database))
    settings = Settings(env="test", database_url=database, keys_file=keys, model_dir=artifacts, allowed_hosts="testserver")
    return create_app(settings), key


@pytest.fixture()
def client(app):
    application, key = app
    with TestClient(application) as client:
        yield client, {"Authorization": f"Bearer {key}"}
