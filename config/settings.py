"""Centralized configuration settings loader."""
from typing import Optional, List
from pydantic_settings import BaseSettings, SettingsConfigDict
from functools import lru_cache
import os


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""
    
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore"
    )
    
    # LLM Configuration
    llm_provider: str = "openai"
    openai_api_key: Optional[str] = None
    anthropic_api_key: Optional[str] = None
    google_api_key: Optional[str] = None
    groq_api_key: Optional[str] = None
    grok_api_key: Optional[str] = None  # xAI Grok (uses OpenAI-compatible API)
    ollama_base_url: str = "http://localhost:11434"
    llm_model: str = "gpt-4o"
    llm_temperature: float = 0.2
    llm_max_tokens: int = 4096

    # LLM fallback (used when primary provider is rate-limited / quota exhausted)
    llm_fallback_provider: str = "openai"
    llm_fallback_model: str = "gpt-4o-mini"
    
    # OpenTelemetry & Ingestion
    otlp_endpoint: str = "http://localhost:4318/v1/logs"
    ingestion_api_host: str = "0.0.0.0"
    ingestion_api_port: int = 8000
    
    # GitHub Integration
    github_token: Optional[str] = None
    github_org: str = "your-org-name"
    github_default_repo: str = "mule-app-repo"  # Main repo for PRs
    github_base_branch: str = "main"
    
    # Jira Integration
    jira_url: str = "https://your-company.atlassian.net"
    jira_email: Optional[str] = None
    jira_api_token: Optional[str] = None
    jira_project_key: str = "MULE"
    jira_default_assignee: str = "unassigned"
    
    # Notification Channels
    slack_webhook_url: Optional[str] = None
    teams_webhook_url: Optional[str] = None
    email_enabled: bool = False
    email_smtp_host: str = "smtp.gmail.com"
    email_smtp_port: int = 587
    email_from: Optional[str] = None
    email_to: Optional[str] = None
    email_password: Optional[str] = None
    
    # Auto-Fix Configuration
    auto_fix_enabled: bool = True
    auto_fix_severity_threshold: str = "HIGH"
    auto_fix_requires_approval: bool = True
    auto_fix_max_file_size_kb: int = 500
    auto_pr_create: bool = True
    auto_pr_label: str = "ai-generated,needs-review"
    
    # Error Detection & Deduplication
    error_fingerprint_algorithm: str = "simhash"
    duplicate_threshold: float = 0.85
    error_burst_window_minutes: int = 10
    error_burst_threshold: int = 5
    
    # Storage
    database_path: str = "./data/incidents.db"
    pdf_output_dir: str = "./data/pdfs"
    patch_output_dir: str = "./data/patches"
    
    # Observability & Debugging
    log_level: str = "INFO"
    langgraph_tracing: bool = True
    langchain_tracing_v2: bool = True
    langchain_api_key: Optional[str] = None
    langchain_project: str = "mule-monitor-poc"
    
    # Rate Limiting & Caching
    llm_cache_enabled: bool = True
    llm_cache_ttl_hours: int = 24
    rate_limit_requests_per_minute: int = 10
    
    def get_pr_labels(self) -> List[str]:
        """Parse PR labels from comma-separated string."""
        return [label.strip() for label in self.auto_pr_label.split(",")]
    
    def ensure_directories(self) -> None:
        """Create necessary directories if they don't exist."""
        os.makedirs(os.path.dirname(self.database_path), exist_ok=True)
        os.makedirs(self.pdf_output_dir, exist_ok=True)
        os.makedirs(self.patch_output_dir, exist_ok=True)


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    settings = Settings()
    settings.ensure_directories()
    return settings
