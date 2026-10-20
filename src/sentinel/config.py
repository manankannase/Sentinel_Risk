from pathlib import Path
from typing import Literal

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import make_url


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SENTINEL_", extra="ignore")
    env: str = "development"
    database_url: str = "sqlite:///runtime/sentinel.db"
    redis_url: str = ""
    keys_file: Path = Path(".secrets/api_keys.json")
    model_dir: Path = Path("artifacts/inr-v2")
    model_sha256: str = ""
    manifest_sha256: str = ""
    decision_mode: Literal["simulation", "shadow"] = "simulation"
    allowed_hosts: str = "localhost,127.0.0.1,testserver,api"
    rate_per_minute: int = 120
    broker: str = ""
    broker_username: str = "sentinel"
    broker_password: str = ""
    broker_protocol: str = "PLAINTEXT"
    broker_ca_file: str = ""
    topic: str = "sentinel.decisions.v1"
    max_body_bytes: int = 8192
    body_timeout_seconds: float = 5
    max_customer_history: int = 10000

    @model_validator(mode="after")
    def production_requirements(self):
        if self.env not in {"development", "production", "test"}:
            raise ValueError("Unknown environment")
        if not 1 <= self.rate_per_minute <= 10000:
            raise ValueError("Rate limit out of range")
        if not 512 <= self.max_body_bytes <= 65536 or not 0 < self.body_timeout_seconds <= 30:
            raise ValueError("Invalid request bounds")
        if not 1 <= self.max_customer_history <= 10000:
            raise ValueError("Invalid customer history bound")
        for digest in (self.model_sha256, self.manifest_sha256):
            if digest and (len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest)):
                raise ValueError("Invalid SHA-256 pin")
        if self.env == "production":
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("Production requires PostgreSQL")
            if make_url(self.database_url).query.get("sslmode") != "verify-full":
                raise ValueError("Production PostgreSQL requires sslmode=verify-full")
            if not self.redis_url.startswith("rediss://") or not self.model_sha256 or not self.manifest_sha256:
                raise ValueError("Production requires TLS Redis and pinned model and manifest digests")
            if make_url(self.redis_url).query.get("ssl_cert_reqs", "required") != "required":
                raise ValueError("Redis certificate verification required")
            if make_url(self.redis_url).query.get("ssl_check_hostname", "true").lower() not in {"true", "1"}:
                raise ValueError("Redis hostname verification required")
            if self.decision_mode != "shadow":
                raise ValueError("Production requires shadow mode until real-data validation is approved")
            if "*" in self.allowed_hosts or not self.allowed_hosts:
                raise ValueError("Explicit host allowlist required")
            if self.broker and self.broker_protocol != "SASL_SSL":
                raise ValueError("Production event streaming requires SASL_SSL")
        return self
