from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Config(BaseSettings):
    redis_host: str = "redis"
    redis_port: int = 6379

    postgres_host: str = "postgres"
    postgres_port: int = 5432

    kafka_host: str = "kafka"
    kafka_port: int = 9092

    rabbitmq_host: str = "rabbitmq"
    rabbitmq_port: int = 5672
    rabbitmq_mgmt_port: int = 15672
    rabbitmq_user: str = Field(default="guest", validation_alias="RABBITMQ_DEFAULT_USER")
    rabbitmq_password: str = Field(default="guest", validation_alias="RABBITMQ_DEFAULT_PASS")

    postgres_user: str = "postgres"
    postgres_password: str = "postgres"
    postgres_db: str = "tideo"

    readiness_timeout_seconds: float = 2.0
    dispatcher_heartbeat_ttl: int = 30
    allowed_origins: str = "http://localhost:5173"
    sleep_lease_host: str = ""
    sleep_lease_interval_seconds: int = Field(default=60, ge=10)

    data_dir: Path = Path("/data")
    max_upload_bytes: int = 4 * 1024**3
    max_source_seconds: int = 7200
    storage_budget_bytes: int = 50 * 1024**3
    storage_min_free_bytes: int = 512 * 1024**2
    output_ttl_days: int = 7
    cleanup_interval_seconds: int = 3600
    source_grace_seconds: int = 86400

    x264_preset: str = "ultrafast"
    dev_max_renditions: int = 4
    transcode_max_seconds: int = 1800

    stt_rate_limit: str = Field(default="3/60", pattern=r"^[1-9][0-9]*/[1-9][0-9]*$")
    stt_model: str = "base"
    stt_compute_type: str = "int8"
    stt_max_retries: int = 3

    profile: Literal["dev", "deploy"] = "dev"
    log_level: str = "INFO"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @field_validator("allowed_origins")
    @classmethod
    def _valid_origins(cls, value: str) -> str:
        origins = [
            origin.strip().rstrip("/") for origin in value.split(",") if origin.strip()
        ]
        for origin in origins:
            parsed = urlsplit(origin)
            if (
                origin == "*"
                or parsed.scheme not in {"http", "https"}
                or not parsed.netloc
            ):
                raise ValueError(f"invalid browser origin: {origin}")
            if parsed.path or parsed.query or parsed.fragment:
                raise ValueError(
                    f"origin must not contain a path, query, or fragment: {origin}"
                )
        if not origins:
            raise ValueError("at least one browser origin is required")
        return ",".join(origins)

    @property
    def allowed_origin_list(self) -> list[str]:
        return self.allowed_origins.split(",")

    @property
    def uploads_dir(self) -> Path:
        return self.data_dir / "uploads"

    @property
    def output_dir(self) -> Path:
        return self.data_dir / "output"

    @property
    def celery_broker_url(self) -> str:
        return (
            f"amqp://{self.rabbitmq_user}:{self.rabbitmq_password}"
            f"@{self.rabbitmq_host}:{self.rabbitmq_port}//"
        )

    @property
    def celery_result_backend(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"
    
    @property
    def kafka_bootstrap(self) -> str:
        return f"{self.kafka_host}:{self.kafka_port}"

    @property
    def postgres_dsn(self) -> str:
        return (f"postgresql://{self.postgres_user}:{self.postgres_password}"
                f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}")


config = Config()  # type: ignore[call-arg]
