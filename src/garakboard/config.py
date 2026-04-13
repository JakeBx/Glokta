"""Application configuration via Pydantic Settings."""

import os

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database
    database_url: str = (
        "postgresql://garakboard:garakboard@localhost:5432/garakboard"
    )

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_rpm_limit: int = 14
    garak_parallel_attempts: int = 1

    # API server
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Gradio
    gradio_server_port: int = 7860
    api_base_url: str = "http://localhost:8000"

    @field_validator("openrouter_api_key")
    @classmethod
    def api_key_must_be_set(cls, v: str) -> str:
        """Raise at startup if the API key is missing outside of test runs."""
        if not v and not os.environ.get("TESTING"):
            raise ValueError(
                "OPENROUTER_API_KEY must be set. "
                "Add it to your .env file or export it as an environment variable."
            )
        return v


settings = Settings()
