"""Application configuration via Pydantic Settings.

All secrets (DATABASE_URL, OPENROUTER_API_KEY, HF_TOKEN) must be supplied via
environment variables or a .env file — never hard-coded in source.  Copy
.env.example to .env and fill in real values before running.
"""

import os

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )

    # Database — no default; must be set via DATABASE_URL env var / .env
    database_url: str = ""

    # OpenRouter
    openrouter_api_key: str = ""
    openrouter_rpm_limit: int = 60  # conservative free-tier default; override via env
    garak_parallel_attempts: int = 10
    garak_timeout_seconds: int = 7200 * 4
    garak_soft_probe_prompt_cap: int = 50   # meaningful sample size per probe
    garak_soft_probe_prompt_cap_max: int = 50

    # API server
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Gradio — must point to the API container name on the Docker network
    gradio_server_port: int = 7860
    api_base_url: str = "http://localhost:8000"

    # Scheduler
    scheduler_enabled: bool = True
    scheduler_top_n_models: int = 20
    scheduler_scan_ttl_days: int = 7
    scheduler_max_scan_cost_usd: float = 10.0  # per-model scan cost cap; None disables
    openrouter_rankings_url: str = "https://openrouter.ai/rankings"
    openrouter_catalog_url: str = "https://openrouter.ai/api/v1/models"

    # HuggingFace Dataset sync — optional, only needed for export/import scripts
    hf_dataset_repo: str = ""  # e.g. "your-username/open-llm-sec-leaderboard"
    hf_token: str = ""         # HuggingFace API token (write for export, read for private import)

    @field_validator("database_url")
    @classmethod
    def database_url_must_be_set(cls, v: str) -> str:
        """Raise at startup if DATABASE_URL is missing outside of test runs."""
        if not v and not os.environ.get("TESTING"):
            raise ValueError(
                "DATABASE_URL must be set. "
                "Add it to your .env file or export it as an environment variable. "
                "Example: postgresql://glokta:<password>@localhost:5432/glokta"
            )
        return v

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
