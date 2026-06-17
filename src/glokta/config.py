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
    openrouter_rpm_limit: int = 600
    garak_parallel_attempts: int = 10
    garak_timeout_seconds: int = 46_000
    garak_soft_probe_prompt_cap: int = 1_000   # meaningful sample size per probe
    garak_soft_probe_prompt_cap_max: int = 1_000

    # API server
    api_host: str = "0.0.0.0"
    api_port: int = 8000

    # Gradio — must point to the API container name on the Docker network
    gradio_server_port: int = 7860
    api_base_url: str = "http://localhost:8000"

    # Scheduler
    scheduler_enabled: bool = True
    scheduler_top_n_models: int = 5
    scheduler_scan_ttl_days: int = 7
    scheduler_max_scan_cost_usd: float = 10.0  # per-model scan cost cap; None disables
    openrouter_rankings_url: str = "https://openrouter.ai/rankings"
    openrouter_catalog_url: str = "https://openrouter.ai/api/v1/models"

    # HuggingFace — token shared for dataset sync and inference provider scanning
    hf_dataset_repo: str = ""  # e.g. "your-username/open-llm-sec-leaderboard"
    hf_token: str = ""         # HuggingFace API token (write for export, read for import/inference)
    scheduler_hf_top_n_models: int = 5
    hf_rpm_limit: int = 600     # HF serverless inference has stricter rate limits than OpenRouter

    # CTI living benchmark
    cti_enabled: bool = True          # gate CTI ingest/eval flows
    nvd_api_key: str = ""              # NVD API 2.0 key (50 req/30s); empty falls back to 5/30s
    cti_data_dir: str = "/tmp/glokta-cti"  # git clone path for cvelistV5/galaxy/attack
    cti_rpm_limit: int = 600           # rate limit for CTI inference calls
    cti_prequential_fading_factor: float = 0.99  # Gama et al. recency-weighting factor
    cti_withhold_window_days: int = 14  # newest-slice raw-text holdout window
    cti_judge_model: str = "anthropic/claude-opus-4.8"  # SYN judge — OpenRouter slug (forced via OpenRouter)
    # Multi-source advisory collection (ATE/TAA/SYN). Comma-separated source keys from
    # report.SOURCES, deduped across sources (joint advisories are co-sealed).
    cti_report_sources: str = "cisa,cccs,ncsc,dfir"
    cti_report_max_per_source: int = 20  # advisory pages fetched per source per run
    # Recent-slice bounding + eval safety rails
    cti_ingest_lookback_days: int = 3   # only ingest items changed within this window
    cti_max_items_per_run: int = 100    # hard cap on inference calls per CTI run
    cti_eval_commit_every: int = 20     # commit results every N items (enables resume)
    cti_run_timeout_seconds: int = 3600  # wall-clock budget for one CTI run

    @property
    def cti_report_source_list(self) -> list[str]:
        """Parsed, whitespace-stripped list of configured report sources."""
        return [s.strip() for s in self.cti_report_sources.split(",") if s.strip()]

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
