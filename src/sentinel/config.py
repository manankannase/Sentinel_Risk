from pathlib import Path

from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="SENTINEL_", extra="ignore")
    env: str = "development"
    database_url: str = "sqlite:///runtime/sentinel.db"
    redis_url: str = ""
    keys_file: Path = Path(".secrets/api_keys.json")
    model_dir: Path = Path("artifacts")
    model_sha256: str = ""
    allowed_hosts: str = "localhost,127.0.0.1,testserver,api"
    rate_per_minute: int = 120
    broker: str = ""
    broker_username: str = "sentinel"
    broker_password: str = ""
    broker_protocol: str = "PLAINTEXT"
    broker_ca_file: str = ""
    topic: str = "sentinel.decisions.v1"
    max_body_bytes: int = 8192

    @model_validator(mode="after")
    def production_requirements(self):
        if self.env not in {"development", "production", "test"}:
            raise ValueError("Unknown environment")
        if not 1 <= self.rate_per_minute <= 10000:
            raise ValueError("Rate limit out of range")
        if self.env == "production":
            if not self.database_url.startswith("postgresql+psycopg://"):
                raise ValueError("Production requires PostgreSQL")
            if not self.redis_url or len(self.model_sha256) != 64:
                raise ValueError("Production requires Redis and a pinned model digest")
            if "*" in self.allowed_hosts or not self.allowed_hosts:
                raise ValueError("Explicit host allowlist required")
            if self.broker and self.broker_protocol != "SASL_SSL":
                raise ValueError("Production event streaming requires SASL_SSL")
        return self
