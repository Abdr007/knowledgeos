"""Application settings.

Every knob the system has is declared here once, typed, and read from the
environment. Nothing else in the codebase reads ``os.environ`` — a setting that
can be introduced from anywhere is a setting nobody can enumerate, and the first
question in any production incident is "what is this instance actually
configured with".

Validation happens at import time, so a container with a bad configuration fails
immediately and visibly on boot rather than at the first request that happens to
touch the broken value.
"""

from __future__ import annotations

from enum import StrEnum
from functools import lru_cache
from typing import Annotated, Literal

from pydantic import AliasChoices, Field, PostgresDsn, RedisDsn, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict


class Environment(StrEnum):
    LOCAL = "local"
    STAGING = "staging"
    PRODUCTION = "production"
    TEST = "test"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    # ── application ──────────────────────────────────────────────────────
    app_name: str = "KnowledgeOS AI"
    environment: Environment = Environment.LOCAL
    debug: bool = False
    #: Interactive API docs. Off in production by default, because an OpenAPI
    #: schema enumerates every endpoint and parameter for an attacker.
    #:
    #: Overridable, and deliberately so: on a public portfolio deployment whose
    #: source is already on GitHub, the API shape is public regardless, so the
    #: disclosure argument does not apply and the docs are worth more open than
    #: closed. Decoupled from `environment` so enabling them does not also
    #: weaken cookie security or drop HSTS.
    docs_enabled: bool | None = None
    api_v1_prefix: str = "/api/v1"
    #: Origins allowed to call the API from a browser. Never "*" in production —
    #: this API is cookie- and token-authenticated and serves tenant data.
    #:
    #: NoDecode is required: pydantic-settings JSON-decodes complex types before
    #: field validators run, so a plain "a,b" from the environment would raise a
    #: JSONDecodeError and never reach the splitter below. NoDecode hands the raw
    #: string to the validator instead, which is what lets Compose and every
    #: scheduler pass a comma-separated list like every other tool expects.
    cors_origins: Annotated[list[str], NoDecode] = Field(
        default_factory=lambda: ["http://localhost:3000"]
    )

    # ── security ─────────────────────────────────────────────────────────
    #: Signing key for access and refresh tokens. No default: a framework that
    #: ships a working secret is a framework that ships that secret to
    #: production. Boot must fail without it.
    secret_key: str = Field(min_length=32)
    jwt_algorithm: Literal["HS256", "HS512"] = "HS256"
    access_token_ttl_minutes: int = 30
    #: Long enough that users are not logged out daily, short enough that a
    #: stolen refresh token has a bounded life. Rotation limits it further.
    refresh_token_ttl_days: int = 14
    password_min_length: int = 12

    # ── datastores ───────────────────────────────────────────────────────
    database_url: PostgresDsn = Field(
        validation_alias=AliasChoices("DATABASE_URL", "POSTGRES_DSN")
    )
    #: Bounded because Postgres connections are a finite server resource and an
    #: unbounded pool turns a traffic spike into a database outage.
    db_pool_size: int = 10
    db_max_overflow: int = 5
    db_echo: bool = False

    redis_url: RedisDsn = Field(default=RedisDsn("redis://localhost:6379/0"))

    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str | None = None
    qdrant_collection: str = "knowledgeos_chunks"

    # ── chat model ───────────────────────────────────────────────────────
    #: The blueprint specifies OpenAI. Anthropic is the configured default here
    #: because it is the credential this deployment holds; the call sites depend
    #: on the LLMProvider protocol, not on either vendor, so switching is one
    #: module rather than a rewrite.
    #: "scripted" is a deterministic, protocol-conformant provider used by the
    #: test suite and by the demo before a live key exists. It is what keeps
    #: Milestone 8 verifiable offline; going live is this one value (§0).
    llm_provider: Literal["anthropic", "openai", "scripted"] = "anthropic"
    anthropic_api_key: str | None = None
    openai_api_key: str | None = None
    chat_model: str = "claude-opus-5"
    llm_request_timeout_seconds: int = 120
    llm_max_retries: int = 3
    #: Cheaper model for mechanical work — conversation titles, query rewriting.
    #: Routing everything to the frontier model is the most common way an LLM
    #: bill ends up an order of magnitude larger than it needs to be.
    utility_model: str = "claude-haiku-4-5"

    # ── embeddings ───────────────────────────────────────────────────────
    #: ANTHROPIC PROVIDES NO EMBEDDINGS API. Claude generates text; it does not
    #: return vectors. A deployment holding only an Anthropic key therefore
    #: cannot embed through its chat provider at all — the retrieval half of RAG
    #: needs a separate answer, and this is it.
    #:
    #: Local inference via fastembed (ONNX, CPU): no second vendor, no key, no
    #: per-token cost, and no document text leaving the deployment — which for
    #: an enterprise knowledge platform is a feature and not a compromise.
    embedding_provider: Literal["local", "openai"] = "local"
    #: English-only, by decision. Multilingual is a config change, not a
    #: redesign: the 384-wide drop-in is
    #: sentence-transformers/paraphrase-multilingual-MiniLM-L12-v2, which needs
    #: no change to the Qdrant collection. Switching later costs a re-embed of
    #: the corpus and nothing else (TDD §29.3).
    embedding_model: str = "BAAI/bge-small-en-v1.5"
    #: Must match the model above. Wrong here builds the Qdrant collection at the
    #: wrong width, which fails loudly at insert rather than degrading silently.
    embedding_dimensions: int = 384

    # ── ingestion ────────────────────────────────────────────────────────
    max_upload_bytes: int = 50 * 1024 * 1024
    chunk_size_chars: int = 1200
    chunk_overlap_chars: int = 150
    embedding_batch_size: int = 64
    #: Retrieval breadth before fusion and reranking narrow it down.
    retrieval_candidates: int = 40
    retrieval_top_k: int = 8
    #: COSINE-SIMILARITY threshold below which the refusal gate fires (§10).
    #: Compared against the best raw dense score, never the fused RRF score —
    #: RRF measures rank agreement, not relevance, and an ANN index always
    #: returns its k nearest neighbours however far away they are.
    #: Measured on BAAI/bge-small-en-v1.5: on-topic 0.63-0.76, off-topic
    #: 0.49-0.52. 0.58 sits in the gap with margin on both sides.
    relevance_floor: float = 0.58

    # ── storage ──────────────────────────────────────────────────────────
    #: "postgres" shares objects between the API and the worker without a
    #: shared volume, which most container platforms cannot provide.
    storage_backend: Literal["local", "postgres", "s3"] = "local"
    storage_local_path: str = "./storage"
    s3_bucket: str | None = None
    s3_region: str | None = None
    s3_endpoint_url: str | None = None

    # ── limits ───────────────────────────────────────────────────────────
    rate_limit_chat_per_minute: int = 20
    rate_limit_upload_per_minute: int = 10
    rate_limit_auth_per_minute: int = 10

    # ── observability ────────────────────────────────────────────────────
    log_level: str = "INFO"
    log_json: bool = True

    @field_validator(
        "qdrant_api_key",
        "anthropic_api_key",
        "openai_api_key",
        "s3_bucket",
        "s3_region",
        "s3_endpoint_url",
        mode="before",
    )
    @classmethod
    def _empty_to_none(cls, v: object) -> object:
        """An unset variable in a .env file arrives as "", not as absent.

        Left alone, `QDRANT_API_KEY=` becomes the empty string, which is truthy
        enough for client libraries to start sending an Authorization header —
        Qdrant then warns about credentials on an insecure connection, and
        `llm_is_configured` would report a key that does not exist.
        """
        if isinstance(v, str) and not v.strip():
            return None
        return v

    @field_validator("cors_origins", mode="before")
    @classmethod
    def _split_origins(cls, v: object) -> object:
        # Compose and most schedulers can only pass strings, so accept
        # "a,b" as well as a real list.
        if isinstance(v, str):
            return [origin.strip() for origin in v.split(",") if origin.strip()]
        return v

    @property
    def is_production(self) -> bool:
        return self.environment is Environment.PRODUCTION

    @property
    def expose_docs(self) -> bool:
        return (not self.is_production) if self.docs_enabled is None else self.docs_enabled

    @property
    def is_test(self) -> bool:
        return self.environment is Environment.TEST

    @property
    def sqlalchemy_url(self) -> str:
        """Database URL with an explicit driver.

        Managed platforms hand out `postgres://` (Heroku's legacy form, still
        emitted by Railway and others) or a bare `postgresql://`. SQLAlchemy 2
        rejects the first outright and resolves the second to psycopg2, which is
        not installed — this project uses psycopg 3. Normalising here means the
        same image runs on any platform without the operator having to rewrite a
        connection string by hand.
        """
        url = str(self.database_url)
        for prefix in ("postgresql+psycopg://", "postgresql+psycopg2://", "postgresql+asyncpg://"):
            if url.startswith(prefix):
                return url
        if url.startswith("postgres://"):
            return "postgresql+psycopg://" + url[len("postgres://") :]
        if url.startswith("postgresql://"):
            return "postgresql+psycopg://" + url[len("postgresql://") :]
        return url

    @property
    def llm_api_key(self) -> str | None:
        """The key for the *selected* provider, or None if it needs none."""
        return {
            "anthropic": self.anthropic_api_key,
            "openai": self.openai_api_key,
            "scripted": "not-required",
        }[self.llm_provider]

    @property
    def llm_is_configured(self) -> bool:
        return bool(self.llm_api_key)


@lru_cache
def get_settings() -> Settings:
    """Cached accessor. Settings are immutable for the life of the process."""
    return Settings()
