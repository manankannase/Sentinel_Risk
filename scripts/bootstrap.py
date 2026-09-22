"""Generate unique local credentials; never ship them in an archive."""
import hashlib
import json
import os
import secrets
from pathlib import Path

root = Path(__file__).resolve().parents[1]
directory = root / ".secrets"
manifest = root / "artifacts/manifest.json"
if not manifest.exists():
    raise SystemExit("Train the model first: python -m sentinel.train --output artifacts")
if directory.exists() or (root / ".env").exists():
    raise SystemExit("Existing credentials preserved. Do not rerun bootstrap to rotate a live database password.")
os.umask(0o077)
directory.mkdir(mode=0o700)
records = []
metrics_token = ""
for name, role, tenant in [("admin", "admin", "demo"), ("analyst", "analyst", "demo"),
                           ("scorer", "scorer", "demo"), ("metrics", "metrics", "operations")]:
    token = secrets.token_urlsafe(32)
    (directory / f"{name}_token").write_text(token + "\n")
    if name == "metrics":
        metrics_token = token
    records.append({"name": name, "role": role, "tenant": tenant, "sha256": hashlib.sha256(token.encode()).hexdigest()})
(directory / "api_keys.json").write_text(json.dumps(records, indent=2) + "\n")
(directory / "metrics_token").write_text(metrics_token + "\n")
passwords = {k: secrets.token_hex(24) for k in ["POSTGRES_PASSWORD", "REDIS_PASSWORD", "BROKER_PASSWORD", "GF_ADMIN_PASSWORD"]}
digest = json.loads(manifest.read_text())["model_sha256"]
env = {**passwords, "BROKER_USERNAME": "sentinel", "MODEL_SHA256": digest,
       "API_PORT": "8000", "GRAFANA_PORT": "3000", "MLFLOW_PORT": "5000", "DOMAIN": "localhost"}
(root / ".env").write_text("\n".join(f"{k}={v}" for k, v in env.items()) + "\n")
# Compose secrets are host bind mounts. Containers run under non-root UID 10001;
# files are readable inside the private 0700 parent directory, never world-traversable on host.
for file in directory.iterdir():
    file.chmod(0o444 if file.name in {"api_keys.json", "metrics_token"} else 0o600)
print("Credentials generated. Dashboard key: .secrets/admin_token (keep private).")
