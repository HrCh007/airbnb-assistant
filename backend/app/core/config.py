"""Application settings, loaded from environment variables (and an optional .env file).

Every external integration is configurable here. No secret has a default value.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- General -----------------------------------------------------------
    environment: Literal["development", "test", "production"] = "development"
    app_name: str = "Delhi Stays Guest Assistant"
    log_level: str = "INFO"
    frontend_url: str = "http://localhost:3000"
    backend_url: str = "http://localhost:8000"
    # Extra comma-separated CORS origins (e.g. Vercel preview URLs).
    cors_extra_origins: str = ""

    # --- Database ----------------------------------------------------------
    database_url: str = "postgresql+asyncpg://airbnb:airbnb@localhost:5432/airbnb_assistant"
    db_pool_size: int = 5
    db_max_overflow: int = 5

    # --- Security ----------------------------------------------------------
    secret_key: str = Field(default="", description="JWT signing key. Required in production.")
    admin_token_ttl_minutes: int = 12 * 60
    rate_limit_chat_per_minute: int = 20
    rate_limit_default_per_minute: int = 120
    rate_limit_login_per_minute: int = 5
    max_upload_mb: int = 10
    max_message_chars: int = 1500

    # --- LLM (Anthropic Claude) -------------------------------------------
    anthropic_api_key: str = ""
    claude_model: str = "claude-opus-5-5"
    # Model used for the fast query-planning / intent-routing call.
    claude_planner_model: str = "claude-opus-5-5"
    claude_answer_effort: Literal["low", "medium", "high", "xhigh", "max"] = "medium"
    claude_planner_effort: Literal["low", "medium", "high", "xhigh", "max"] = "low"
    claude_max_tokens: int = 4000
    claude_timeout_seconds: float = 60.0
    claude_max_retries: int = 2
    # Server-side refusal fallback (beta). Disable if your account/platform rejects it.
    claude_enable_refusal_fallback: bool = True
    history_messages_for_llm: int = 12
    # Optional path to a custom system prompt file (defaults to app/prompts/system_prompt.md).
    system_prompt_path: str = ""

    # --- Embeddings --------------------------------------------------------
    # "openai" uses text-embedding-3-small; "local" is a deterministic hashing
    # embedder for offline development and tests (lexical, not semantic).
    embedding_provider: Literal["auto", "openai", "local"] = "auto"
    openai_api_key: str = ""
    openai_embedding_model: str = "text-embedding-3-small"
    embedding_dim: int = 1536

    # --- RAG ---------------------------------------------------------------
    chunk_size: int = 900  # characters
    chunk_overlap: int = 150
    retrieval_top_k: int = 6
    retrieval_candidate_k: int = 20
    retrieval_min_vector_similarity: float = 0.15
    hybrid_search: bool = True

    # --- Local discovery (Google Places API New) ---------------------------
    places_provider: Literal["auto", "google", "mock", "disabled"] = "auto"
    google_maps_api_key: str = ""
    places_timeout_seconds: float = 8.0
    places_max_retries: int = 2
    places_max_results: int = 12
    places_results_to_show: int = 5
    default_discovery_radius_m: int = 3000
    max_discovery_radius_m: int = 25000
    places_language: str = "en"
    places_region: str = "in"
    places_cache_ttl_seconds: int = 600

    # Ranking weights (sum need not be 1; they are normalised).
    rank_weight_relevance: float = 0.25
    rank_weight_distance: float = 0.25
    rank_weight_rating: float = 0.30
    rank_weight_open: float = 0.10
    rank_weight_preference: float = 0.10

    # --- Admin bootstrap ---------------------------------------------------
    admin_email: str = ""
    admin_password: str = ""

    # --- Optional Supabase (only used by docs / storage-free deployment) ----
    supabase_url: str = ""
    supabase_service_role_key: str = ""

    @field_validator(
        "anthropic_api_key", "openai_api_key", "google_maps_api_key", "secret_key",
        "supabase_service_role_key", "admin_password", mode="before",
    )
    @classmethod
    def _ignore_comment_values(cls, v):
        # Docker Compose passes "KEY=   # comment" through as the literal value
        # "# comment"; treat that as unset rather than as a (bogus) credential.
        if isinstance(v, str) and v.strip().startswith("#"):
            return ""
        return v.strip() if isinstance(v, str) else v

    @field_validator("database_url")
    @classmethod
    def _normalise_db_url(cls, v: str) -> str:
        # Accept plain postgres:// URLs from providers (Supabase/Render/Railway).
        if v.startswith("postgres://"):
            v = "postgresql://" + v[len("postgres://") :]
        if v.startswith("postgresql://"):
            v = "postgresql+asyncpg://" + v[len("postgresql://") :]
        return v

    @property
    def cors_origins(self) -> list[str]:
        origins = [self.frontend_url.rstrip("/")]
        origins += [o.strip().rstrip("/") for o in self.cors_extra_origins.split(",") if o.strip()]
        return origins

    @property
    def llm_enabled(self) -> bool:
        return bool(self.anthropic_api_key)

    @property
    def resolved_embedding_provider(self) -> str:
        if self.embedding_provider == "auto":
            return "openai" if self.openai_api_key else "local"
        return self.embedding_provider

    @property
    def resolved_places_provider(self) -> str:
        if self.places_provider == "auto":
            return "google" if self.google_maps_api_key else "disabled"
        return self.places_provider

    def validate_for_production(self) -> list[str]:
        problems: list[str] = []
        if self.environment == "production":
            if len(self.secret_key) < 32:
                problems.append("SECRET_KEY must be set to at least 32 random characters.")
            if self.resolved_places_provider == "mock":
                problems.append("PLACES_PROVIDER=mock is not allowed in production.")
        return problems


@lru_cache
def get_settings() -> Settings:
    return Settings()
