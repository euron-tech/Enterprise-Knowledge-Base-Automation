"""Single settings object. Nothing else reads os.environ."""

from __future__ import annotations

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=(".env", "../.env"), env_file_encoding="utf-8", extra="ignore"
    )

    environment: Literal["dev", "test", "prod"] = "test"
    debug: bool = True
    log_level: str = "INFO"

    # API surface
    cors_origins: str = "http://localhost:5173"
    trusted_hosts: str = "localhost,127.0.0.1,testserver"
    max_body_bytes: int = 1_048_576
    max_upload_bytes: int = 52_428_800

    database_url: str = "sqlite+aiosqlite:///./ekba.db"
    redis_url: str = "redis://localhost:6379/0"
    cache_ttl_seconds: int = 86_400

    qdrant_url: str = ":memory:"
    qdrant_api_key: SecretStr = SecretStr("")
    qdrant_collection: str = "ekba_chunks_dev"

    # Euri gateway
    euri_base_url: str = "https://api.euron.one/api/v1/euri"
    euri_api_key: SecretStr = SecretStr("")
    euri_embedding_model: str = "gemini-embedding-2-preview"
    euri_embedding_dimensions: int = 1536
    euri_generation_model: str = "gpt-4.1"
    euri_planner_model: str = "gpt-4.1-mini"
    euri_fallback_model: str = "gpt-4.1-mini"
    euri_vision_model: str = "gpt-4.1"
    euri_transcribe_model: str = "whisper-large-v3-turbo"

    # Cognito
    cognito_region: str = "ap-south-1"
    cognito_user_pool_id: str = ""
    cognito_client_id: str = ""
    auth_dev_mode: bool = False  # locally signed tokens; tests only

    s3_bucket_documents: str = "ekba-dev-documents"
    s3_bucket_derived: str = "ekba-dev-derived"
    aws_region: str = "ap-south-1"

    # Agent budgets — enforced in code, never by a prompt
    agent_max_iterations: int = 8
    agent_max_tool_calls: int = 12
    agent_max_calls_per_tool: int = 4
    agent_max_context_chunks: int = 40
    agent_max_tokens: int = 60_000
    agent_wall_clock_seconds: int = 45

    retrieval_top_k: int = 8
    relevance_threshold: float = 0.35
    max_chunk_tokens: int = 1_500

    prompt_version: str = "v1"
    agent_version: str = "v1"

    rate_limits: dict[str, tuple[int, int]] = Field(
        default_factory=lambda: {
            "/chat": (20, 60),
            "/search": (40, 60),
            "/documents/upload": (5, 60),
            "default": (60, 60),
        }
    )

    @model_validator(mode="after")
    def _reject_local_defaults_outside_tests(self) -> Settings:
        """Fail closed. A deployed environment must never run on local scaffolding.

        Local affordances (SQLite, in-memory Qdrant, locally signed dev tokens) exist
        only for tests. If any of them reached dev or prod, the system would appear to
        work while storing nothing durable and accepting self-signed tokens.
        """
        if self.environment == "test":
            return self

        problems: list[str] = []
        if self.auth_dev_mode:
            problems.append(
                "AUTH_DEV_MODE=true accepts locally signed tokens — never outside tests"
            )
        if self.database_url.startswith("sqlite"):
            problems.append("DATABASE_URL is SQLite — set a PostgreSQL URL")
        if self.qdrant_url in (":memory:", ""):
            problems.append("QDRANT_URL is in-memory — set a real Qdrant endpoint")
        elif self.qdrant_url.startswith("https://") and not self.qdrant_api_key.get_secret_value():
            problems.append("QDRANT_API_KEY is required for a remote Qdrant endpoint")
        if not self.euri_api_key.get_secret_value():
            problems.append("EURI_API_KEY is empty")
        if not self.cognito_user_pool_id or not self.cognito_client_id:
            problems.append("COGNITO_USER_POOL_ID / COGNITO_CLIENT_ID are required")
        if self.environment == "prod":
            if self.debug:
                problems.append("DEBUG must be false in prod")
            if "localhost" in self.cors_origins:
                problems.append("CORS_ORIGINS contains localhost in prod")

        if problems:
            raise ValueError(
                f"invalid configuration for environment={self.environment}:\n  - "
                + "\n  - ".join(problems)
            )
        return self

    @property
    def is_prod(self) -> bool:
        return self.environment == "prod"

    @property
    def cors_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def trusted_host_list(self) -> list[str]:
        return [h.strip() for h in self.trusted_hosts.split(",") if h.strip()]


@lru_cache
def get_settings() -> Settings:
    return Settings()
