from pathlib import Path
from typing import Literal
from pydantic import Field
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
    rabbitmq_user: str = Field(default="guest", validation_alias="RABBITMQ_DEFAULT_USER")
    rabbitmq_password: str = Field(default="guest", validation_alias="RABBITMQ_DEFAULT_PASS")

    readiness_timeout_seconds: float = 2.0

    data_dir: Path = Path("/data")
    max_upload_bytes: int = 4 * 1024**3
    max_source_seconds: int = 7200
    storage_watermark_pct: int = 85
    output_ttl_days: int = 7

    x264_preset: str = "ultrafast"
    dev_max_renditions: int | None = 2
    transcode_max_seconds: int = 1800

    stt_provider: Literal["local", "openai"] = "local"
    stt_rate_limit: str = "3/60"

    admin_token: str = Field(min_length=1)
    profile: Literal["dev", "deploy"] = "dev"

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

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


config = Config()  # type: ignore[call-arg]
