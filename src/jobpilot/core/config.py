"""Application configuration.

All secrets and tunables live in environment variables (loaded from ``.env``).
Nothing here requires an API key: JobPilot must run in a fully local mode.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[3]


class Settings(BaseSettings):
    """Typed settings with sensible, safe defaults."""

    model_config = SettingsConfigDict(
        env_file=(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # --- app ---------------------------------------------------------------
    app_name: str = "JobPilot"
    environment: str = "development"
    secret_key: str = "jobpilot-dev-secret-change-me"
    debug: bool = True
    host: str = "127.0.0.1"
    port: int = 5000

    # --- storage -----------------------------------------------------------
    data_dir: Path = PROJECT_ROOT / "data"
    trace_dir: Path = PROJECT_ROOT / "traces"
    db_name: str = "jobpilot.sqlite3"

    # --- LLM gateway -------------------------------------------------------
    openai_api_key: str = ""
    openai_base_url: str = "https://api.openai.com/v1"
    jobs_fast_model: str = "gpt-4o-mini"
    jobs_strong_model: str = "gpt-4o-mini"
    embedding_model: str = "text-embedding-3-small"
    embedding_provider: str = "local"  # local | openai
    llm_timeout_seconds: float = 60.0
    max_budget_usd: float = 1.50
    daily_budget_usd: float = 0.50
    llm_max_retries: int = 3

    # --- harness -----------------------------------------------------------
    max_steps: int = 12
    max_consecutive_failures: int = 3
    context_token_budget: int = 6000
    require_approval_for_writes: bool = True

    # --- sources -----------------------------------------------------------
    adzuna_app_id: str = ""
    adzuna_app_key: str = ""
    adzuna_country: str = "gb"
    enable_rss_sources: bool = True
    rss_feeds: str = "https://weworkremotely.com/categories/remote-programming-jobs.rss"
    enable_linkedin: bool = False

    # Greenhouse and Workable are free, public ATS APIs. Configure company
    # tokens/account slugs; no API key is involved.
    enable_greenhouse: bool = True
    greenhouse_boards: str = "figma,vercel,gitlab,dropbox"
    enable_workable: bool = True
    workable_accounts: str = "devsinc-17,userwise-services,prime-system"

    # Public, key-free boards. Each can be switched off if a source is flaky
    # or if a deployment wants a narrower result set.
    enable_mustakbil: bool = True
    enable_himalayas: bool = True
    enable_themuse: bool = True
    enable_jobicy: bool = True

    # Indeed retired its public search API and prohibits scraping. This flag is
    # accepted for older deployments but the adapter stays disabled.
    enable_indeed: bool = False

    http_timeout_seconds: float = 20.0
    source_budget_seconds: float = 45.0
    search_cache_ttl_seconds: int = 300
    max_resume_mb: int = 10
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    )

    # --- derived -----------------------------------------------------------
    @field_validator("data_dir", "trace_dir", mode="after")
    @classmethod
    def _expand(cls, value: Path) -> Path:
        return value.expanduser()

    @property
    def db_path(self) -> Path:
        return self.data_dir / self.db_name

    @property
    def rss_feed_list(self) -> list[str]:
        return [f.strip() for f in self.rss_feeds.split(",") if f.strip()]

    @property
    def greenhouse_board_list(self) -> list[str]:
        return [b.strip() for b in self.greenhouse_boards.split(",") if b.strip()]

    @property
    def workable_account_list(self) -> list[str]:
        return [a.strip() for a in self.workable_accounts.split(",") if a.strip()]

    @property
    def llm_enabled(self) -> bool:
        return bool(self.openai_api_key.strip())

    @property
    def budget_remaining(self) -> float:
        return self.max_budget_usd

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.trace_dir.mkdir(parents=True, exist_ok=True)

    def public_dict(self) -> dict:
        """Settings that are safe to render in the UI (no secrets)."""
        return {
            "app_name": self.app_name,
            "environment": self.environment,
            "llm_enabled": self.llm_enabled,
            "fast_model": self.jobs_fast_model,
            "strong_model": self.jobs_strong_model,
            "embedding_provider": self.embedding_provider,
            "max_budget_usd": self.max_budget_usd,
            "daily_budget_usd": self.daily_budget_usd,
            "max_steps": self.max_steps,
            "context_token_budget": self.context_token_budget,
            "adzuna_enabled": bool(self.adzuna_app_id and self.adzuna_app_key),
            "rss_enabled": self.enable_rss_sources,
            "linkedin_enabled": self.enable_linkedin,
            "greenhouse_enabled": self.enable_greenhouse,
            "greenhouse_boards": self.greenhouse_board_list,
            "workable_enabled": self.enable_workable,
            "workable_accounts": self.workable_account_list,
            "indeed_enabled": False,
            "data_dir": str(self.data_dir),
            "db_path": str(self.db_path),
            "trace_dir": str(self.trace_dir),
        }


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    settings = Settings()
    settings.ensure_dirs()
    return settings
