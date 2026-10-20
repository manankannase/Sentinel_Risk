"""Update only MODEL_SHA256 after intentionally promoting a new local model."""
import hashlib
import json
from pathlib import Path

root = Path(__file__).resolve().parents[1]
env_path = root / ".env"
digest = json.loads((root / "artifacts/inr-v2/manifest.json").read_text())["model_sha256"]
if len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
    raise SystemExit("Manifest has an invalid model digest")
if not env_path.exists():
    raise SystemExit("Run scripts/bootstrap.py first")
lines = env_path.read_text().splitlines()
matches = [i for i, line in enumerate(lines) if line.startswith("MODEL_SHA256=")]
if len(matches) != 1:
    raise SystemExit(".env must contain exactly one MODEL_SHA256 entry")
lines[matches[0]] = f"MODEL_SHA256={digest}"
manifest_digest = hashlib.sha256((root / "artifacts/inr-v2/manifest.json").read_bytes()).hexdigest()
lines = [line for line in lines if not line.startswith("MANIFEST_SHA256=")]
lines.append(f"MANIFEST_SHA256={manifest_digest}")
temporary = env_path.with_suffix(".tmp")
temporary.write_text("\n".join(lines) + "\n")
temporary.chmod(0o600)
temporary.replace(env_path)
print("Pinned model digest synchronized without rotating credentials.")
