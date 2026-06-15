"""Centralized configuration settings loader.

Only infrastructure-level settings live here.
All integration credentials (LLM API keys, Jira, GitHub, Slack, Teams, etc.)
are stored encrypted in the database and managed through the Team Admin
onboarding / project configuration pages.
"""
from functools import lru_cache
from pathlib import Path
from typing import List, Optional

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables.

    Only non-credential, infrastructure settings are defined here.
    Integration credentials are managed per-project in the DB.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Encryption key for secrets stored in DB ──────────────────────────────
    # This is the ONLY secret allowed here — it is the master key used to
    # encrypt/decrypt all other credentials stored in the database.
    integration_secret_key: Optional[str] = None
    integration_secret_key_file: str = "./data/.integration.key"

    # ── OpenTelemetry & Ingestion ─────────────────────────────────────────────
    otlp_collector_port: int = 4318
    otlp_endpoint: str = "http://localhost:4318/v1/logs"
    ingestion_api_host: str = "0.0.0.0"
    ingestion_api_port: int = 8000

    # ── Storage ───────────────────────────────────────────────────────────────
    database_path: str = "./data/incidents.db"
    pdf_output_dir: str = "./data/pdfs"
    patch_output_dir: str = "./data/patches"

    # ── Auto-Fix Behaviour ────────────────────────────────────────────────────
    auto_fix_enabled: bool = True
    auto_fix_severity_threshold: str = "HIGH"
    auto_fix_requires_approval: bool = True
    auto_fix_max_file_size_kb: int = 500
    auto_pr_create: bool = True
    auto_pr_label: str = "ai-generated,needs-review"

    # ── Error Detection & Deduplication ──────────────────────────────────────
    error_fingerprint_algorithm: str = "simhash"
    duplicate_threshold: float = 0.85
    error_burst_window_minutes: int = 10
    error_burst_threshold: int = 5

    # ── UI Deep-link Base URL ─────────────────────────────────────────────────
    # Used to generate clickable incident links in Slack/Teams notifications.
    ui_base_url: str = "http://localhost:8080"

    # ── Observability & Debugging ─────────────────────────────────────────────
    log_level: str = "INFO"
    langgraph_tracing: bool = True
    langchain_tracing_v2: bool = True
    langchain_api_key: Optional[str] = None
    langchain_project: str = "mule-monitor-poc"

    # ── Rate Limiting & Caching ───────────────────────────────────────────────
    llm_cache_enabled: bool = True
    llm_cache_ttl_hours: int = 24
    rate_limit_requests_per_minute: int = 10

    def get_pr_labels(self) -> List[str]:
        """Parse PR labels from comma-separated string."""
        return [label.strip() for label in self.auto_pr_label.split(",") if label.strip()]

    def ensure_directories(self) -> None:
        """Create necessary directories if they don't exist."""
        database_dir = Path(self.database_path).expanduser().resolve().parent
        pdf_dir = Path(self.pdf_output_dir).expanduser().resolve()
        patch_dir = Path(self.patch_output_dir).expanduser().resolve()

        database_dir.mkdir(parents=True, exist_ok=True)
        pdf_dir.mkdir(parents=True, exist_ok=True)
        patch_dir.mkdir(parents=True, exist_ok=True)


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    settings = Settings()
    settings.ensure_directories()
    return settings
