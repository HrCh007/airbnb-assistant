"""Application settings, loaded from environment variables (and an optional .env file).

Zero-cost local architecture: no setting here requires an API key, a billing
account or a paid service. Everything has a working default.
"""

from __future__ import annotations

import secrets
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    # --- General -----------------------------------------------------------
    environment: Literal["development", "test", "production"] = "development"
    app_name: str = "Hideout Studios Guest Assistant"
    log_level: str = "INFO"
    frontend_url: str = "http://localhost:3000"
    cors_extra_origins: str = ""

    # --- Storage (all local, persisted via Docker volumes) -----------------
    # Root for the SQLite database, uploaded documents and the generated secret.
    data_dir: str = "./.data"
    database_url: str = ""  # default: sqlite+aiosqlite:///<data_dir>/app.db
    # Bundled data (sample documents, discovery datasets) shipped in the repo/image.
    bundled_data_dir: str = ""  # default: <repo>/data

    # --- Vector store (ChromaDB) --------------------------------------------
    # http: talk to the chromadb container; persistent: embedded on-disk store
    # under <data_dir>/chroma (no container needed); memory: tests only.
    chroma_mode: Literal["http", "persistent", "memory"] = "persistent"
    chroma_host: str = "chromadb"
    chroma_port: int = 8000

    # --- Embeddings (local) ---------------------------------------------------
    # sentence-transformers runs locally; "hash" is a tiny lexical fallback used
    # by the automated tests and when the model can't be loaded.
    embedding_provider: Literal["sentence-transformers", "hash"] = "sentence-transformers"
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    embedding_device: str = "cpu"

    # --- Local LLM (Ollama) ---------------------------------------------------
    llm_enabled: bool = True
    ollama_url: str = "http://localhost:11434"
    ollama_model: str = "qwen3:1.7b"
    ollama_timeout_seconds: float = 180.0
    ollama_num_ctx: int = 4096
    ollama_temperature: float = 0.3
    ollama_keep_alive: str = "30m"
    # Ask thinking-capable models (e.g. Qwen3) to answer directly - much faster on CPU.
    ollama_disable_thinking: bool = True
    # Use the LLM to classify messages the deterministic router is unsure about.
    llm_routing: bool = True
    # Check streamed answers against the retrieved facts; replace them if they
    # contain facts (times, codes, numbers, place names) not found in the context.
    verify_llm_answers: bool = True
    history_messages_for_llm: int = 8
    system_prompt_path: str = ""

    # --- RAG ------------------------------------------------------------------
    chunk_size: int = 900
    chunk_overlap: int = 150
    retrieval_top_k: int = 6
    retrieval_candidate_k: int = 20
    retrieval_min_vector_similarity: float = 0.15

    # --- Local discovery (OpenStreetMap) --------------------------------------
    # Live queries to the public Overpass API (free, no key; fair-use limits).
    discovery_live: bool = True
    # Comma-separated public Overpass servers, tried in order until one answers.
    overpass_urls: str = (
        "https://overpass-api.de/api/interpreter,"
        "https://overpass.private.coffee/api/interpreter,"
        "https://overpass.kumi.systems/api/interpreter,"
        "https://maps.mail.ru/osm/tools/overpass/api/interpreter"
    )
    # When every Overpass server fails, search OpenStreetMap's Nominatim instead.
    discovery_nominatim_fallback: bool = True
    overpass_url: str = ""  # legacy single-server setting; if set, it is tried first
    nominatim_url: str = "https://nominatim.openstreetmap.org"
    # OSM usage policies require an identifying User-Agent with contact details.
    osm_user_agent: str = "HideoutStudiosGuestAssistant/1.0 (self-hosted; set OSM_USER_AGENT with your contact)"
    discovery_timeout_seconds: float = 12.0
    # Total time allowed for trying Overpass servers before moving to the next fallback.
    discovery_total_budget_seconds: float = 25.0
    discovery_cache_ttl_seconds: int = 3600
    # After a live failure, skip live calls for this long and use local datasets.
    discovery_failure_cooldown_seconds: int = 60
    # Fictional sample places (clearly labelled) when no real data is available.
    # Off by default: with it off, a failed live search says so honestly instead.
    discovery_sample_data: bool = False
    default_discovery_radius_m: int = 3000
    allowed_radii_km: str = "1,3,5,10"
    max_discovery_radius_m: int = 10000
    places_results_to_show: int = 6

    # Ranking weights (normalised).
    rank_weight_relevance: float = 0.40
    rank_weight_distance: float = 0.35
    rank_weight_open: float = 0.10
    rank_weight_preference: float = 0.10
    rank_weight_completeness: float = 0.05

    # --- Security -------------------------------------------------------------
    secret_key: str = ""  # generated and persisted in <data_dir>/secret_key if empty
    admin_token_ttl_minutes: int = 12 * 60
    admin_email: str = ""
    admin_password: str = ""
    rate_limit_chat_per_minute: int = 20
    rate_limit_default_per_minute: int = 120
    rate_limit_login_per_minute: int = 5
    max_upload_mb: int = 10
    max_message_chars: int = 1500
    # Send an SSE keep-alive comment when nothing else was sent for this long.
    sse_heartbeat_seconds: float = 5.0

    # --- Startup ----------------------------------------------------------------
    # Load the fictional sample properties/documents when the database is empty.
    auto_seed_sample_data: bool = True

    @field_validator("secret_key", "admin_password", "admin_email", mode="before")
    @classmethod
    def _ignore_comment_values(cls, v):
        # Docker Compose passes "KEY=   # comment" through as the value "# comment".
        if isinstance(v, str) and v.strip().startswith("#"):
            return ""
        return v.strip() if isinstance(v, str) else v

    # ----------------------------------------------------------------- derived
    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir).expanduser().resolve()
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def uploads_path(self) -> Path:
        p = self.data_path / "uploads"
        p.mkdir(parents=True, exist_ok=True)
        return p

    @property
    def bundled_path(self) -> Path:
        if self.bundled_data_dir:
            return Path(self.bundled_data_dir).resolve()
        return Path(__file__).resolve().parents[3] / "data"

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite+aiosqlite:///{self.data_path / 'app.db'}"

    @property
    def cors_origins(self) -> list[str]:
        origins = [self.frontend_url.rstrip("/")]
        origins += [o.strip().rstrip("/") for o in self.cors_extra_origins.split(",") if o.strip()]
        return origins

    @property
    def overpass_endpoints(self) -> list[str]:
        urls = [self.overpass_url] if self.overpass_url else []
        urls += [u.strip() for u in self.overpass_urls.split(",") if u.strip()]
        return list(dict.fromkeys(urls))

    @property
    def radii_km(self) -> list[int]:
        return [int(x) for x in self.allowed_radii_km.split(",") if x.strip()]

    def resolved_secret_key(self) -> str:
        """SECRET_KEY, or a random key generated once and kept in the data volume
        (so admin logins survive restarts without any manual setup)."""
        if self.secret_key:
            return self.secret_key
        path = self.data_path / "secret_key"
        if path.exists():
            return path.read_text().strip()
        key = secrets.token_urlsafe(48)
        path.write_text(key)
        try:
            path.chmod(0o600)
        except OSError:  # pragma: no cover - e.g. Windows bind mounts
            pass
        return key


@lru_cache
def get_settings() -> Settings:
    return Settings()
