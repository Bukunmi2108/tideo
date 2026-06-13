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

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    @property
    def celery_broker_url(self) -> str:
        return (
            f"amqp://{self.rabbitmq_user}:{self.rabbitmq_password}"
            f"@{self.rabbitmq_host}:{self.rabbitmq_port}//"
        )

    @property
    def celery_result_backend(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"


config = Config()
